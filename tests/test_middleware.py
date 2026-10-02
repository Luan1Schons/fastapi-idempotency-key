from __future__ import annotations

import asyncio
from typing import Any
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse
import httpx
import pytest

from fastapi_idempotency_key import IdempotencyMiddleware, MemoryBackend


@pytest.mark.asyncio
async def test_middleware_replays_cached_response() -> None:
    app = FastAPI()
    backend = MemoryBackend()
    app.add_middleware(IdempotencyMiddleware, backend=backend)

    call_count = 0

    @app.post("/items")
    async def create_item(payload: dict[str, Any]) -> dict[str, Any]:
        nonlocal call_count
        call_count += 1
        return {"id": call_count, "data": payload}

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        # First request
        res1 = await client.post(
            "/items", json={"name": "Book"}, headers={"Idempotency-Key": "key_1"}
        )
        assert res1.status_code == 200
        assert res1.json() == {"id": 1, "data": {"name": "Book"}}
        assert "Idempotency-Replayed" not in res1.headers
        assert call_count == 1

        # Second request with identical payload
        res2 = await client.post(
            "/items", json={"name": "Book"}, headers={"Idempotency-Key": "key_1"}
        )
        assert res2.status_code == 200
        assert res2.json() == {"id": 1, "data": {"name": "Book"}}
        assert res2.headers.get("Idempotency-Replayed") == "true"
        # Handler must not have been executed a second time
        assert call_count == 1


@pytest.mark.asyncio
async def test_middleware_payload_mismatch_returns_422() -> None:
    app = FastAPI()
    backend = MemoryBackend()
    app.add_middleware(IdempotencyMiddleware, backend=backend)

    @app.post("/items")
    async def create_item(payload: dict[str, Any]) -> dict[str, Any]:
        return {"status": "created", "name": payload["name"]}

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        # Initial request
        res1 = await client.post(
            "/items",
            json={"name": "First"},
            headers={"Idempotency-Key": "key_mismatch"},
        )
        assert res1.status_code == 200

        # Request with same key but different body
        res2 = await client.post(
            "/items",
            json={"name": "Different"},
            headers={"Idempotency-Key": "key_mismatch"},
        )
        assert res2.status_code == 422
        assert "different request payload" in res2.json()["detail"]


@pytest.mark.asyncio
async def test_middleware_conflict_returns_409() -> None:
    app = FastAPI()
    backend = MemoryBackend()
    app.add_middleware(IdempotencyMiddleware, backend=backend)

    entered_event = asyncio.Event()
    release_event = asyncio.Event()

    @app.post("/slow")
    async def slow_endpoint() -> dict[str, str]:
        entered_event.set()
        await release_event.wait()
        return {"status": "finished"}

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        task1 = asyncio.create_task(
            client.post("/slow", json={}, headers={"Idempotency-Key": "concurrent_key"})
        )
        await entered_event.wait()

        # Concurrent request with same key
        res2 = await client.post(
            "/slow", json={}, headers={"Idempotency-Key": "concurrent_key"}
        )
        assert res2.status_code == 409
        assert "in progress" in res2.json()["detail"]

        # Release first request
        release_event.set()
        res1 = await task1
        assert res1.status_code == 200


@pytest.mark.asyncio
async def test_middleware_server_error_releases_lock() -> None:
    app = FastAPI()
    backend = MemoryBackend()
    app.add_middleware(IdempotencyMiddleware, backend=backend)

    attempt = 0

    @app.post("/flaky")
    async def flaky_endpoint() -> dict[str, str]:
        nonlocal attempt
        attempt += 1
        if attempt == 1:
            raise HTTPException(status_code=500, detail="Temporary DB outage")
        return {"status": "success"}

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        # First attempt fails with 500
        res1 = await client.post(
            "/flaky", json={}, headers={"Idempotency-Key": "flaky_key"}
        )
        assert res1.status_code == 500

        # Lock should have been released, allowing client to retry with the same key
        res2 = await client.post(
            "/flaky", json={}, headers={"Idempotency-Key": "flaky_key"}
        )
        assert res2.status_code == 200
        assert res2.json() == {"status": "success"}


@pytest.mark.asyncio
async def test_middleware_passthrough_safe_methods_and_missing_key() -> None:
    app = FastAPI()
    backend = MemoryBackend()
    app.add_middleware(IdempotencyMiddleware, backend=backend)

    @app.get("/items")
    async def get_items() -> dict[str, str]:
        return {"type": "get"}

    @app.post("/no-key")
    async def no_key() -> dict[str, str]:
        return {"type": "no-key"}

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        # GET with header passes through without error
        r_get = await client.get("/items", headers={"Idempotency-Key": "get_key"})
        assert r_get.status_code == 200
        assert "Idempotency-Replayed" not in r_get.headers

        # POST without header passes through
        r_post = await client.post("/no-key", json={})
        assert r_post.status_code == 200
        assert "Idempotency-Replayed" not in r_post.headers


@pytest.mark.asyncio
async def test_middleware_required_flag() -> None:
    app = FastAPI()
    backend = MemoryBackend()
    app.add_middleware(IdempotencyMiddleware, backend=backend, required=True)

    @app.post("/strict")
    async def strict_endpoint() -> dict[str, str]:
        return {"ok": "true"}

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        # Missing header returns 400
        r = await client.post("/strict", json={})
        assert r.status_code == 400
        assert "Idempotency-Key header is required" in r.json()["detail"]

        # Present header succeeds
        r2 = await client.post(
            "/strict", json={}, headers={"Idempotency-Key": "valid_key"}
        )
        assert r2.status_code == 200
