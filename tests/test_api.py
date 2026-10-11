from contextlib import asynccontextmanager
from dataclasses import replace

import httpx
import pytest

from app.config import Settings
from app.main import create_app
from tests.conftest import REDIS_URL

UNREACHABLE = "redis://127.0.0.1:1/0"


@asynccontextmanager
async def client_for(**overrides):
    settings = Settings(
        redis_url=REDIS_URL, bucket_capacity=3, bucket_refill_rate=0.01, window_limit=2, window_seconds=60
    )
    settings = replace(settings, **overrides)
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            yield client


async def test_check_allows_then_denies_with_retry_after(redis):
    async with client_for() as client:
        bodies = [(await client.post("/check", json={"client_id": "u1"})).json() for _ in range(4)]
    assert [b["allowed"] for b in bodies] == [True, True, True, False]
    assert bodies[0]["limit"] == 3 and bodies[0]["remaining"] == 2
    assert bodies[-1]["retry_after_ms"] > 0
    assert bodies[-1]["algorithm"] == "token_bucket" and bodies[-1]["client_id"] == "u1"


async def test_check_sliding_window(redis):
    async with client_for() as client:
        allowed = [
            (await client.post("/check", json={"client_id": "u2", "algorithm": "sliding_window"})).json()["allowed"]
            for _ in range(3)
        ]
    assert allowed == [True, True, False]


@pytest.mark.parametrize(
    "payload",
    [{"client_id": "u1", "algorithm": "fixed_window"}, {"client_id": ""}, {"client_id": "has space"}, {}],
)
async def test_check_validates_input(redis, payload):
    async with client_for() as client:
        assert (await client.post("/check", json=payload)).status_code == 422


async def test_status_and_reset(redis):
    async with client_for() as client:
        await client.post("/check", json={"client_id": "u3"})
        status = (await client.get("/status/u3")).json()
        assert status["remaining"] == 2
        assert (await client.get("/status/u3")).json()["remaining"] == 2  # status does not consume
        assert (await client.delete("/reset/u3")).status_code == 200
        assert (await client.get("/status/u3")).json()["remaining"] == 3
        assert (await client.get("/status/u3?algorithm=sliding_window")).json()["remaining"] == 2
        assert (await client.get("/status/u3?algorithm=nope")).status_code == 422


async def test_demo_endpoint_returns_429_with_headers(redis):
    async with client_for() as client:
        ok = await client.get("/demo", headers={"X-Client-Id": "demo-user"})
        assert ok.status_code == 200
        assert ok.headers["X-RateLimit-Limit"] == "3" and ok.headers["X-RateLimit-Remaining"] == "2"
        await client.get("/demo", headers={"X-Client-Id": "demo-user"})
        await client.get("/demo", headers={"X-Client-Id": "demo-user"})
        limited = await client.get("/demo", headers={"X-Client-Id": "demo-user"})
        assert limited.status_code == 429
        assert int(limited.headers["Retry-After"]) >= 1
        assert limited.headers["X-RateLimit-Remaining"] == "0"
        too_long = await client.get("/demo", headers={"X-Client-Id": "x" * 200})
        assert too_long.status_code == 400


async def test_health(redis):
    async with client_for() as client:
        assert (await client.get("/health")).json() == {"status": "ok", "redis": "connected"}


async def test_fails_closed_by_default_when_redis_is_down():
    async with client_for(redis_url=UNREACHABLE, redis_timeout_seconds=0.2) as client:
        assert (await client.post("/check", json={"client_id": "u4"})).status_code == 503
        assert (await client.get("/status/u4")).status_code == 503
        assert (await client.delete("/reset/u4")).status_code == 503
        assert (await client.get("/health")).status_code == 503


async def test_fail_open_lets_requests_through_marked_degraded():
    async with client_for(redis_url=UNREACHABLE, redis_timeout_seconds=0.2, fail_open=True) as client:
        body = (await client.post("/check", json={"client_id": "u5"})).json()
    assert body["allowed"] is True and body["degraded"] is True
