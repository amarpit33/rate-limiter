"""Token bucket rate limiter backed by Redis.

The whole read-refill-consume-write cycle runs inside one Lua script, so Redis
executes it atomically: concurrent requests from any number of API instances
cannot read the same token count and both spend it. Time comes from Redis
(``TIME``), not from the API hosts, so clock skew between instances cannot
mint extra tokens.

State per client is a hash ``{tokens, ts}`` with a TTL equal to the time a
drained bucket needs to refill completely, so idle clients cost no memory.
"""
from __future__ import annotations

import math

from redis.asyncio import Redis

from .base import Decision

# KEYS[1] = bucket key
# ARGV[1] = capacity, ARGV[2] = refill rate (tokens/second), ARGV[3] = key TTL (ms)
# Returns {allowed (0/1), tokens remaining as string, retry_after_ms}
_CONSUME = """
local capacity = tonumber(ARGV[1])
local rate = tonumber(ARGV[2])
local ttl_ms = tonumber(ARGV[3])
local t = redis.call('TIME')
local now = tonumber(t[1]) * 1000 + math.floor(tonumber(t[2]) / 1000)
local state = redis.call('HMGET', KEYS[1], 'tokens', 'ts')
local tokens = tonumber(state[1])
local ts = tonumber(state[2])
if tokens == nil or ts == nil then
  tokens = capacity
  ts = now
end
tokens = math.min(capacity, tokens + math.max(0, now - ts) * rate / 1000)
local allowed = 0
local retry_after = 0
if tokens >= 1 then
  tokens = tokens - 1
  allowed = 1
else
  retry_after = math.ceil((1 - tokens) * 1000 / rate)
end
redis.call('HSET', KEYS[1], 'tokens', tostring(tokens), 'ts', now)
redis.call('PEXPIRE', KEYS[1], ttl_ms)
return {allowed, tostring(tokens), retry_after}
"""

# Read-only view of the bucket (no writes, so status checks never consume tokens).
_PEEK = """
local capacity = tonumber(ARGV[1])
local rate = tonumber(ARGV[2])
local t = redis.call('TIME')
local now = tonumber(t[1]) * 1000 + math.floor(tonumber(t[2]) / 1000)
local state = redis.call('HMGET', KEYS[1], 'tokens', 'ts')
local tokens = tonumber(state[1])
local ts = tonumber(state[2])
if tokens == nil or ts == nil then
  return tostring(capacity)
end
return tostring(math.min(capacity, tokens + math.max(0, now - ts) * rate / 1000))
"""


class TokenBucket:
    """Allows bursts up to ``capacity``, then ``refill_rate`` requests per second."""

    def __init__(self, redis: Redis, capacity: int = 10, refill_rate: float = 1.0, prefix: str = "rl") -> None:
        if capacity < 1:
            raise ValueError("capacity must be >= 1")
        if refill_rate <= 0:
            raise ValueError("refill_rate must be > 0")
        self.capacity = capacity
        self.refill_rate = refill_rate
        self._redis = redis
        self._prefix = f"{prefix}:tb:"
        # Time for an empty bucket to refill completely, plus a second of slack.
        self._ttl_ms = math.ceil(capacity / refill_rate * 1000) + 1000
        self._consume = redis.register_script(_CONSUME)
        self._peek = redis.register_script(_PEEK)

    def key(self, client_id: str) -> str:
        return self._prefix + client_id

    async def allow(self, client_id: str) -> Decision:
        allowed, tokens, retry_after = await self._consume(
            keys=[self.key(client_id)], args=[self.capacity, self.refill_rate, self._ttl_ms]
        )
        return Decision(
            allowed=bool(allowed),
            limit=self.capacity,
            remaining=int(float(tokens)),
            retry_after_ms=int(retry_after),
        )

    async def status(self, client_id: str) -> Decision:
        tokens = float(await self._peek(keys=[self.key(client_id)], args=[self.capacity, self.refill_rate]))
        retry_after = 0 if tokens >= 1 else math.ceil((1 - tokens) * 1000 / self.refill_rate)
        return Decision(allowed=tokens >= 1, limit=self.capacity, remaining=int(tokens), retry_after_ms=retry_after)

    async def reset(self, client_id: str) -> None:
        await self._redis.delete(self.key(client_id))
