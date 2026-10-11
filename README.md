![CI](https://github.com/amarpit33/rate-limiter/actions/workflows/ci.yml/badge.svg)

# Distributed Rate Limiter — Python, FastAPI, Redis

Token bucket and sliding window rate limiting with all state in Redis, so any number of stateless API
instances enforce one shared limit. Each check is a single atomic Lua script.

**Built with:** Python · FastAPI · Redis (Lua) · asyncio · Docker · GitHub Actions

---

## Why it's correct under concurrency

A distributed limiter's one job is to never let more than the limit through when many instances are
checking the same client at once. The naive version (read the counter, decide in application code,
write it back) fails that: two instances read the same value and both allow the request. An earlier
version of this repo did exactly that, and let **197–200 of 200 simultaneous requests** through a
limit of 10.

Now each algorithm runs as one Lua script. Redis executes a script atomically, so the
read-decide-write cycle can't interleave with another request's.

`tests/test_concurrency.py` fires 400 simultaneous requests through 4 independent Redis clients (separate
connection pools, like separate API instances) and asserts that **exactly** `limit` are allowed, for both
algorithms and for several clients at once. CI runs it against a real Redis on every push.

## Algorithms

**Token bucket** (`app/limiter/token_bucket.py`). Allows bursts up to `capacity`, then `refill_rate`
requests per second. State is a hash `{tokens, ts}`; the script refills by elapsed time, spends a token
if one is available, and writes back. Good for APIs where short bursts are fine.

**Sliding window log** (`app/limiter/sliding_window.py`). At most `limit` requests in any rolling
`window_seconds`. Allowed requests go into a sorted set scored by timestamp; the script prunes expired
entries, counts, and adds the new request only if it fits. Exact, with no fixed-window boundary bursts.

## Design decisions

| Decision | Why |
|---|---|
| **Lua scripts (`EVALSHA`)** instead of `WATCH`/`MULTI` or app-side logic | Atomic with one round trip; no optimistic-lock retries under contention. Scripts are cached by SHA (`register_script`) |
| **Redis `TIME` as the clock** | API hosts' clocks drift. If each instance used its own clock, a skewed host could mint tokens or shift windows. One clock, one source of truth |
| **Only allowed requests enter the window** | Denied requests can't occupy slots, so a client hammering the API doesn't lock itself out for longer than the window |
| **Unique sorted-set members** (`uuid4`) | Two requests in the same millisecond are two entries, not one |
| **TTL on every key** | Bucket keys expire when a drained bucket would be full again; window keys expire with the window. Idle clients cost no memory |
| **One key per script call** | Compatible with Redis Cluster without hash tags |
| **Fail closed by default** (`RATE_LIMIT_FAIL_OPEN=true` to fail open) | If Redis is down, `/check` returns 503. Failing open keeps traffic flowing but drops protection; the response is marked `degraded` so callers can tell |
| **Short Redis timeouts** (500 ms default) | The limiter sits on the request path; a slow Redis should trigger the failure policy quickly, not stall every request |
| **Async FastAPI + `redis.asyncio`** | No thread-pool hop per request; one connection pool per instance |
| **Read-only status** | `GET /status` computes state without writing, so monitoring never spends a client's capacity |

**Trade-off:** the sliding window log stores one entry per allowed request, O(limit) memory per active
client. For very large limits, a sliding window *counter* (two fixed windows, weighted) gives O(1) memory
at the cost of being approximate.

## API

| Method | Path | Description |
|---|---|---|
| `POST` | `/check` | `{"client_id": "...", "algorithm": "token_bucket" \| "sliding_window"}`. Consumes one unit. Returns `allowed`, `limit`, `remaining`, `retry_after_ms` |
| `GET` | `/status/{client_id}?algorithm=` | Current state, read-only |
| `DELETE` | `/reset/{client_id}?algorithm=` | Clear a client's state (admin) |
| `GET` | `/demo` | Example endpoint protected by the limiter as a FastAPI dependency (keyed by `X-Client-Id`, else client IP). Returns `429` with `Retry-After` and `X-RateLimit-Limit` / `X-RateLimit-Remaining` |
| `GET` | `/health` | Liveness plus Redis connectivity |

`client_id`: 1–128 characters from `[A-Za-z0-9_.:@-]`. Invalid input returns 422.

```bash
curl -s -X POST localhost:8000/check -H 'content-type: application/json' -d '{"client_id":"user-42"}'
# {"allowed":true,"limit":10,"remaining":9,"retry_after_ms":0,"algorithm":"token_bucket","client_id":"user-42","degraded":false}
```

## Configuration

| Variable | Default | |
|---|---|---|
| `REDIS_URL` | `redis://localhost:6379/0` | Or `REDIS_HOST` / `REDIS_PORT` |
| `TOKEN_BUCKET_CAPACITY` / `TOKEN_BUCKET_REFILL_RATE` | 10 / 1.0 | Burst size; tokens per second |
| `SLIDING_WINDOW_LIMIT` / `SLIDING_WINDOW_SECONDS` | 100 / 60 | |
| `RATE_LIMIT_FAIL_OPEN` | `false` | Behaviour when Redis is unreachable |
| `REDIS_TIMEOUT_SECONDS` | 0.5 | Connect and command timeout |
| `RATE_LIMIT_KEY_PREFIX` | `rl` | Namespace for keys |

## Run locally

```bash
docker compose up --build        # API on http://localhost:8000, docs at /docs
```

Or without Docker: `pip install -r requirements.txt`, start `redis-server`, then
`uvicorn app.main:app --port 8000`.

## Tests

```bash
pip install -r requirements-dev.txt
redis-server --daemonize yes
pytest --cov=app                 # uses redis://localhost:6379/15; override with REDIS_URL
```

36 tests, all against a real Redis: the concurrency tests above, plus refill, window expiry,
retry-after, TTLs, read-only status, input validation, 429 headers, and fail-open/fail-closed behaviour.
CI runs ruff and the suite on Python 3.11 and 3.12 with a Redis 7 service container and a 90% coverage
gate (currently 99%).

## Benchmark

Three replicas sharing one Redis, `POST /check` load on a **2-vCPU machine**, with the load generator on
the same two vCPUs:

| | Decisions/s | p99 | Errors | Over-admission |
|---|---|---|---|---|
| Token bucket, 3 replicas (median of 3 × 30 s) | ~2,830 | ~54 ms | 0 | none |
| Sliding window, 3 replicas | ~3,030 | ~50 ms | 0 | none |
| Redis alone executing the Lua scripts | ~48,000–54,000 | <1 ms p50 | — | — |

The ceiling is the shared CPU (Python HTTP handling plus the load generator), not Redis, which used
~10% of a core. Full setup, raw results, and how to reproduce: [bench/RESULTS.md](bench/RESULTS.md).

## Limitations

- **One Redis is a single point of failure.** Use Sentinel or Cluster for HA. The scripts touch one key
  each, so they're Cluster-compatible, but this repo hasn't been tested against a cluster.
- The benchmark is a local loopback test on two vCPUs, not a production measurement.
- No per-route or per-plan limits; one configuration per algorithm per process.
- The Docker image and compose file are provided for convenience; the tests and benchmark run without
  Docker.

## Project structure

```
app/
  main.py               FastAPI app factory, routes, 429 dependency, fail-open/closed policy
  config.py             settings from environment variables
  redis_client.py       async Redis client with timeouts
  limiter/
    token_bucket.py     Lua token bucket
    sliding_window.py   Lua sliding window log
    base.py             Decision type
tests/                  integration + concurrency tests (real Redis)
bench/                  load generator, local multi-replica runner, results
```
