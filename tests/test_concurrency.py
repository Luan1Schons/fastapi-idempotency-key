from __future__ import annotations

import asyncio
from pathlib import Path
import tempfile
from typing import Any, List
from fastapi import FastAPI
import httpx
import pytest

from fastapi_idempotency_key import (
    IdempotencyMiddleware,
    MemoryBackend,
    SQLiteBackend,
    idempotent,
)


@pytest.mark.asyncio
async def test_concurrency_memory_backend_10_requests() -> None:
    """Test 10 concurrent requests hitting the middleware with the exact same

    Idempotency-Key at the exact same time via asyncio.gather().

    Verify that EXACTLY ONE request executes the route handler and returns
    201, while the other 9 concurrent requests receive HTTP 409 Conflict.
    Verify that subsequent request replays the cached 201 response.
    """
    app = FastAPI()
    backend = MemoryBackend()
    app.add_middleware(IdempotencyMiddleware, backend=backend, timeout=0.0)

    handler_executions = 0
    barrier = asyncio.Event()

    @app.post("/orders")
    async def create_order(payload: dict[str, Any]) -> dict[str, Any]:
        nonlocal handler_executions
        handler_executions += 1
        # Brief pause to simulate processing time and ensure concurrency collision
        await asyncio.sleep(0.08)
        return {"order_id": 1001, "status": "confirmed", "item": payload["item"]}

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        # Launch 10 concurrent requests with exact same Idempotency-Key and payload
        headers = {"Idempotency-Key": "concurrent_order_key_1"}
        payload = {"item": "Quantum Laptop", "amount": 1999}

        tasks = [
            client.post("/orders", json=payload, headers=headers) for _ in range(10)
        ]
        responses = await asyncio.gather(*tasks)

        success_responses = [r for r in responses if r.status_code == 200]
        conflict_responses = [r for r in responses if r.status_code == 409]

        assert (
            len(success_responses) == 1
        ), f"Expected exactly 1 success response, got {len(success_responses)}"
        assert (
            len(conflict_responses) == 9
        ), f"Expected exactly 9 conflict responses, got {len(conflict_responses)}"
        assert (
            handler_executions == 1
        ), f"Handler must execute exactly once, but executed {handler_executions} times"

        success_res = success_responses[0]
        assert success_res.json() == {
            "order_id": 1001,
            "status": "confirmed",
            "item": "Quantum Laptop",
        }
        assert "Idempotency-Replayed" not in success_res.headers

        for conf_res in conflict_responses:
            assert "in progress" in conf_res.json()["detail"]

        # Subsequent request after completion must replay the cached response
        replay_res = await client.post("/orders", json=payload, headers=headers)
        assert replay_res.status_code == 200
        assert replay_res.json() == {
            "order_id": 1001,
            "status": "confirmed",
            "item": "Quantum Laptop",
        }
        assert replay_res.headers.get("Idempotency-Replayed") == "true"
        # Handler must still have only executed once
        assert handler_executions == 1


