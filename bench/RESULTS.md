# Benchmark results

Measured 11 Oct 2026 with `bench/run_local.sh`. Raw output for every run is in [`results/`](results/).

## Setup

| | |
|---|---|
| Machine | 2 vCPU (Intel Xeon @ 2.80 GHz), 8 GB RAM, Linux container |
| Topology | 3 API replicas (one uvicorn worker each) → one Redis 7.0 on the same machine |
| Load generator | `bench/loadgen.py` (aiohttp, closed loop, 64 in-flight requests), **on the same 2 vCPUs** |
| Traffic | `POST /check`, round-robin across replicas, 100 client ids chosen at random |
| Limits | Token bucket capacity 100, refill 10/s; sliding window 100 per 10 s |
| Duration | 3 s warm-up, then 30 s measured |
| Software | Python 3.13, FastAPI 0.143, uvicorn 0.54 (uvloop, httptools), redis-py 6.4 |

## End-to-end HTTP results

| Run | Replicas | Algorithm | Decisions/s | p50 | p95 | p99 | Allowed / denied | Errors | Over-admission |
|---|---|---|---|---|---|---|---|---|---|
| 1 | 3 | token bucket | 2,779 | 21.4 ms | 43.0 ms | 57.8 ms | 34,288 / 49,138 | 0 | none |
| 2 | 3 | token bucket | 2,938 | 20.2 ms | 40.1 ms | 53.8 ms | 34,550 / 53,591 | 0 | none |
| 3 | 3 | token bucket | 2,828 | 21.0 ms | 41.3 ms | 54.1 ms | 35,894 / 48,982 | 0 | none |
| — | 3 | sliding window | 3,030 | 20.0 ms | 38.0 ms | 49.8 ms | 27,733 / 63,184 | 0 | — |
| — | 1 | token bucket | 2,513 | 24.5 ms | 37.6 ms | 50.1 ms | 36,091 / 39,332 | 0 | none |

**Median for 3 replicas, token bucket: ~2,830 decisions/s.**

"Over-admission" is checked by the load generator: no client may be allowed more than
`capacity + refill_rate × elapsed` times. The busiest client was allowed 367–380 times against a
ceiling of 430 in every run. Exactness under contention (exactly `limit` allowed out of hundreds of
simultaneous requests) is proven separately by `tests/test_concurrency.py`.

## Redis-side cost of the Lua scripts

`redis-benchmark -n 300000 -c 50 -r 1000 EVALSHA <sha> 1 <key> ...`, three runs each:

| Script | Calls/s | p50 |
|---|---|---|
| Token bucket consume | 48,100 – 53,700 | 0.81 – 0.87 ms |
| Sliding window acquire | 48,900 – 53,100 | 0.82 – 0.84 ms |

## How to read this

- **The ceiling here is CPU, not Redis.** During the runs, the load generator used ~60% of a core,
  each replica ~40%, and Redis ~10%: the two vCPUs were saturated. Redis alone executes the scripts at
  ~50k calls/s on the same machine, about 17× the end-to-end rate.
- That is also why 3 replicas only beat 1 replica by ~13%: on two shared vCPUs there is no spare CPU
  for extra replicas to use. On separate hosts the HTTP layer scales out until Redis becomes the limit.
- Latency percentiles include queueing on a saturated machine with 64 requests in flight; they are not
  representative of an idle network hop.
- This is a local loopback benchmark. It is not a production measurement.

## Reproduce

```bash
pip install -r requirements-dev.txt          # needs redis-server on PATH
REPLICAS=3 CONCURRENCY=64 CLIENTS=100 DURATION=30 ./bench/run_local.sh
ALGORITHM=sliding_window ./bench/run_local.sh
```
