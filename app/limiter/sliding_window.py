import time
from redis import Redis


class SlidingWindow:
    """
    Sliding window rate limiter.

    Tracks request timestamps in a Redis sorted set.
    On each request, entries older than `window_seconds` are pruned,
    then the remaining count is checked against `limit`.

    More precise than fixed windows — avoids the boundary burst problem
    where a client can double-fire at the edge of two fixed windows.
    """

    def __init__(self, redis: Redis, limit: int = 100, window_seconds: int = 60):
        self.redis = redis
        self.limit = limit
        self.window_seconds = window_seconds

    def _key(self, client_id: str) -> str:
        return f"sw:{client_id}"

    def allow(self, client_id: str) -> dict:
        key = self._key(client_id)
        now = time.time()
        window_start = now - self.window_seconds

        pipe = self.redis.pipeline()
        pipe.zremrangebyscore(key, "-inf", window_start)
        pipe.zcard(key)
        pipe.zadd(key, {str(now): now})
        pipe.expire(key, self.window_seconds * 2)
        results = pipe.execute()

        count_before = results[1]
        allowed = count_before < self.limit

        if not allowed:
            self.redis.zrem(key, str(now))

        return {
            "allowed": allowed,
            "requests_in_window": count_before,
            "limit": self.limit,
            "window_seconds": self.window_seconds,
            "remaining": max(0, self.limit - count_before - (1 if allowed else 0)),
        }

    def status(self, client_id: str) -> dict:
        key = self._key(client_id)
        now = time.time()
        window_start = now - self.window_seconds

        self.redis.zremrangebyscore(key, "-inf", window_start)
        count = self.redis.zcard(key)

        return {
            "requests_in_window": count,
            "limit": self.limit,
            "window_seconds": self.window_seconds,
            "remaining": max(0, self.limit - count),
        }

    def reset(self, client_id: str) -> None:
        self.redis.delete(self._key(client_id))