@pytest.mark.asyncio
async def test_concurrency_sqlite_backend_10_requests() -> None:
    """Test 10 concurrent requests with SQLiteBackend (using disk-based WAL file).

    Verify that EXACTLY ONE request executes the route handler and returns
    201, while the other 9 receive HTTP 409 Conflict.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "concurrent_idempotency.db"
        backend = SQLiteBackend(db_path=str(db_path))

        app = FastAPI()
        app.add_middleware(IdempotencyMiddleware, backend=backend, timeout=0.0)

        handler_executions = 0

        @app.post("/payments", status_code=201)
        async def process_payment(payload: dict[str, Any]) -> dict[str, Any]:
            nonlocal handler_executions
            handler_executions += 1
            await asyncio.sleep(0.08)
            return {"payment_id": "pay_999", "status": "succeeded"}

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            headers = {"Idempotency-Key": "concurrent_sqlite_key"}
            payload = {"amount": 5000, "currency": "usd"}

            tasks = [
                client.post("/payments", json=payload, headers=headers)
                for _ in range(10)
            ]
            responses = await asyncio.gather(*tasks)

            success_responses = [r for r in responses if r.status_code == 201]
            conflict_responses = [r for r in responses if r.status_code == 409]

            assert (
                len(success_responses) == 1
            ), f"Expected exactly 1 success response, got {len(success_responses)}"
            assert (
                len(conflict_responses) == 9
            ), f"Expected exactly 9 conflict responses, got {len(conflict_responses)}"
            assert handler_executions == 1

            assert success_responses[0].json() == {
                "payment_id": "pay_999",
                "status": "succeeded",
            }
            assert "Idempotency-Replayed" not in success_responses[0].headers

            for conf_res in conflict_responses:
                assert "in progress" in conf_res.json()["detail"]

            # Replay after completion
            replay_res = await client.post("/payments", json=payload, headers=headers)
            assert replay_res.status_code == 201
            assert replay_res.json() == {
                "payment_id": "pay_999",
                "status": "succeeded",
            }
            assert replay_res.headers.get("Idempotency-Replayed") == "true"
            assert handler_executions == 1

        await backend.close()


@pytest.mark.asyncio
async def test_concurrency_with_timeout_waiting_replays_all() -> None:
    """Test 10 concurrent requests when middleware timeout > 0.

    When timeout is enabled, concurrent requests wait for the in-progress
    request to complete and then automatically replay the cached response.
    Result: All 10 requests return 200, exactly 1 executes handler, 9 are
    marked Idempotency-Replayed: true.
    """
    app = FastAPI()
    backend = MemoryBackend()
    app.add_middleware(IdempotencyMiddleware, backend=backend, timeout=1.0)

    handler_executions = 0

    @app.post("/checkout")
    async def checkout(payload: dict[str, Any]) -> dict[str, Any]:
        nonlocal handler_executions
        handler_executions += 1
        await asyncio.sleep(0.06)
        return {"invoice_id": "inv_777", "total": payload["total"]}

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        headers = {"Idempotency-Key": "timeout_key_1"}
        payload = {"total": 42.50}

        tasks = [
            client.post("/checkout", json=payload, headers=headers) for _ in range(10)
        ]
        responses = await asyncio.gather(*tasks)

        assert all(r.status_code == 200 for r in responses)
        assert handler_executions == 1

        original_responses = [
            r for r in responses if "Idempotency-Replayed" not in r.headers
        ]
        replayed_responses = [
            r for r in responses if r.headers.get("Idempotency-Replayed") == "true"
        ]

        assert len(original_responses) == 1
        assert len(replayed_responses) == 9

        for r in responses:
            assert r.json() == {"invoice_id": "inv_777", "total": 42.50}


@pytest.mark.asyncio
async def test_concurrency_route_decorator() -> None:
    """Test 10 concurrent requests on route-level @idempotent decorator."""
    app = FastAPI()
    backend = MemoryBackend()
    handler_executions = 0

    @app.post("/decorated-orders")
    @idempotent(backend=backend, timeout=0.0)
    async def create_decorated_order(payload: dict[str, Any]) -> dict[str, Any]:
        nonlocal handler_executions
        handler_executions += 1
        await asyncio.sleep(0.06)
        return {"status": "ok", "order": payload["order"]}

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        headers = {"Idempotency-Key": "dec_concurrent_key"}
        payload = {"order": 55}

        tasks = [
            client.post("/decorated-orders", json=payload, headers=headers)
            for _ in range(10)
        ]
        responses = await asyncio.gather(*tasks)

        success = [r for r in responses if r.status_code == 200]
        conflicts = [r for r in responses if r.status_code == 409]

        assert len(success) == 1
        assert len(conflicts) == 9
        assert handler_executions == 1

        # Replay
        replay = await client.post("/decorated-orders", json=payload, headers=headers)
        assert replay.status_code == 200
        assert replay.headers.get("Idempotency-Replayed") == "true"
        assert handler_executions == 1
