#!/usr/bin/env bash
# Start N API replicas (one uvicorn worker each) sharing one Redis, then load them.
#
#   REPLICAS=3 DURATION=30 CONCURRENCY=64 ./bench/run_local.sh
#
# Needs: redis-server on PATH (or REDIS_URL pointing at a running Redis) and
# `pip install -r requirements-dev.txt`. Run from the repository root.
set -euo pipefail

REPLICAS=${REPLICAS:-3}
BASE_PORT=${BASE_PORT:-8001}
DURATION=${DURATION:-30}
CONCURRENCY=${CONCURRENCY:-64}
CLIENTS=${CLIENTS:-1000}
ALGORITHM=${ALGORITHM:-token_bucket}
REDIS_PORT=${REDIS_PORT:-6399}
export REDIS_URL=${REDIS_URL:-redis://127.0.0.1:${REDIS_PORT}/0}
export TOKEN_BUCKET_CAPACITY=${TOKEN_BUCKET_CAPACITY:-100}
export TOKEN_BUCKET_REFILL_RATE=${TOKEN_BUCKET_REFILL_RATE:-10}
export SLIDING_WINDOW_LIMIT=${SLIDING_WINDOW_LIMIT:-100}
export SLIDING_WINDOW_SECONDS=${SLIDING_WINDOW_SECONDS:-10}
export REDIS_TIMEOUT_SECONDS=${REDIS_TIMEOUT_SECONDS:-2}

mkdir -p bench/logs
pids=()
cleanup() {
  for pid in "${pids[@]}"; do kill "$pid" 2>/dev/null || true; done
  for pid in "${pids[@]}"; do wait "$pid" 2>/dev/null || true; done
}
trap cleanup EXIT

if [[ -z "${EXTERNAL_REDIS:-}" ]]; then
  redis-server --port "$REDIS_PORT" --save "" --appendonly no --daemonize no >bench/logs/redis.log 2>&1 &
  pids+=($!)
fi
for _ in $(seq 1 25); do redis-cli -u "$REDIS_URL" ping >/dev/null 2>&1 && break; sleep 0.2; done
redis-cli -u "$REDIS_URL" flushdb >/dev/null

urls=()
for ((i = 0; i < REPLICAS; i++)); do
  port=$((BASE_PORT + i))
  uvicorn app.main:app --host 127.0.0.1 --port "$port" --no-access-log --log-level warning \
    >"bench/logs/api-$port.log" 2>&1 &
  pids+=($!)
  urls+=("http://127.0.0.1:$port")
done

for url in "${urls[@]}"; do
  healthy=""
  for _ in $(seq 1 50); do curl -sf "$url/health" >/dev/null && healthy=1 && break; sleep 0.2; done
  if [[ -z "$healthy" ]]; then echo "replica $url did not become healthy; see bench/logs/" >&2; exit 1; fi
done

url_list=$(IFS=,; echo "${urls[*]}")
extra=()
if [[ -n "${RESULT_JSON:-}" ]]; then extra=(--json "$RESULT_JSON"); fi
python bench/loadgen.py --urls "$url_list" --duration "$DURATION" --concurrency "$CONCURRENCY" \
  --clients "$CLIENTS" --algorithm "$ALGORITHM" \
  --capacity "$TOKEN_BUCKET_CAPACITY" --refill-rate "$TOKEN_BUCKET_REFILL_RATE" \
  ${extra[@]+"${extra[@]}"}
