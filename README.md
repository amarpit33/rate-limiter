![CI](https://github.com/amarpit33/rate-limiter/actions/workflows/ci.yml/badge.svg)

# Rate Limiter — Python + Redis

Token bucket + sliding window rate limiter with REST API.

**Built with:** Python · FastAPI · Redis · Docker  
**Status:** ✅ Complete

---

## Overview

Implements two rate limiting algorithms:

- **Token Bucket** — allows bursts up to a capacity, then refills at a fixed rate. Good for APIs where occasional spikes are acceptable.
- **Sliding Window** — counts requests in a rolling time window per client. More precise, prevents boundary exploits of fixed windows.

Redis is the backing store so rate limit state is shared across all API instances — enabling stateless, horizontally scalable deployments.

---

## Design Decisions

**Why Redis?**  
Rate limit state must be shared across all API instances for horizontal scaling. Redis atomic operations (`INCR`, `EXPIRE`) prevent race conditions without explicit locking.

**Token Bucket vs Sliding Window — when to use which?**
- Token Bucket: better for bursty traffic (APIs, mobile clients)
- Sliding Window: more precise, prevents boundary-exploit attacks

**Why stateless API instances?**  
All state lives in Redis — API pods can scale horizontally without sticky sessions or shared memory.

---

## Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/check` | Check if a request is allowed for a given client |
| `GET` | `/status/{client_id}` | Get current token count / window usage |
| `DELETE` | `/reset/{client_id}` | Reset limits for a client (admin) |

## Run Locally

```bash
docker-compose up
```

API will be available at `http://localhost:8000`  
Interactive docs at `http://localhost:8000/docs`

---

## Project Structure

```
rate-limiter/
├── app/
│   ├── main.py            # FastAPI app + routes
│   ├── limiter/
│   │   ├── token_bucket.py
│   │   └── sliding_window.py
│   └── redis_client.py
├── tests/
├── docker-compose.yml
├── Dockerfile
└── requirements.txt
```
