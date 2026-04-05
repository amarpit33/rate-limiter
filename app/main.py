from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel

from app.redis_client import get_redis
from app.limiter.token_bucket import TokenBucket
from app.limiter.sliding_window import SlidingWindow

app = FastAPI(
    title="Rate Limiter API",
    description="Token bucket and sliding window rate limiting over Redis.",
    version="1.0.0",
)

_redis = get_redis()
token_bucket = TokenBucket(_redis, capacity=10, refill_rate=1.0)
sliding_window = SlidingWindow(_redis, limit=100, window_seconds=60)


class CheckRequest(BaseModel):
    client_id: str
    algorithm: str = "token_bucket"


@app.post("/check")
def check(req: CheckRequest):
    """Check whether a request from client_id is allowed."""
    if req.algorithm == "token_bucket":
        result = token_bucket.allow(req.client_id)
    elif req.algorithm == "sliding_window":
        result = sliding_window.allow(req.client_id)
    else:
        raise HTTPException(status_code=400, detail="algorithm must be 'token_bucket' or 'sliding_window'")
    result.update({"algorithm": req.algorithm, "client_id": req.client_id})
    return result


@app.get("/status/{client_id}")
def status(client_id: str, algorithm: str = Query("token_bucket")):
    """Get current rate limit state for a client."""
    if algorithm == "token_bucket":
        result = token_bucket.status(client_id)
    elif algorithm == "sliding_window":
        result = sliding_window.status(client_id)
    else:
        raise HTTPException(status_code=400, detail="algorithm must be 'token_bucket' or 'sliding_window'")
    result.update({"algorithm": algorithm, "client_id": client_id})
    return result


@app.delete("/reset/{client_id}")
def reset(client_id: str, algorithm: str = Query("token_bucket")):
    """Reset rate limit state for a client (admin use)."""
    if algorithm == "token_bucket":
        token_bucket.reset(client_id)
    elif algorithm == "sliding_window":
        sliding_window.reset(client_id)
    else:
        raise HTTPException(status_code=400, detail="algorithm must be 'token_bucket' or 'sliding_window'")
    return {"message": f"Reset {algorithm} for {client_id}"}


@app.get("/health")
def health():
    """Liveness check — verifies Redis connectivity."""
    try:
        _redis.ping()
        return {"status": "ok", "redis": "connected"}
    except Exception:
        raise HTTPException(status_code=503, detail="Redis unavailable")
