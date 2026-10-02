from __future__ import annotations

import asyncio
import time
from typing import Any, AsyncIterator, cast
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse, PlainTextResponse, StreamingResponse
import httpx
import pytest
from starlette.types import Message

from fastapi_idempotency_key import (
    IdempotencyMiddleware,
    MemoryBackend,
    SQLiteBackend,
    idempotent,
)
from fastapi_idempotency_key.backends.base import BaseIdempotencyBackend
from fastapi_idempotency_key.backends.redis import RedisBackend
from fastapi_idempotency_key.exceptions import (
    IdempotencyConflictError,
    IdempotencyKeyMissingError,
    IdempotencyPayloadMismatchError,
    IdempotencyStorageError,
)
from fastapi_idempotency_key.status import IdempotencyRecord, IdempotencyStatus

# ------------------------------------------------------------------------------
# 1. Streaming responses and Large Payloads
# ------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_streaming_response_buffering_and_replay() -> None:
    """StreamingResponse should be captured in full and replayed on subsequent requests."""
    app = FastAPI()
    backend = MemoryBackend()
    app.add_middleware(IdempotencyMiddleware, backend=backend)

    stream_executions = 0

    @app.post("/stream")
    async def stream_data() -> StreamingResponse:
        nonlocal stream_executions
        stream_executions += 1

        async def generator() -> AsyncIterator[bytes]:
            for i in range(4):
                yield f"chunk-{i}\n".encode("utf-8")
                await asyncio.sleep(0.01)

        return StreamingResponse(generator(), media_type="text/plain")

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        headers = {"Idempotency-Key": "stream_key_1"}

        # First request (streams from generator)
        res1 = await client.post("/stream", headers=headers)
        assert res1.status_code == 200
        assert res1.text == "chunk-0\nchunk-1\nchunk-2\nchunk-3\n"
        assert "Idempotency-Replayed" not in res1.headers
        assert stream_executions == 1

        # Second request (replayed from cache)
        res2 = await client.post("/stream", headers=headers)
        assert res2.status_code == 200
        assert res2.text == "chunk-0\nchunk-1\nchunk-2\nchunk-3\n"
        assert res2.headers.get("Idempotency-Replayed") == "true"
        assert stream_executions == 1


@pytest.mark.asyncio
async def test_large_payload_handling_and_mismatch() -> None:
    """Large request payloads (>1MB) should be fingerprinted and replayed correctly,

    and mismatch detection should work seamlessly.
    """
    app = FastAPI()
    backend = MemoryBackend()
    app.add_middleware(IdempotencyMiddleware, backend=backend)

    executions = 0

    @app.post("/large-upload")
    async def upload_large(request: Request) -> dict[str, Any]:
        nonlocal executions
        executions += 1
        body = await request.body()
        return {"received_bytes": len(body), "executions": executions}

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        large_data = b"x" * (1024 * 1024)  # 1 MB
        headers = {
            "Idempotency-Key": "large_key_1",
            "Content-Type": "application/octet-stream",
        }

        # Initial large request
        res1 = await client.post("/large-upload", content=large_data, headers=headers)
        assert res1.status_code == 200
        assert res1.json() == {"received_bytes": 1024 * 1024, "executions": 1}
        assert executions == 1

        # Replay identical large request
        res2 = await client.post("/large-upload", content=large_data, headers=headers)
        assert res2.status_code == 200
        assert res2.headers.get("Idempotency-Replayed") == "true"
        assert res2.json() == {"received_bytes": 1024 * 1024, "executions": 1}
        assert executions == 1

        # Mismatch with 1-byte alteration
        altered_data = large_data + b"y"
        res3 = await client.post("/large-upload", content=altered_data, headers=headers)
        assert res3.status_code == 422
        assert "different request payload" in res3.json()["detail"]


