"""Tests run against a real Redis (default redis://localhost:6379/15).

Set REDIS_URL to point elsewhere. Without Redis the suite is skipped, unless
REQUIRE_REDIS=1 (as in CI), in which case a missing Redis is a failure.
"""
from __future__ import annotations

import os
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from redis.asyncio import Redis
from redis.exceptions import RedisError

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/15")


async def _connect(url: str = REDIS_URL) -> Redis:
    client = Redis.from_url(url, decode_responses=True)
    try:
        await client.ping()
    except (RedisError, OSError) as exc:
        await client.aclose()
        if os.getenv("REQUIRE_REDIS"):
            raise
        pytest.skip(f"Redis not reachable at {url}: {exc}")
    return client


@pytest_asyncio.fixture
async def redis() -> AsyncIterator[Redis]:
    client = await _connect()
    await client.flushdb()
    yield client
    await client.flushdb()
    await client.aclose()


@pytest_asyncio.fixture
async def redis_factory(redis: Redis) -> AsyncIterator:
    """Independent clients (own connection pools), standing in for separate API instances."""
    clients: list[Redis] = []

    async def make() -> Redis:
        c = await _connect()
        clients.append(c)
        return c

    yield make
    for c in clients:
        await c.aclose()
