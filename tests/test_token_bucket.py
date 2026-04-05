import time
from unittest.mock import MagicMock

import pytest

from app.limiter.token_bucket import TokenBucket


def _mock_redis(tokens: float = 10.0):
    mock = MagicMock()
    data = {"tokens": str(tokens), "last_refill": str(time.time())}
    pipe = MagicMock()
    pipe.execute.return_value = [data]
    mock.pipeline.return_value = pipe
    return mock


def _mock_redis_new_client():
    mock = MagicMock()
    pipe = MagicMock()
    pipe.execute.return_value = [{}]
    mock.pipeline.return_value = pipe
    return mock


def test_allows_when_tokens_available():
    bucket = TokenBucket(_mock_redis(tokens=5.0), capacity=10, refill_rate=1.0)
    assert bucket.allow("user1")["allowed"] is True


def test_denies_when_no_tokens():
    bucket = TokenBucket(_mock_redis(tokens=0.0), capacity=10, refill_rate=1.0)
    assert bucket.allow("user1")["allowed"] is False


def test_new_client_starts_with_full_bucket():
    bucket = TokenBucket(_mock_redis_new_client(), capacity=10, refill_rate=1.0)
    result = bucket.allow("new_user")
    assert result["allowed"] is True
    assert result["tokens_remaining"] == 9


def test_tokens_remaining_decrements():
    bucket = TokenBucket(_mock_redis(tokens=3.0), capacity=10, refill_rate=1.0)
    result = bucket.allow("user1")
    assert result["tokens_remaining"] == 2
