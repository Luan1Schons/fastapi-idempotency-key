<div align="center">

# FastAPI Idempotency Key 🛡️

[![CI Status](https://github.com/Luan1Schons/fastapi-idempotency-key/actions/workflows/test.yml/badge.svg)](https://github.com/Luan1Schons/fastapi-idempotency-key/actions/workflows/test.yml)
[![PyPI Version](https://img.shields.io/pypi/v/fastapi-idempotency-key.svg?color=blue&cache=1)](https://pypi.org/project/fastapi-idempotency-key/)
[![Python Versions](https://img.shields.io/pypi/pyversions/fastapi-idempotency-key.svg?cache=1)](https://pypi.org/project/fastapi-idempotency-key/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Coverage](https://img.shields.io/badge/coverage-99%25-brightgreen.svg)](https://github.com/Luan1Schons/fastapi-idempotency-key)
[![Code style: black](https://img.shields.io/badge/code%20style-black-000000.svg)](https://github.com/psf/black)
[![Type Checked: mypy](https://img.shields.io/badge/type%20checked-mypy-blue.svg)](https://mypy-lang.org/)

**Stripe-grade `Idempotency-Key` engine for FastAPI and Starlette APIs.**<br>
Guaranteed exactly-once execution, deterministic SHA-256 fingerprinting, atomic distributed locks, and pluggable backends (Memory, SQLite, Redis).

<p align="center">
  <b><a href="README.md">English</a></b> • <b><a href="README.pt-BR.md">Português (Brasil)</a></b>
</p>

</div>

---

## 📖 Table of Contents

- [💥 The Problem: Why Idempotency Matters](#-the-problem-why-idempotency-matters)
- [🛡️ The Solution: Stripe-Grade Idempotency Engine](#️-the-solution-stripe-grade-idempotency-engine)
- [🎯 Real-World Applications](#-real-world-applications)
- [⚙️ How It Works Under the Hood](#️-how-it-works-under-the-hood)
- [📊 Architecture & Flow](#-architecture--flow)
- [✨ Key Features](#-key-features)
- [📦 Installation](#-installation)
- [🚀 Quick Start](#-quick-start)
  - [1. Global ASGI Middleware](#1-global-asgi-middleware)
  - [2. Route-Level Decorator](#2-route-level-decorator)
- [🗄️ Storage Backends Comparison](#️-storage-backends-comparison)
  - [MemoryBackend](#1-memorybackend)
  - [SQLiteBackend](#2-sqlitebackend)
  - [RedisBackend](#3-redisbackend)
- [🛠️ Configuration & API Reference](#️-configuration--api-reference)
  - [IdempotencyMiddleware](#idempotencymiddleware)
  - [@idempotent Decorator](#idempotent-decorator)
- [🚨 Error Handling & HTTP Status Codes](#-error-handling--http-status-codes)
- [🧪 Contributing & Testing](#-contributing--testing)
- [📄 License](#-license)

---

## 💥 The Problem: Why Idempotency Matters

In distributed systems, networks are inherently unreliable. Consider a critical payment endpoint: `POST /api/v1/checkout`.

```
[Client App]                     [FastAPI Server]                 [Payment Gateway / DB]
     |                                  |                                    |
     |---- 1. POST /checkout ---------->|                                    |
     |    (Header: Idempotency-Key)     |---- 2. Charge $500 --------------->|
     |                                  |<--- 3. Charge Successful ----------|
     |                                  |
     |  x-- 4. Network drops! ---------x|  (Client never receives HTTP 200)
     |     (or 4G timeout occurs)
     |
     |---- 5. Retry / Double-click! --->|
     |    (Axios/SDK auto-retry)        |---- 6. DUPLICATE CHARGE! --------->|  💥 DISASTER!
```

### The Real-World Breakdown:
1. A customer clicks **"Pay Now"**.
2. The server processes the charge ($500.00 debited from the card) and saves the order.
3. Right before the server can return `HTTP 200 OK`, the client's mobile connection drops for 200ms, or an HTTP gateway timeout triggers.
4. The client's frontend (or SDK, Axios, React Query) assumes the request failed and **retries automatically**. Or the user panics and clicks "Pay" again.
5. **The Disaster**: Without server-side idempotency safeguards, the server executes the route handler again, **charging the customer a second time ($1,000 total)** and creating duplicate fulfillment records.

> Under the HTTP specification, methods like `GET`, `PUT`, and `DELETE` are naturally idempotent. But **`POST` is not**. Without server-side idempotency, duplicate side effects, double billings, and race conditions are guaranteed to happen in production.

---

## 🛡️ The Solution: Stripe-Grade Idempotency Engine

`fastapi-idempotency-key` implements the official [IETF HTTP Idempotency-Key specification](https://datatracker.ietf.org/doc/draft-ietf-httpapi-idempotency-key-header/) and Stripe's battle-tested idempotency model.

When the client attaches an `Idempotency-Key` header (e.g., a client-generated UUID v4):

1. **First Arrival**: An atomic lock is claimed. Your FastAPI endpoint executes normally. The status code (`200 OK`), response headers, and response payload are atomically cached with a configurable TTL.
2. **Subsequent Retries / Duplicate Requests**: The endpoint **does not run again**. The cached response is returned in `< 1ms` with an `Idempotency-Replayed: true` header. Zero duplicate charges, zero side effects.
3. **Race Condition Protection**: If two requests with the same key arrive at the exact same millisecond, the first claims the lock and the second is immediately rejected with **`HTTP 409 Conflict`** (or waits for completion if `timeout` is configured).
4. **Payload Tampering Protection**: If a client reuses an existing key with a different body or query params, it is blocked with **`HTTP 422 Unprocessable Entity`**.
5. **Automatic Fault Recovery**: If your server raises an unhandled exception or returns a 5xx error, the lock is automatically released so the client can safely retry without waiting for the TTL to expire.

---

## 🎯 Real-World Applications

- 💳 **Fintechs & Payment Gateways**: Credit card authorizations, instant PIX / wire transfers, invoice generation, and refunds.
- 🛍️ **E-Commerce & Marketplaces**: Order checkout, inventory reservation, and single-use promo code redemption.
- ⚡ **Webhook Ingestion**: Third-party providers (Stripe, GitHub, Shopify, Mercado Pago) retry delivery 5–10 times until receiving HTTP 200. This library guarantees your consumer processes each event exactly once.
- 📨 **Microservices & Event Queues**: Distributed services consuming message queues (Kafka, RabbitMQ, Celery) operating under *at-least-once delivery* guarantees.
- 📱 **Mobile Applications**: Unstable cellular connections (tunnels, elevators, roaming) where network requests disconnect mid-flight.

---

## ⚙️ How It Works Under the Hood

1. **Request Interception**: Incoming requests are inspected for the `Idempotency-Key` header (e.g. `Idempotency-Key: e8a78bf5-4f40-42bf-9076-2f6cfd939634`).
2. **Deterministic Fingerprinting**: A deterministic SHA-256 hash is computed over the HTTP method, normalized URL path, sorted query parameters, and raw request body.
3. **Atomic Mutual Exclusion**: An atomic lock is claimed in the storage backend (Memory, SQLite WAL, or Redis Lua script).
4. **Three Execution Outcomes**:
   - **First Arrival (Lock Acquired)**: Downstream FastAPI route executes normally. The completed HTTP status code, response headers, and response body are atomically cached with a TTL.
   - **Concurrent Duplicate (In Flight)**: If another request with the same key is currently running, the server rejects it with **HTTP 409 Conflict** (or optionally blocks up to `timeout` seconds to await completion).
   - **Subsequent Duplicate (Completed)**: If the request was previously executed, the exact cached response is replayed with an `Idempotency-Replayed: true` header.
5. **Mismatch Protection**: If an existing key is reused with a different payload or query parameters, the server blocks execution and immediately responds with **HTTP 422 Unprocessable Entity**.
6. **Automatic Fault Tolerance**: If your application crashes or returns a 5xx server error, the lock is automatically released so the client can safely retry without waiting for the TTL to expire.

---

## 📊 Architecture & Flow

```mermaid
%%{init: {'theme': 'neutral'}}%%
flowchart TD
    Start(["Incoming HTTP Request"]) --> CheckHeader{"Has Idempotency-Key?"}

    CheckHeader -- No --> PassThrough["Execute Downstream Route Directly"]
    CheckHeader -- Yes --> CalcFP["Compute SHA-256 Request Fingerprint"]

    CalcFP --> TryLock{"Atomic Lock Attempt<br/>(Memory / SQLite / Redis)"}

    TryLock -- "Acquired (New Key)" --> ExecHandler["Execute Route Handler"]

    ExecHandler --> StatusCheck{"Response Status Code"}
    StatusCheck -- "2xx / 3xx / 4xx (Cacheable)" --> CacheResp["Cache Status, Headers & Body<br/>Set Status = COMPLETED"]
    StatusCheck -- "5xx Error / Exception" --> ReleaseLock["Release Lock<br/>Allow Client Retry"]

    CacheResp --> ReturnOriginal["Return Original Response to Client"]
    ReleaseLock --> ReturnError["Return 5xx Error Response"]

    TryLock -- "Key Exists: COMPLETED" --> VerifyFP{"Fingerprint Matches?"}
    VerifyFP -- "Yes" --> ReplayCached["Replay Cached Response<br/>Header: Idempotency-Replayed = true"]
    VerifyFP -- "No (Payload Mismatch)" --> RejectMismatch["Return HTTP 422 Unprocessable Entity"]

    TryLock -- "Key Exists: IN_PROGRESS" --> CheckTimeout{"timeout configured?"}
    CheckTimeout -- "No / Expired" --> ReturnConflict["Return HTTP 409 Conflict<br/>(Request In Progress)"]
    CheckTimeout -- "Yes (Waits for Lock)" --> AwaitComplete["Wait & Replay on Completion"]
    AwaitComplete --> ReplayCached
```

---

## ✨ Key Features

- **Strict Exactly-Once Semantics**: Eliminates duplicate operations and race conditions under heavy concurrent load.
- **Atomic Concurrency Protection**: High-concurrency protection via `asyncio.Lock` (Memory), atomic transactions in WAL mode (SQLite), or single-roundtrip atomic Lua scripts (Redis).
- **Deterministic SHA-256 Fingerprinting**: Detects request tampering, altered bodies, or mismatched query parameters.
- **Zero-Loss Replay Engine**: Transparently captures and replays status codes, custom headers, JSON payloads, binary blobs, and `StreamingResponse` objects.
- **Fail-Safe Exception Recovery**: Unhandled Python exceptions or 500 server errors immediately release locks, allowing client retries without waiting for expiration.
- **Flexible Integration**: Apply globally as an ASGI middleware (`IdempotencyMiddleware`) or selectively on individual endpoints via `@idempotent`.
- **Zero External Dependencies Required**: Core functionality works out-of-the-box using pure Python and Starlette.
- **100% Type-Annotated**: Full `mypy --strict` compliance and `py.typed` marker included.

---

## 📦 Installation

```bash
# Core package (in-memory backend included, zero extra dependencies)
pip install fastapi-idempotency-key

# With Redis backend support
pip install "fastapi-idempotency-key[redis]"

# With SQLite backend support
pip install "fastapi-idempotency-key[sqlite]"

# With all backend drivers
pip install "fastapi-idempotency-key[all]"
```

---

## 🚀 Quick Start

### 1. Global ASGI Middleware

Protect all mutating endpoints (`POST`, `PUT`, `PATCH`) across your entire application in under 30 seconds:

```python
from fastapi import FastAPI
from fastapi_idempotency_key import IdempotencyMiddleware, MemoryBackend

app = FastAPI(title="Payment API")

# Register middleware with in-memory storage (24-hour TTL)
app.add_middleware(
    IdempotencyMiddleware,
    backend=MemoryBackend(),
    header_name="Idempotency-Key",
    default_ttl=86400,
)

@app.post("/payments")
async def create_payment(payment: dict):
    # Guaranteed to execute exactly once per Idempotency-Key!
    return {"status": "paid", "amount": payment["amount"]}
```

Test it with `curl`:
```bash
# 1. First request: Executes handler normally
curl -i -X POST http://localhost:8000/payments \
  -H "Idempotency-Key: pay_unique_987" \
  -H "Content-Type: application/json" \
  -d '{"amount": 100}'

# 2. Second request with same key: Replayed immediately from cache (<1ms)
curl -i -X POST http://localhost:8000/payments \
  -H "Idempotency-Key: pay_unique_987" \
  -H "Content-Type: application/json" \
  -d '{"amount": 100}'
# -> Returns HTTP 200 with header: "Idempotency-Replayed: true"

# 3. Tampered request with same key but altered payload:
curl -i -X POST http://localhost:8000/payments \
  -H "Idempotency-Key: pay_unique_987" \
  -H "Content-Type: application/json" \
  -d '{"amount": 250}'
# -> Returns HTTP 422 Unprocessable Entity
```

---

### 2. Route-Level Decorator

Target only high-risk routes (e.g. checkout, refunds) without applying idempotency globally:

```python
from fastapi import FastAPI, Request
from fastapi_idempotency_key import idempotent, MemoryBackend

app = FastAPI()
backend = MemoryBackend()

@app.post("/checkout")
@idempotent(backend=backend, expire=300, required=True)
async def checkout(payload: dict, request: Request):
    return {"order_id": "ord_123", "status": "confirmed"}

@app.post("/cart/items")
async def add_to_cart(item: dict):
    # Standard endpoint: not idempotent
    return {"status": "added", "item": item}
```

---

## 🗄️ Storage Backends Comparison

| Feature | `MemoryBackend` | `SQLiteBackend` | `RedisBackend` |
| :--- | :---: | :---: | :---: |
| **Persistence** | In-Memory (Volatile) | Disk / File / `:memory:` | Memory / RDB / AOF |
| **Multi-Process Safe** | Single Process | Multi-Process (WAL Mode) | Distributed Multi-Worker |
| **Locking Mechanism** | `asyncio.Lock` | SQLite Immediate Transactions | Atomic Redis Lua Scripts |
| **Eviction / TTL** | In-memory LRU + TTL Purge | SQLite Index + On-demand TTL | Native Redis Key TTL |
| **Setup Overhead** | Zero Dependencies | `aiosqlite` | `redis-py` (asyncio) |
| **Ideal Use Case** | Testing, Local Dev, Micro-apps | Single-node servers, Embedded APIs | Distributed microservices, Kubernetes |

### 1. `MemoryBackend`

```python
from fastapi_idempotency_key import MemoryBackend

# Keep up to 10,000 keys in memory with LRU eviction
backend = MemoryBackend(max_keys=10_000)
```

### 2. `SQLiteBackend`

Thread-safe and process-safe persistent storage using `aiosqlite` with Write-Ahead Logging (WAL) enabled:

```python
from fastapi_idempotency_key import SQLiteBackend

# Persistent SQLite database file
backend = SQLiteBackend(database_path="idempotency.db")
```

### 3. `RedisBackend`

Enterprise-scale distributed storage with atomic single-flight Lua scripts:

```python
from fastapi_idempotency_key import RedisBackend

# Connect via connection string
backend = RedisBackend(redis_url="redis://localhost:6379/0", prefix="myapp:idempotency:")

# Or pass an existing redis.asyncio.Redis instance:
import redis.asyncio as aioredis

redis_client = aioredis.from_url("redis://localhost:6379/0")
backend = RedisBackend(redis=redis_client)
```

---

## 🛠️ Configuration & API Reference

### `IdempotencyMiddleware`

| Parameter | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| `app` | `ASGIApp` | *Required* | Downstream ASGI application. |
| `backend` | `BaseIdempotencyBackend` | `MemoryBackend()` | Storage backend instance. |
| `header_name` | `str` | `"Idempotency-Key"` | Case-insensitive header name for client keys. |
| `replay_header_name` | `str` | `"Idempotency-Replayed"` | Header set to `"true"` on replayed responses. |
| `enforce_methods` | `Tuple[str, ...]` | `("POST", "PATCH", "PUT")` | HTTP verbs subjected to idempotency enforcement. |
| `default_ttl` | `int` | `86400` | Expiration time for cached responses (in seconds). |
| `timeout` | `float` | `0.0` | Maximum seconds to wait for an in-progress request before returning 409 Conflict. |
| `cache_statuses` | `Tuple[int, ...]` | `(200, 201, 202, 204, ...)` | HTTP status codes that will be persisted and replayed. |
| `on_conflict_status`| `int` | `409` | Status code returned on concurrent in-progress duplicate. |
| `on_mismatch_status`| `int` | `422` | Status code returned on payload mismatch. |
| `required` | `bool` | `False` | When `True`, returns HTTP 400 if the header is missing. |

---

### `@idempotent` Decorator

| Parameter | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| `expire` | `int` | `86400` | TTL in seconds for the cached response. |
| `header_name` | `str` | `"Idempotency-Key"` | Header name to inspect. |
| `replay_header_name`| `str` | `"Idempotency-Replayed"` | Header attached to replayed responses. |
| `backend` | `Optional[BaseIdempotencyBackend]` | `None` | Storage backend (defaults to global memory store). |
| `required` | `bool` | `False` | Raise `IdempotencyKeyMissingError` (HTTP 400) if absent. |
| `timeout` | `float` | `0.0` | Seconds to wait for concurrent requests. |
| `cache_statuses` | `Tuple[int, ...]` | `(200, 201, 202, 204, ...)` | Status codes to cache. |

---

## 🚨 Error Handling & HTTP Status Codes

| HTTP Status | Condition | Example Response Body |
| :---: | :--- | :--- |
| **`409 Conflict`** | Another request with the same key is currently running. | `{"detail": "A request with this idempotency key is currently in progress."}` |
| **`422 Unprocessable`** | Key was previously used with a different body, path, or query string. | `{"detail": "Idempotency key was previously used with a different request payload."}` |
| **`400 Bad Request`** | `required=True` is enabled and client omitted the header. | `{"detail": "Idempotency-Key header is required."}` |
| **`5xx Server Error`** | Upstream handler failed; lock is released to allow client retry. | Normal upstream server error representation. |

---

## 🧪 Contributing & Testing

We uphold strict quality gates with 99%+ test coverage and comprehensive static analysis.

```bash
# Clone the repository
git clone https://github.com/Luan1Schons/fastapi-idempotency-key.git
cd fastapi-idempotency-key

# Create and activate virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install development dependencies
pip install -e ".[all,dev]"

# Run tests with coverage
pytest --cov=fastapi_idempotency_key --cov-report=term-missing

# Run code formatters and linters
black --check fastapi_idempotency_key tests examples
flake8 fastapi_idempotency_key tests examples
mypy fastapi_idempotency_key tests examples
```

---

## 📄 License

This project is licensed under the terms of the [MIT License](LICENSE).
