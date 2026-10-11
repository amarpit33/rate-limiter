from redis.asyncio import Redis

from app.config import Settings


def create_redis(settings: Settings) -> Redis:
    """Async client with a shared connection pool and short timeouts.

    Short timeouts matter for a rate limiter: it sits on the request path, so a
    slow Redis should fail fast and fall back to the configured fail-open or
    fail-closed policy instead of stalling every request.
    """
    return Redis.from_url(
        settings.redis_url,
        decode_responses=True,
        socket_timeout=settings.redis_timeout_seconds,
        socket_connect_timeout=settings.redis_timeout_seconds,
        health_check_interval=30,
    )
