import asyncio

import pytest

from app.limiter.sliding_window import SlidingWindow


async def test_allows_up_to_limit_then_denies(redis):
    window = SlidingWindow(redis, limit=5, window_seconds=60)
    results = [await window.allow("alice") for _ in range(6)]
    assert [r.allowed for r in results] == [True] * 5 + [False]
    assert [r.remaining for r in results] == [4, 3, 2, 1, 0, 0]


async def test_denied_requests_do_not_take_a_slot(redis):
    window = SlidingWindow(redis, limit=3, window_seconds=60)
    for _ in range(10):
        await window.allow("bob")
    assert await redis.zcard(window.key("bob")) == 3


async def test_requests_in_same_millisecond_are_counted_separately(redis):
    window = SlidingWindow(redis, limit=50, window_seconds=60)
    results = await asyncio.gather(*(window.allow("carol") for _ in range(20)))
    assert all(r.allowed for r in results)
    assert await redis.zcard(window.key("carol")) == 20


async def test_capacity_returns_after_window_passes(redis):
    window = SlidingWindow(redis, limit=2, window_seconds=0.3)
    assert (await window.allow("dave")).allowed
    assert (await window.allow("dave")).allowed
    denied = await window.allow("dave")
    assert not denied.allowed
    assert 0 < denied.retry_after_ms <= 300
    await asyncio.sleep(0.35)
    assert (await window.allow("dave")).allowed


async def test_status_is_read_only_and_reports_retry_after(redis):
    window = SlidingWindow(redis, limit=2, window_seconds=60)
    assert (await window.status("erin")).remaining == 2
    await window.allow("erin")
    await window.allow("erin")
    for _ in range(3):
        status = await window.status("erin")
    assert status.remaining == 0 and not status.allowed and 0 < status.retry_after_ms <= 60_000
    assert await redis.zcard(window.key("erin")) == 2


async def test_key_expires_with_window(redis):
    window = SlidingWindow(redis, limit=5, window_seconds=2)
    await window.allow("frank")
    assert 0 < await redis.pttl(window.key("frank")) <= 2000


async def test_reset_clears_window(redis):
    window = SlidingWindow(redis, limit=1, window_seconds=60)
    await window.allow("gina")
    await window.reset("gina")
    assert (await window.allow("gina")).allowed


@pytest.mark.parametrize("limit, seconds", [(0, 60), (5, 0), (5, -1)])
async def test_rejects_invalid_configuration(redis, limit, seconds):
    with pytest.raises(ValueError):
        SlidingWindow(redis, limit=limit, window_seconds=seconds)