# ------------------------------------------------------------------------------
# 2. 500 Server Error Lock Release
# ------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_middleware_unhandled_exception_releases_lock() -> None:
    """An unhandled Python exception in route must release lock so client can retry immediately."""
    app = FastAPI()
    backend = MemoryBackend()
    app.add_middleware(IdempotencyMiddleware, backend=backend)

    attempt = 0

    @app.post("/crash")
    async def crashing_endpoint() -> dict[str, str]:
        nonlocal attempt
        attempt += 1
        if attempt == 1:
            raise RuntimeError("Database connection pool exhausted")
        return {"status": "recovered"}

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
    ) as client:
        # First attempt raises RuntimeError -> 500
        res1 = await client.post(
            "/crash", json={}, headers={"Idempotency-Key": "crash_key"}
        )
        assert res1.status_code == 500

        # Lock was released, retry with same key succeeds!
        res2 = await client.post(
            "/crash", json={}, headers={"Idempotency-Key": "crash_key"}
        )
        assert res2.status_code == 200
        assert res2.json() == {"status": "recovered"}


@pytest.mark.asyncio
async def test_middleware_explicit_500_response_releases_lock() -> None:
    """Returning a 500 Response directly must release the lock."""
    app = FastAPI()
    backend = MemoryBackend()
    app.add_middleware(IdempotencyMiddleware, backend=backend)

    calls = 0

    @app.post("/explicit-500")
    async def explicit_500() -> Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return JSONResponse(
                status_code=500, content={"error": "service unavailable"}
            )
        return JSONResponse(status_code=200, content={"status": "ok"})

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        r1 = await client.post(
            "/explicit-500", json={}, headers={"Idempotency-Key": "exp_500_key"}
        )
        assert r1.status_code == 500

        # Retry succeeds
        r2 = await client.post(
            "/explicit-500", json={}, headers={"Idempotency-Key": "exp_500_key"}
        )
        assert r2.status_code == 200
        assert r2.json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_decorator_500_server_error_releases_lock() -> None:
    """Decorator releasing lock on unhandled exception and explicit 500 response."""
    app = FastAPI()
    backend = MemoryBackend()

    crash_attempts = 0

    @app.post("/dec-crash")
    @idempotent(backend=backend)
    async def dec_crash() -> dict[str, str]:
        nonlocal crash_attempts
        crash_attempts += 1
        if crash_attempts == 1:
            raise RuntimeError("Transient crash in decorated route")
        return {"status": "ok"}

    resp_attempts = 0

    @app.post("/dec-resp-500")
    @idempotent(backend=backend)
    async def dec_resp_500() -> Response:
        nonlocal resp_attempts
        resp_attempts += 1
        if resp_attempts == 1:
            return JSONResponse(status_code=500, content={"error": "transient"})
        return JSONResponse(status_code=200, content={"status": "recovered"})

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
    ) as client:
        # Crash retry
        r1 = await client.post(
            "/dec-crash", json={}, headers={"Idempotency-Key": "dec_crash_k"}
        )
        assert r1.status_code == 500
        r2 = await client.post(
            "/dec-crash", json={}, headers={"Idempotency-Key": "dec_crash_k"}
        )
        assert r2.status_code == 200
        assert r2.json() == {"status": "ok"}

        # Explicit 500 retry
        r3 = await client.post(
            "/dec-resp-500", json={}, headers={"Idempotency-Key": "dec_500_k"}
        )
        assert r3.status_code == 500
        r4 = await client.post(
            "/dec-resp-500", json={}, headers={"Idempotency-Key": "dec_500_k"}
        )
        assert r4.status_code == 200
        assert r4.json() == {"status": "recovered"}


