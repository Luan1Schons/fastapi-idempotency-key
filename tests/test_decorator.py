from __future__ import annotations

import asyncio
from typing import Any
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
import httpx
import pytest

from fastapi_idempotency_key import IdempotencyMiddleware, MemoryBackend, idempotent


@pytest.mark.asyncio
async def test_decorator_without_request_param() -> None:
    app = FastAPI()
    backend = MemoryBackend()
    calls = 0

    @app.post("/endpoint-no-req")
    @idempotent(backend=backend)
    async def no_req_endpoint(data: dict[str, Any]) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        return {"result": data, "calls": calls}

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        # First call
        r1 = await client.post(
            "/endpoint-no-req", json={"val": 10}, headers={"Idempotency-Key": "dec_k1"}
        )
        assert r1.status_code == 200
        assert r1.json()["calls"] == 1

        # Second call with same key
        r2 = await client.post(
            "/endpoint-no-req", json={"val": 10}, headers={"Idempotency-Key": "dec_k1"}
        )
        assert r2.status_code == 200
        assert r2.json()["calls"] == 1
        assert r2.headers.get("Idempotency-Replayed") == "true"
        assert calls == 1


@pytest.mark.asyncio
async def test_decorator_with_request_param() -> None:
    app = FastAPI()
    backend = MemoryBackend()
    calls = 0

    @app.post("/endpoint-with-req")
    @idempotent(backend=backend)
    async def with_req_endpoint(
        data: dict[str, Any], request: Request
    ) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        return {"method": request.method, "data": data, "calls": calls}

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        r1 = await client.post(
            "/endpoint-with-req",
            json={"val": 20},
            headers={"Idempotency-Key": "dec_k2"},
        )
        assert r1.status_code == 200
        assert r1.json()["calls"] == 1

        r2 = await client.post(
            "/endpoint-with-req",
            json={"val": 20},
            headers={"Idempotency-Key": "dec_k2"},
        )
        assert r2.status_code == 200
        assert r2.json()["calls"] == 1
        assert r2.headers.get("Idempotency-Replayed") == "true"
        assert calls == 1


@pytest.mark.asyncio
async def test_decorator_required_missing_header() -> None:
    app = FastAPI()
    backend = MemoryBackend()

    @app.post("/required-key")
    @idempotent(backend=backend, required=True)
    async def req_key_endpoint() -> dict[str, str]:
        return {"status": "ok"}

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        r = await client.post("/required-key", json={})
        assert r.status_code == 400
        assert "Idempotency-Key header is required" in r.json()["detail"]


@pytest.mark.asyncio
async def test_decorator_conflict_and_mismatch() -> None:
    app = FastAPI()
    backend = MemoryBackend()

    @app.post("/data")
    @idempotent(backend=backend)
    async def data_endpoint(payload: dict[str, Any]) -> dict[str, Any]:
        return {"echo": payload}

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        r1 = await client.post(
            "/data", json={"x": 1}, headers={"Idempotency-Key": "key_dm"}
        )
        assert r1.status_code == 200

        # Mismatch payload
        r2 = await client.post(
            "/data", json={"x": 2}, headers={"Idempotency-Key": "key_dm"}
        )
        assert r2.status_code == 422
        assert "different request payload" in r2.json()["detail"]


@pytest.mark.asyncio
async def test_decorator_with_middleware_cooperation() -> None:
    app = FastAPI()
    backend = MemoryBackend()
    # Add middleware
    app.add_middleware(IdempotencyMiddleware, backend=backend)

    calls = 0

    @app.post("/coop")
    @idempotent(backend=backend, required=True)
    async def coop_endpoint() -> dict[str, int]:
        nonlocal calls
        calls += 1
        return {"calls": calls}

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        r1 = await client.post(
            "/coop", json={}, headers={"Idempotency-Key": "coop_key"}
        )
        assert r1.status_code == 200
        assert r1.json()["calls"] == 1

        r2 = await client.post(
            "/coop", json={}, headers={"Idempotency-Key": "coop_key"}
        )
        assert r2.status_code == 200
        assert r2.json()["calls"] == 1
        assert r2.headers.get("Idempotency-Replayed") == "true"
        assert calls == 1
