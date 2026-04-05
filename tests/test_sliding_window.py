from unittest.mock import MagicMock

from app.limiter.sliding_window import SlidingWindow


def _mock_redis(count: int = 0):
    mock = MagicMock()
    pipe = MagicMock()
    pipe.execute.return_value = [None, count, None, None]
    mock.pipeline.return_value = pipe
    return mock


def test_allows_within_limit():
    window = SlidingWindow(_mock_redis(count=5), limit=100, window_seconds=60)
    assert window.allow("user1")["allowed"] is True


def test_denies_at_limit():
    window = SlidingWindow(_mock_redis(count=100), limit=100, window_seconds=60)
    assert window.allow("user1")["allowed"] is False


def test_remaining_decrements():
    window = SlidingWindow(_mock_redis(count=10), limit=50, window_seconds=60)
    result = window.allow("user1")
    assert result["remaining"] == 39


def test_window_metadata_present():
    window = SlidingWindow(_mock_redis(count=0), limit=50, window_seconds=30)
    result = window.allow("user1")
    assert result["limit"] == 50
    assert result["window_seconds"] == 30