# ------------------------------------------------------------------------------
# 3. Decorator Edge Cases (Sync Endpoints, Custom Headers, Status Codes)
# ------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_decorator_sync_endpoints() -> None:
    """Synchronous endpoint functions should be supported by @idempotent."""
    app = FastAPI()
    backend = MemoryBackend()

    sync_calls = 0

    @app.post("/sync-endpoint")
    @idempotent(backend=backend)
    def sync_endpoint(payload: dict[str, Any], request: Request) -> dict[str, Any]:
        nonlocal sync_calls
        sync_calls += 1
        return {"sync": True, "count": sync_calls, "data": payload}

    sync_resp_calls = 0

    @app.post("/sync-response")
    @idempotent(backend=backend)
    def sync_response_endpoint(request: Request) -> Response:
        nonlocal sync_resp_calls
        sync_resp_calls += 1
        return PlainTextResponse(f"sync response {sync_resp_calls}", status_code=201)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        # Sync dict return
        r1 = await client.post(
            "/sync-endpoint",
            json={"action": "test"},
            headers={"Idempotency-Key": "sync_k1"},
        )
        assert r1.status_code == 200
        assert r1.json()["count"] == 1
        assert "Idempotency-Replayed" not in r1.headers

        r2 = await client.post(
            "/sync-endpoint",
            json={"action": "test"},
            headers={"Idempotency-Key": "sync_k1"},
        )
        assert r2.status_code == 200
        assert r2.json()["count"] == 1
        assert r2.headers.get("Idempotency-Replayed") == "true"
        assert sync_calls == 1

        # Sync Response object return
        r3 = await client.post(
            "/sync-response", headers={"Idempotency-Key": "sync_resp_k"}
        )
        assert r3.status_code == 201
        assert r3.text == "sync response 1"

        r4 = await client.post(
            "/sync-response", headers={"Idempotency-Key": "sync_resp_k"}
        )
        assert r4.status_code == 201
        assert r4.text == "sync response 1"
        assert r4.headers.get("Idempotency-Replayed") == "true"
        assert sync_resp_calls == 1


@pytest.mark.asyncio
async def test_decorator_custom_header_names() -> None:
    """Custom header names for key and replay."""
    app = FastAPI()
    backend = MemoryBackend()

    @app.post("/custom-headers")
    @idempotent(
        backend=backend,
        header_name="X-Custom-Idempotency",
        replay_header_name="X-Custom-Replayed",
    )
    async def custom_headers_endpoint(data: dict[str, str]) -> dict[str, str]:
        return {"status": "ok", "tag": data["tag"]}

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        headers = {"X-Custom-Idempotency": "custom_key_100"}
        r1 = await client.post("/custom-headers", json={"tag": "prod"}, headers=headers)
        assert r1.status_code == 200
        assert "X-Custom-Replayed" not in r1.headers

        r2 = await client.post("/custom-headers", json={"tag": "prod"}, headers=headers)
        assert r2.status_code == 200
        assert r2.headers.get("X-Custom-Replayed") == "true"


@pytest.mark.asyncio
async def test_decorator_custom_cache_statuses() -> None:
    """Custom cache_statuses: uncacheable status code releases lock."""
    app = FastAPI()
    backend = MemoryBackend()

    counter = 0

    @app.post("/teapot")
    @idempotent(backend=backend, cache_statuses=(200, 201))
    async def teapot_endpoint() -> Response:
        nonlocal counter
        counter += 1
        if counter == 1:
            # 418 is not in cache_statuses
            return Response(content="I'm a teapot", status_code=418)
        return Response(content="Now I am coffee", status_code=200)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        headers = {"Idempotency-Key": "teapot_key"}
        r1 = await client.post("/teapot", headers=headers)
        assert r1.status_code == 418

        # Lock was released, re-request executes handler again
        r2 = await client.post("/teapot", headers=headers)
        assert r2.status_code == 200
        assert r2.text == "Now I am coffee"
        assert counter == 2


@pytest.mark.asyncio
async def test_decorator_positional_request_and_direct_invocation() -> None:
    """Test decorator handling positional Request argument and direct function invocation."""
    backend = MemoryBackend()

    async def dummy_receive() -> dict[str, Any]:
        return {"type": "http.request", "body": b"", "more_body": False}

    # Function accepting request positionally
    @idempotent(backend=backend)
    async def pos_func(request: Request, x: int) -> int:
        return x * 2

    # Request passed positionally
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/pos",
        "headers": [(b"idempotency-key", b"pos_k")],
    }
    req = Request(scope, receive=dummy_receive)
    res = await pos_func(req, 5)
    assert res == 10

    # Direct invocation without Request when function doesn't require Request
    @idempotent(backend=backend)
    async def no_req_func(val: int) -> int:
        return val * 3

    assert await no_req_func(val=5) == 15

    # Sync function direct invocation (decorator wrapper is async)
    @idempotent(backend=backend)
    def sync_direct(val: str) -> str:
        return f"sync_{val}"

    assert await cast(Any, sync_direct)(val="hello") == "sync_hello"

    # Function with optional request parameter called without request (req is None)
    @idempotent(backend=backend)
    async def async_opt_req(request: Request = None, val: int = 1) -> int:  # type: ignore[assignment]
        return val * 10

    @idempotent(backend=backend)
    def sync_opt_req(request: Request = None, val: int = 1) -> int:  # type: ignore[assignment]
        return val * 20

    assert await async_opt_req(val=3) == 30
    assert await cast(Any, sync_opt_req)(val=4) == 80


