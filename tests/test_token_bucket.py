import asyncio

import pytest

from app.limiter.token_bucket import TokenBucket


async def test_new_client_gets_full_burst_then_is_denied(redis):
    bucket = TokenBucket(redis, capacity=5, refill_rate=0.01)
    results = [await bucket.allow("alice") for _ in range(6)]
    assert [r.allowed for r in results] == [True] * 5 + [False]
    assert [r.remaining for r in results[:5]] == [4, 3, 2, 1, 0]
    assert all(r.limit == 5 for r in results)


async def test_denied_decision_reports_time_to_next_token(redis):
    bucket = TokenBucket(redis, capacity=1, refill_rate=2.0)  # one token every 500 ms
    assert (await bucket.allow("bob")).allowed
    denied = await bucket.allow("bob")
    assert not denied.allowed
    assert 0 < denied.retry_after_ms <= 500


async def test_tokens_refill_over_time(redis):
    bucket = TokenBucket(redis, capacity=2, refill_rate=20.0)  # one token every 50 ms
    assert (await bucket.allow("carol")).allowed
    assert (await bucket.allow("carol")).allowed
    assert not (await bucket.allow("carol")).allowed
    await asyncio.sleep(0.12)
    assert (await bucket.allow("carol")).allowed


async def test_refill_never_exceeds_capacity(redis):
    bucket = TokenBucket(redis, capacity=3, refill_rate=100.0)
    await bucket.allow("dave")
    await asyncio.sleep(0.1)  # enough time to refill 10 tokens
    assert (await bucket.status("dave")).remaining == 3


async def test_status_is_read_only(redis):
    bucket = TokenBucket(redis, capacity=2, refill_rate=0.01)
    for _ in range(5):
        status = await bucket.status("erin")
    assert status.remaining == 2 and status.allowed
    assert not await redis.exists(bucket.key("erin"))
    await bucket.allow("erin")
    await bucket.allow("erin")
    drained = await bucket.status("erin")
    assert drained.remaining == 0 and not drained.allowed and drained.retry_after_ms > 0


async def test_key_expires_once_bucket_would_be_full(redis):
    bucket = TokenBucket(redis, capacity=10, refill_rate=5.0)  # 2 s to refill, +1 s slack
    await bucket.allow("frank")
    ttl = await redis.pttl(bucket.key("frank"))
    assert 2000 < ttl <= 3000


async def test_clients_are_isolated(redis):
    bucket = TokenBucket(redis, capacity=1, refill_rate=0.01)
    assert (await bucket.allow("a")).allowed
    assert not (await bucket.allow("a")).allowed
    assert (await bucket.allow("b")).allowed


async def test_reset_restores_full_bucket(redis):
    bucket = TokenBucket(redis, capacity=1, refill_rate=0.01)
    await bucket.allow("gina")
    await bucket.reset("gina")
    assert (await bucket.allow("gina")).allowed


@pytest.mark.parametrize("capacity, rate", [(0, 1.0), (5, 0.0), (5, -1.0)])
async def test_rejects_invalid_configuration(redis, capacity, rate):
    with pytest.raises(ValueError):
        TokenBucket(redis, capacity=capacity, refill_rate=rate)
