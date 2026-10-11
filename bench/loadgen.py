"""Closed-loop HTTP load generator for POST /check.

Spreads requests round-robin across one or more API replicas, drawing client ids
from a fixed pool, and reports throughput, latency percentiles and the
allowed/denied split. It also checks the limiter never over-admits: for the token
bucket, a client can be allowed at most `capacity + refill_rate * elapsed` times.

    python bench/loadgen.py --urls http://127.0.0.1:8001,http://127.0.0.1:8002 \
        --duration 30 --concurrency 64 --clients 1000
"""
import argparse
import asyncio
import json
import random
import statistics
import time
from collections import Counter

import aiohttp


def percentile(sorted_values: list[float], p: float) -> float:
    if not sorted_values:
        return 0.0
    k = min(len(sorted_values) - 1, max(0, round(p / 100 * (len(sorted_values) - 1))))
    return sorted_values[k]


async def worker(session, urls, offset, deadline, args, latencies, outcomes, allowed_by_client):
    i = offset
    while time.perf_counter() < deadline:
        url = urls[i % len(urls)]
        i += 1
        client_id = f"client-{random.randrange(args.clients)}"
        start = time.perf_counter()
        try:
            async with session.post(
                f"{url}/check", json={"client_id": client_id, "algorithm": args.algorithm}
            ) as resp:
                body = await resp.json()
                elapsed = time.perf_counter() - start
                if resp.status != 200:
                    outcomes[f"http_{resp.status}"] += 1
                    continue
        except (aiohttp.ClientError, TimeoutError):
            outcomes["error"] += 1
            continue
        latencies.append(elapsed)
        if body["allowed"]:
            outcomes["allowed"] += 1
            allowed_by_client[client_id] += 1
        else:
            outcomes["denied"] += 1


async def run(args) -> dict:
    urls = [u.rstrip("/") for u in args.urls.split(",")]
    connector = aiohttp.TCPConnector(limit=0, ttl_dns_cache=300)
    timeout = aiohttp.ClientTimeout(total=10)
    async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
        # Warm-up: open connections and load the Lua scripts on each replica.
        warm_deadline = time.perf_counter() + args.warmup
        await asyncio.gather(
            *(worker(session, urls, n, warm_deadline, args, [], Counter(), Counter()) for n in range(args.concurrency))
        )
        latencies: list[float] = []
        outcomes: Counter = Counter()
        allowed_by_client: Counter = Counter()
        start = time.perf_counter()
        deadline = start + args.duration
        await asyncio.gather(
            *(
                worker(session, urls, n, deadline, args, latencies, outcomes, allowed_by_client)
                for n in range(args.concurrency)
            )
        )
        wall = time.perf_counter() - start

    latencies.sort()
    completed = outcomes["allowed"] + outcomes["denied"]
    result = {
        "replicas": len(urls),
        "algorithm": args.algorithm,
        "concurrency": args.concurrency,
        "client_pool": args.clients,
        "duration_s": round(wall, 2),
        "requests_completed": completed,
        "throughput_rps": round(completed / wall, 1),
        "latency_ms": {
            "p50": round(percentile(latencies, 50) * 1000, 2),
            "p95": round(percentile(latencies, 95) * 1000, 2),
            "p99": round(percentile(latencies, 99) * 1000, 2),
            "max": round((latencies[-1] if latencies else 0) * 1000, 2),
            "mean": round(statistics.fmean(latencies) * 1000, 2) if latencies else 0,
        },
        "allowed": outcomes["allowed"],
        "denied": outcomes["denied"],
        "errors": {k: v for k, v in outcomes.items() if k not in {"allowed", "denied"}},
    }
    if args.algorithm == "token_bucket":
        # Upper bound per client over the warm-up + measured period (tokens may carry over from warm-up).
        bound = args.capacity + args.refill_rate * (wall + args.warmup)
        worst = max(allowed_by_client.values(), default=0)
        result["over_admission_check"] = {
            "max_allowed_single_client": worst,
            "theoretical_max_per_client": round(bound, 1),
            "passed": worst <= bound,
        }
    return result


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--urls", required=True, help="comma-separated base URLs of the API replicas")
    p.add_argument("--duration", type=float, default=30)
    p.add_argument("--warmup", type=float, default=3)
    p.add_argument("--concurrency", type=int, default=64, help="in-flight requests")
    p.add_argument("--clients", type=int, default=1000, help="size of the client-id pool")
    p.add_argument("--algorithm", choices=["token_bucket", "sliding_window"], default="token_bucket")
    p.add_argument("--capacity", type=float, default=100, help="TOKEN_BUCKET_CAPACITY the servers use")
    p.add_argument("--refill-rate", type=float, default=10, help="TOKEN_BUCKET_REFILL_RATE the servers use")
    p.add_argument("--json", help="write the result to this file")
    args = p.parse_args()
    result = asyncio.run(run(args))
    print(json.dumps(result, indent=2))
    if args.json:
        with open(args.json, "w") as fh:
            json.dump(result, fh, indent=2)


if __name__ == "__main__":
    main()
