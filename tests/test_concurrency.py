"""The property that matters for a distributed limiter: under concurrent load from
several API instances, exactly `limit` requests get through, never more.

Each simulated instance has its own Redis client and connection pool, so requests
really do interleave at the Redis server. The earlier read-modify-write version of
the token bucket let 197-200 of 200 concurrent requests through a limit of 10.
"""
import asyncio

import pytest

from app.limiter.sliding_window import SlidingWindow
from app.limiter.token_bucket import TokenBucket

INSTANCES = 4
REQUESTS = 400
LIMIT = 10


def _limiter(kind, client):
    if kind == "token_bucket":
        return TokenBucket(client, capacity=LIMIT, refill_rate=0.001)
    return SlidingWindow(client, limit=LIMIT, window_seconds=60)


@pytest.mark.parametrize("kind", ["token_bucket", "sliding_window"])
async def test_exactly_limit_allowed_across_instances(redis_factory, kind):
    limiters = [_limiter(kind, await redis_factory()) for _ in range(INSTANCES)]
    decisions = await asyncio.gather(
        *(limiters[i % INSTANCES].allow("shared-client") for i in range(REQUESTS))
    )
    assert sum(d.allowed for d in decisions) == LIMIT
    assert all(d.retry_after_ms > 0 for d in decisions if not d.allowed)


@pytest.mark.parametrize("kind", ["token_bucket", "sliding_window"])
async def test_limits_hold_per_client_under_mixed_load(redis_factory, kind):
    limiters = [_limiter(kind, await redis_factory()) for _ in range(INSTANCES)]
    clients = [f"client-{n}" for n in range(8)]
    calls = [(limiters[i % INSTANCES], clients[i % len(clients)]) for i in range(REQUESTS)]
    decisions = await asyncio.gather(*(lim.allow(cid) for lim, cid in calls))
    allowed_per_client: dict[str, int] = {}
    for (_, cid), decision in zip(calls, decisions, strict=True):
        allowed_per_client[cid] = allowed_per_client.get(cid, 0) + decision.allowed
    assert allowed_per_client == {cid: LIMIT for cid in clients}
