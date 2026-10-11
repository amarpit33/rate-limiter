"""Sliding window log rate limiter backed by Redis.

Each allowed request is stored in a sorted set scored by its timestamp. One Lua
script prunes entries older than the window, counts what is left and adds the
new request only if it fits, all atomically. Denied requests are never written,
so they cannot occupy a slot, and every member is unique so two requests in the
same millisecond are counted separately.

Memory is O(limit) per active client; for very large limits a sliding window
*counter* (two fixed buckets, weighted) trades exactness for O(1) memory.
"""
from __future__ import annotations

import math
import uuid

from redis.asyncio import Redis

from .base import Decision

# KEYS[1] = sorted-set key
# ARGV[1] = limit, ARGV[2] = window (ms), ARGV[3] = unique member for this request
# Returns {allowed (0/1), requests in window after this call, retry_after_ms}
_ACQUIRE = """
local limit = tonumber(ARGV[1])
local window = tonumber(ARGV[2])
local t = redis.call('TIME')
local now = tonumber(t[1]) * 1000 + math.floor(tonumber(t[2]) / 1000)
redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', now - window)
local count = redis.call('ZCARD', KEYS[1])
if count < limit then
  redis.call('ZADD', KEYS[1], now, ARGV[3])
  redis.call('PEXPIRE', KEYS[1], window)
  return {1, count + 1, 0}
end
local oldest = redis.call('ZRANGE', KEYS[1], 0, 0, 'WITHSCORES')
return {0, count, math.max(1, tonumber(oldest[2]) + window - now)}
"""

# Read-only: count requests inside the window without pruning or adding.
_PEEK = """
local limit = tonumber(ARGV[1])
local window = tonumber(ARGV[2])
local t = redis.call('TIME')
local now = tonumber(t[1]) * 1000 + math.floor(tonumber(t[2]) / 1000)
local count = redis.call('ZCOUNT', KEYS[1], '(' .. (now - window), '+inf')
if count < limit then
  return {count, 0}
end
local first = redis.call('ZRANGEBYSCORE', KEYS[1], '(' .. (now - window), '+inf', 'WITHSCORES', 'LIMIT', 0, 1)
return {count, math.max(1, tonumber(first[2]) + window - now)}
"""


class SlidingWindow:
    """At most ``limit`` requests in any rolling ``window_seconds`` interval."""

    def __init__(self, redis: Redis, limit: int = 100, window_seconds: float = 60.0, prefix: str = "rl") -> None:
        if limit < 1:
            raise ValueError("limit must be >= 1")
        if window_seconds <= 0:
            raise ValueError("window_seconds must be > 0")
        self.limit = limit
        self.window_seconds = window_seconds
        self._window_ms = max(1, math.ceil(window_seconds * 1000))
        self._redis = redis
        self._prefix = f"{prefix}:sw:"
        self._acquire = redis.register_script(_ACQUIRE)
        self._peek = redis.register_script(_PEEK)

    def key(self, client_id: str) -> str:
        return self._prefix + client_id

    async def allow(self, client_id: str) -> Decision:
        allowed, count, retry_after = await self._acquire(
            keys=[self.key(client_id)], args=[self.limit, self._window_ms, uuid.uuid4().hex]
        )
        return Decision(
            allowed=bool(allowed),
            limit=self.limit,
            remaining=max(0, self.limit - int(count)),
            retry_after_ms=int(retry_after),
        )

    async def status(self, client_id: str) -> Decision:
        count, retry_after = await self._peek(keys=[self.key(client_id)], args=[self.limit, self._window_ms])
        return Decision(
            allowed=int(count) < self.limit,
            limit=self.limit,
            remaining=max(0, self.limit - int(count)),
            retry_after_ms=int(retry_after),
        )

    async def reset(self, client_id: str) -> None:
        await self._redis.delete(self.key(client_id))
