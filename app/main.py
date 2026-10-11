"""FastAPI service exposing the limiters as a decision API and as a reusable dependency."""

import logging
import math
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated, Literal

from fastapi import Depends, FastAPI, HTTPException, Path, Query, Request, Response
from pydantic import BaseModel, Field
from redis.exceptions import RedisError

from app.config import Settings
from app.limiter.base import Decision
from app.limiter.sliding_window import SlidingWindow
from app.limiter.token_bucket import TokenBucket
from app.redis_client import create_redis

log = logging.getLogger("rate_limiter")

Algorithm = Literal["token_bucket", "sliding_window"]
CLIENT_ID_PATTERN = r"^[A-Za-z0-9_.:@-]{1,128}$"
ClientId = Annotated[str, Path(pattern=CLIENT_ID_PATTERN)]
AlgorithmQuery = Annotated[Algorithm, Query()]


class CheckRequest(BaseModel):
    client_id: str = Field(pattern=CLIENT_ID_PATTERN)
    algorithm: Algorithm = "token_bucket"


class DecisionOut(BaseModel):
    allowed: bool
    limit: int
    remaining: int
    retry_after_ms: int
    algorithm: Algorithm
    client_id: str
    degraded: bool = False  # True when Redis was unreachable and the fail-open policy let it through


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        redis = create_redis(settings)
        app.state.redis = redis
        app.state.limiters = {
            "token_bucket": TokenBucket(
                redis, settings.bucket_capacity, settings.bucket_refill_rate, settings.key_prefix
            ),
            "sliding_window": SlidingWindow(
                redis, settings.window_limit, settings.window_seconds, settings.key_prefix
            ),
        }
        try:
            yield
        finally:
            await redis.aclose()

    app = FastAPI(
        title="Rate Limiter API",
        description="Token bucket and sliding window rate limiting with atomic Redis Lua scripts.",
        version="2.0.0",
        lifespan=lifespan,
    )

    def limiter_for(request: Request, algorithm: str) -> TokenBucket | SlidingWindow:
        return request.app.state.limiters[algorithm]

    async def decide(request: Request, client_id: str, algorithm: str) -> DecisionOut:
        limiter = limiter_for(request, algorithm)
        try:
            decision = await limiter.allow(client_id)
        except RedisError as exc:
            if not settings.fail_open:
                log.error("redis unavailable, failing closed: %s", exc)
                raise HTTPException(status_code=503, detail="rate limiter backend unavailable") from exc
            log.warning("redis unavailable, failing open: %s", exc)
            decision = Decision(allowed=True, limit=0, remaining=0, retry_after_ms=0)
            return DecisionOut(**decision.to_dict(), algorithm=algorithm, client_id=client_id, degraded=True)
        return DecisionOut(**decision.to_dict(), algorithm=algorithm, client_id=client_id)

    @app.post("/check", response_model=DecisionOut)
    async def check(req: CheckRequest, request: Request) -> DecisionOut:
        """Consume one unit for ``client_id`` and say whether the request is allowed."""
        return await decide(request, req.client_id, req.algorithm)

    @app.get("/status/{client_id}", response_model=DecisionOut)
    async def status(client_id: ClientId, request: Request, algorithm: AlgorithmQuery = "token_bucket"):
        """Current state for a client. Read-only: never consumes capacity."""
        try:
            decision = await limiter_for(request, algorithm).status(client_id)
        except RedisError as exc:
            raise HTTPException(status_code=503, detail="rate limiter backend unavailable") from exc
        return DecisionOut(**decision.to_dict(), algorithm=algorithm, client_id=client_id)

    @app.delete("/reset/{client_id}")
    async def reset(client_id: ClientId, request: Request, algorithm: AlgorithmQuery = "token_bucket"):
        """Clear a client's state (admin use)."""
        try:
            await limiter_for(request, algorithm).reset(client_id)
        except RedisError as exc:
            raise HTTPException(status_code=503, detail="rate limiter backend unavailable") from exc
        return {"message": f"Reset {algorithm} for {client_id}"}

    async def rate_limited(request: Request, response: Response) -> DecisionOut:
        """Dependency: limit by ``X-Client-Id`` header, falling back to the caller's IP."""
        client_id = request.headers.get("x-client-id") or (request.client.host if request.client else "anonymous")
        if len(client_id) > 128:
            raise HTTPException(status_code=400, detail="X-Client-Id too long")
        decision = await decide(request, client_id, "token_bucket")
        headers = {"X-RateLimit-Limit": str(decision.limit), "X-RateLimit-Remaining": str(decision.remaining)}
        if not decision.allowed:
            headers["Retry-After"] = str(max(1, math.ceil(decision.retry_after_ms / 1000)))
            raise HTTPException(status_code=429, detail="rate limit exceeded", headers=headers)
        response.headers.update(headers)
        return decision

    @app.get("/demo")
    async def demo(decision: Annotated[DecisionOut, Depends(rate_limited)]):
        """Example endpoint protected by the limiter: returns 429 with Retry-After when exhausted."""
        return {"message": "ok", "remaining": decision.remaining}

    @app.get("/health")
    async def health(request: Request):
        """Liveness plus Redis connectivity."""
        try:
            await request.app.state.redis.ping()
        except RedisError as exc:
            raise HTTPException(status_code=503, detail="Redis unavailable") from exc
        return {"status": "ok", "redis": "connected"}

    return app


app = create_app()
