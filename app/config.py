"""Runtime configuration, read once from environment variables."""
from __future__ import annotations

import os
from dataclasses import dataclass


def _bool(value: str | None, default: bool) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    redis_url: str = "redis://localhost:6379/0"
    key_prefix: str = "rl"
    # Token bucket: burst up to `capacity`, refilled continuously at `refill_rate` tokens/second.
    bucket_capacity: int = 10
    bucket_refill_rate: float = 1.0
    # Sliding window log: at most `window_limit` requests in any rolling `window_seconds`.
    window_limit: int = 100
    window_seconds: float = 60.0
    # Behaviour when Redis is unreachable: False rejects with 503 (fail closed),
    # True lets the request through and marks the decision as degraded (fail open).
    fail_open: bool = False
    redis_timeout_seconds: float = 0.5

    @classmethod
    def from_env(cls) -> Settings:
        env = os.environ
        return cls(
            redis_url=env.get("REDIS_URL")
            or f"redis://{env.get('REDIS_HOST', 'localhost')}:{env.get('REDIS_PORT', '6379')}/0",
            key_prefix=env.get("RATE_LIMIT_KEY_PREFIX", cls.key_prefix),
            bucket_capacity=int(env.get("TOKEN_BUCKET_CAPACITY", cls.bucket_capacity)),
            bucket_refill_rate=float(env.get("TOKEN_BUCKET_REFILL_RATE", cls.bucket_refill_rate)),
            window_limit=int(env.get("SLIDING_WINDOW_LIMIT", cls.window_limit)),
            window_seconds=float(env.get("SLIDING_WINDOW_SECONDS", cls.window_seconds)),
            fail_open=_bool(env.get("RATE_LIMIT_FAIL_OPEN"), cls.fail_open),
            redis_timeout_seconds=float(env.get("REDIS_TIMEOUT_SECONDS", cls.redis_timeout_seconds)),
        )