@pytest.mark.asyncio
async def test_decorator_default_backend_and_missing_key_optional() -> None:
    """Test decorator using default MemoryBackend and missing header when required=False."""
    app = FastAPI()

    # Uses _get_default_backend()
    @app.post("/default-backend")
    @idempotent(required=False)
    async def def_backend(payload: dict[str, str]) -> dict[str, str]:
        return {"received": payload["msg"]}

    @app.post("/sync-no-key")
    @idempotent(required=False)
    def sync_no_key(payload: dict[str, str]) -> dict[str, str]:
        return {"sync_received": payload["msg"]}

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        # Async endpoint without header passes through
        r1 = await client.post("/default-backend", json={"msg": "hi"})
        assert r1.status_code == 200
        assert r1.json() == {"received": "hi"}

        # Async endpoint with header caches and replays
        r2 = await client.post(
            "/default-backend",
            json={"msg": "hi"},
            headers={"Idempotency-Key": "k_def"},
        )
        assert r2.status_code == 200
        r3 = await client.post(
            "/default-backend",
            json={"msg": "hi"},
            headers={"Idempotency-Key": "k_def"},
        )
        assert r3.headers.get("Idempotency-Replayed") == "true"

        # Sync endpoint without header passes through
        r4 = await client.post("/sync-no-key", json={"msg": "sync_hi"})
        assert r4.status_code == 200
        assert r4.json() == {"sync_received": "sync_hi"}


@pytest.mark.asyncio
async def test_decorator_handled_by_middleware_with_required_key_missing() -> None:
    """When _idempotency_handled is set by middleware, decorator verifies required header."""
    backend = MemoryBackend()

    @idempotent(backend=backend, required=True)
    async def endpoint(request: Request) -> dict[str, str]:
        return {"status": "ok"}

    # Simulate request with _idempotency_handled=True but no header
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/test",
        "headers": [],
        "_idempotency_handled": True,
    }
    req = Request(scope)
    with pytest.raises(IdempotencyKeyMissingError):
        await endpoint(request=req)


@pytest.mark.asyncio
async def test_decorator_handled_by_middleware_passes_through() -> None:
    """When _idempotency_handled is set and header present, decorator executes endpoint."""
    backend = MemoryBackend()

    @idempotent(backend=backend, required=True)
    async def async_ep(request: Request) -> dict[str, str]:
        return {"async": "ok"}

    @idempotent(backend=backend, required=True)
    def sync_ep(request: Request) -> dict[str, str]:
        return {"sync": "ok"}

    scope = {
        "type": "http",
        "method": "POST",
        "path": "/test",
        "headers": [(b"idempotency-key", b"key_123")],
        "_idempotency_handled": True,
    }
    req = Request(scope)
    assert await async_ep(request=req) == {"async": "ok"}
    assert await cast(Any, sync_ep)(request=req) == {"sync": "ok"}


@pytest.mark.asyncio
async def test_decorator_sync_error_releases_lock() -> None:
    """Synchronous function raising an error releases lock."""
    backend = MemoryBackend()

    runs = 0

    @idempotent(backend=backend)
    def failing_sync(request: Request) -> str:
        nonlocal runs
        runs += 1
        if runs == 1:
            raise ValueError("Sync failed")
        return "success"

    async def dummy_receive() -> dict[str, Any]:
        return {"type": "http.request", "body": b"", "more_body": False}

    scope = {
        "type": "http",
        "method": "POST",
        "path": "/test",
        "headers": [(b"idempotency-key", b"fail_k")],
    }
    req = Request(scope, receive=dummy_receive)

    with pytest.raises(ValueError, match="Sync failed"):
        await cast(Any, failing_sync)(request=req)

    # Lock must be released
    rec = await backend.get_record("fail_k")
    assert rec is None

    # Next attempt works
    res = await cast(Any, failing_sync)(request=req)
    assert res == "success"


