import time
from redis import Redis


class TokenBucket:
    """
    Token bucket rate limiter.

    Tokens accumulate at `refill_rate` per second up to `capacity`.
    Each allowed request consumes one token.
    State is stored in Redis so it works across multiple API instances.
    """

    def __init__(self, redis: Redis, capacity: int = 10, refill_rate: float = 1.0):
        self.redis = redis
        self.capacity = capacity
        self.refill_rate = refill_rate

    def _key(self, client_id: str) -> str:
        return f"tb:{client_id}"

    def allow(self, client_id: str) -> dict:
        key = self._key(client_id)
        now = time.time()

        pipe = self.redis.pipeline()
        pipe.hgetall(key)
        (data,) = pipe.execute()

        if data:
            tokens = float(data["tokens"])
            last_refill = float(data["last_refill"])
            elapsed = now - last_refill
            tokens = min(self.capacity, tokens + elapsed * self.refill_rate)
        else:
            tokens = float(self.capacity)

        allowed = tokens >= 1.0
        if allowed:
            tokens -= 1.0

        self.redis.hset(key, mapping={"tokens": tokens, "last_refill": now})
        self.redis.expire(key, 3600)

        return {
            "allowed": allowed,
            "tokens_remaining": max(0, int(tokens)),
            "capacity": self.capacity,
        }

    def status(self, client_id: str) -> dict:
        key = self._key(client_id)
        data = self.redis.hgetall(key)
        if not data:
            return {"tokens_remaining": self.capacity, "capacity": self.capacity}

        now = time.time()
        tokens = float(data["tokens"])
        last_refill = float(data["last_refill"])
        elapsed = now - last_refill
        tokens = min(self.capacity, tokens + elapsed * self.refill_rate)

        return {
            "tokens_remaining": max(0, int(tokens)),
            "capacity": self.capacity,
        }

    def reset(self, client_id: str) -> None:
        self.redis.delete(self._key(client_id))