# ------------------------------------------------------------------------------
# 4. Middleware Edge Cases
# ------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_middleware_non_http_scope() -> None:
    """Non-HTTP scopes (websocket, lifespan) are passed through without modification."""
    backend = MemoryBackend()

    called = False

    async def dummy_app(scope: Any, receive: Any, send: Any) -> None:
        nonlocal called
        called = True

    middleware = IdempotencyMiddleware(app=dummy_app, backend=backend)

    # Lifespan
    await middleware({"type": "lifespan"}, None, None)  # type: ignore[arg-type]
    assert called is True

    # Websocket
    called = False
    await middleware({"type": "websocket"}, None, None)  # type: ignore[arg-type]
    assert called is True


@pytest.mark.asyncio
async def test_middleware_custom_receive_more_messages() -> None:
    """Test custom_receive when called past buffered messages."""
    backend = MemoryBackend()

    receive_calls = 0

    async def mock_receive() -> dict[str, Any]:
        nonlocal receive_calls
        receive_calls += 1
        if receive_calls == 1:
            return {"type": "http.request", "body": b"initial", "more_body": False}
        return {"type": "http.disconnect"}

    async def app(scope: Any, receive: Any, send: Any) -> None:
        msg1 = await receive()
        msg2 = await receive()
        assert msg2["type"] == "http.disconnect"
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok", "more_body": False})

    middleware = IdempotencyMiddleware(app=app, backend=backend)
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/custom-rcv",
        "headers": [(b"idempotency-key", b"rcv_key")],
    }

    sent_messages: list[Message] = []

    async def mock_send(message: Message) -> None:
        sent_messages.append(message)

    await middleware(scope, mock_receive, mock_send)
    assert len(sent_messages) == 2
    assert sent_messages[0]["status"] == 200


@pytest.mark.asyncio
async def test_middleware_filters_existing_replay_header_and_content_length() -> None:
    """If downstream already had idempotency-replayed header or no content-length, handle correctly."""
    backend = MemoryBackend()

    async def app(scope: Any, receive: Any, send: Any) -> None:
        await receive()
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [
                    (b"idempotency-replayed", b"false"),
                    (b"content-type", b"text/plain"),
                ],
            }
        )
        # Send other message types to capture_send
        await send({"type": "http.response.trailers", "headers": []})
        await send(
            {"type": "http.response.body", "body": b"payload", "more_body": False}
        )

    middleware = IdempotencyMiddleware(app=app, backend=backend)
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/filter-header",
        "headers": [(b"idempotency-key", b"filter_k")],
    }

    sent1: list[Message] = []
    sent2: list[Message] = []

    async def send1(msg: Message) -> None:
        sent1.append(msg)

    async def send2(msg: Message) -> None:
        sent2.append(msg)

    async def receive_msg() -> dict[str, Any]:
        return {"type": "http.request", "body": b"", "more_body": False}

    await middleware(scope, receive_msg, send1)
    # Second request replaying
    await middleware(scope, receive_msg, send2)

    headers = dict(sent2[0]["headers"])
    assert b"Idempotency-Replayed" in headers
    assert headers[b"Idempotency-Replayed"] == b"true"
    assert b"content-length" in headers
    assert headers[b"content-length"] == b"7"


# ------------------------------------------------------------------------------
# 5. Storage Backend Edge Cases & Context Managers
# ------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_backend_context_manager() -> None:
    """Test BaseIdempotencyBackend async context manager."""
    async with MemoryBackend() as backend:
        locked, _ = await backend.try_lock("ctx_k", "fp", ttl=60)
        assert locked is True


@pytest.mark.asyncio
async def test_backend_timeout_expiry() -> None:
    """Test BaseIdempotencyBackend try_lock timing out when key stays IN_PROGRESS."""
    backend = MemoryBackend()
    locked, _ = await backend.try_lock("held_k", "fp", ttl=60)
    assert locked is True

    # Try to lock with small timeout
    start = time.monotonic()
    locked2, rec2 = await backend.try_lock("held_k", "fp", ttl=60, timeout=0.1)
    duration = time.monotonic() - start

    assert locked2 is False
    assert rec2 is not None
    assert rec2.status == IdempotencyStatus.IN_PROGRESS
    assert duration >= 0.1


@pytest.mark.asyncio
async def test_backend_timeout_unlock_acquisition() -> None:
    """Test BaseIdempotencyBackend acquiring lock when previously held lock is released during wait."""
    backend = MemoryBackend()
    await backend.try_lock("release_later_k", "fp", ttl=60)

    async def release_task() -> None:
        await asyncio.sleep(0.05)
        await backend.release_lock("release_later_k")

    asyncio.create_task(release_task())

    locked, rec = await backend.try_lock("release_later_k", "fp", ttl=60, timeout=0.3)
    assert locked is True
    assert rec is None


@pytest.mark.asyncio
async def test_memory_backend_expired_eviction_and_direct_store() -> None:
    """Test MemoryBackend purging expired keys during LRU eviction and direct store_response."""
    backend = MemoryBackend(max_keys=2)

    # Insert 2 keys, one with past expires_at
    await backend.try_lock("k1", "fp", ttl=60)
    await backend.try_lock("k2", "fp", ttl=1)

    # Manually expire k2
    backend._store["k2"].expires_at = time.time() - 10

    # Insert 3rd key, triggering _purge_expired_locked at line 28
    await backend.try_lock("k3", "fp", ttl=60)
    assert "k2" not in backend._store
    assert "k3" in backend._store

    # Test _try_lock_once on expired key at line 49
    backend._store["k3"].expires_at = time.time() - 10
    locked, _ = await backend.try_lock("k3", "fp_new", ttl=60)
    assert locked is True

    # Test store_response when key was not previously locked (line 91-102)
    await backend.store_response(
        "unlocked_key", 200, [("content-type", "text/plain")], b"hello", ttl=60
    )
    rec = await backend.get_record("unlocked_key")
    assert rec is not None
    assert rec.body == b"hello"


@pytest.mark.asyncio
async def test_sqlite_backend_invalid_path_and_body_types() -> None:
    """Test SQLiteBackend handling invalid DB directory and non-bytes body."""
    # Invalid directory raises IdempotencyStorageError
    invalid_backend = SQLiteBackend("/nonexistent/directory/idempotency.db")
    with pytest.raises(IdempotencyStorageError):
        await invalid_backend._get_conn()

    # Memory backend conversion of memoryview body
    mem_backend = SQLiteBackend(":memory:")
    locked, _ = await mem_backend.try_lock("byte_k", "fp", ttl=60)
    assert locked is True
    await mem_backend.store_response("byte_k", 200, [], b"bytes_data", ttl=60)

    # Test _row_to_record with memoryview
    conn = await mem_backend._get_conn()
    cur = await conn.execute(
        "SELECT * FROM idempotency_records WHERE key = ?", ("byte_k",)
    )
    row = await cur.fetchone()
    rec = mem_backend._row_to_record(row)
    assert isinstance(rec.body, bytes)
    assert rec.body == b"bytes_data"
    await mem_backend.close()


@pytest.mark.asyncio
async def test_sqlite_backend_expired_row_during_try_lock() -> None:
    """Test SQLiteBackend deleting expired row when concurrent lock hits."""
    backend = SQLiteBackend(":memory:")
    await backend.try_lock("sq_exp", "fp1", ttl=60)
    conn = await backend._get_conn()
    # Manually expire
    await conn.execute(
        "UPDATE idempotency_records SET expires_at = ? WHERE key = ?",
        (time.time() - 5, "sq_exp"),
    )
    await conn.commit()

    # _try_lock_once should catch the expired row, delete it, and lock
    locked, rec = await backend.try_lock("sq_exp", "fp2", ttl=60)
    assert locked is True
    assert rec is None
    await backend.close()


@pytest.mark.asyncio
async def test_status_record_from_dict_variations() -> None:
    """Test IdempotencyRecord.from_dict with bytes body and Enum status."""
    # body as raw bytes
    r1 = IdempotencyRecord.from_dict(
        {
            "key": "k",
            "fingerprint": "f",
            "status": IdempotencyStatus.COMPLETED,
            "body": b"raw_bytes",
        }
    )
    assert r1.body == b"raw_bytes"
    assert r1.status == IdempotencyStatus.COMPLETED

    # body as int/other
    r2 = IdempotencyRecord.from_dict(
        {
            "key": "k",
            "fingerprint": "f",
            "status": "in_progress",
            "body": 12345,
        }
    )
    assert r2.body == b""

    # is_expired
    r3 = IdempotencyRecord(
        "k", "f", IdempotencyStatus.COMPLETED, expires_at=time.time() - 1
    )
    assert r3.is_expired() is True
    r4 = IdempotencyRecord("k", "f", IdempotencyStatus.COMPLETED, expires_at=0.0)
    assert r4.is_expired() is False


@pytest.mark.asyncio
async def test_redis_backend_expired_record_cleanup() -> None:
    """Test RedisBackend get_record cleaning up expired key."""

    # Test with mock redis client
    class MockRedis:
        def __init__(self) -> None:
            self.data: dict[str, Any] = {}

        async def get(self, key: str) -> Any:
            return self.data.get(key)

        async def delete(self, *keys: str) -> None:
            for k in keys:
                self.data.pop(k, None)

        async def close(self) -> None:
            pass

    mock = MockRedis()
    backend = RedisBackend(redis=mock)

    # Insert expired record into mock
    expired_rec = IdempotencyRecord(
        "exp_k", "fp", IdempotencyStatus.COMPLETED, expires_at=time.time() - 10
    )
    import json

    mock.data["idempotency:exp_k"] = json.dumps(expired_rec.to_dict()).encode("utf-8")

    rec = await backend.get_record("exp_k")
    assert rec is None
    assert "idempotency:exp_k" not in mock.data

    await backend.close()


@pytest.mark.asyncio
async def test_base_backend_close_default() -> None:
    """BaseIdempotencyBackend.close() default no-op implementation."""
    backend = MemoryBackend()
    await super(MemoryBackend, backend).close()


@pytest.mark.asyncio
async def test_redis_backend_initialization_options_and_bytes_eval() -> None:
    """RedisBackend url initialization, default client initialization, and bytes in eval response."""
    b1 = RedisBackend(redis_url="redis://localhost:6379/1")
    assert b1._owned_client is True
    await b1.close()

    b2 = RedisBackend()
    assert b2._owned_client is True
    await b2.close()

    # Test _try_lock_once when redis returns bytes instead of str in res[1]
    class MockRedisBytes:
        async def eval(self, script: str, numkeys: int, *args: Any) -> list[Any]:
            import json

            rec = IdempotencyRecord(
                "k", "fp", IdempotencyStatus.IN_PROGRESS, expires_at=time.time() + 60
            )
            return [0, json.dumps(rec.to_dict()).encode("utf-8")]

        async def close(self) -> None:
            pass

    b3 = RedisBackend(redis=MockRedisBytes())
    locked, rec = await b3._try_lock_once("k", "fp", ttl=60)
    assert locked is False
    assert rec is not None
    assert rec.status == IdempotencyStatus.IN_PROGRESS


@pytest.mark.asyncio
async def test_status_non_str_status_val() -> None:
    """IdempotencyRecord.from_dict when status is not a string."""
    rec = IdempotencyRecord.from_dict(
        {
            "key": "k",
            "fingerprint": "f",
            "status": 999,
        }
    )
    assert rec.status == cast(Any, 999)


@pytest.mark.asyncio
async def test_sqlite_backend_non_bytes_body_conversion() -> None:
    """SQLiteBackend _row_to_record with non-bytes body (bytearray)."""
    backend = SQLiteBackend(":memory:")
    fake_row = {
        "key": "k",
        "fingerprint": "fp",
        "status": "completed",
        "status_code": 200,
        "headers": "[]",
        "body": bytearray(b"bytearray_body"),
        "created_at": time.time(),
        "expires_at": time.time() + 60,
    }
    rec = backend._row_to_record(fake_row)
    assert isinstance(rec.body, bytes)
    assert rec.body == b"bytearray_body"
    await backend.close()
