from __future__ import annotations

import asyncio
import tempfile
import time
from pathlib import Path
import pytest

from fastapi_idempotency_key.backends.sqlite import SQLiteBackend
from fastapi_idempotency_key.status import IdempotencyStatus


@pytest.mark.asyncio
async def test_sqlite_backend_memory() -> None:
    backend = SQLiteBackend(":memory:")

    # Initial lock
    locked, rec = await backend.try_lock("sqlite_k1", "fp1", ttl=60)
    assert locked is True
    assert rec is None

    # Duplicate in progress
    locked2, rec2 = await backend.try_lock("sqlite_k1", "fp1", ttl=60)
    assert locked2 is False
    assert rec2 is not None
    assert rec2.status == IdempotencyStatus.IN_PROGRESS

    # Store response
    await backend.store_response(
        "sqlite_k1",
        status_code=201,
        headers=[("content-type", "application/json")],
        body=b'{"id": 42}',
        ttl=60,
    )

    # Fetch completed
    rec3 = await backend.get_record("sqlite_k1")
    assert rec3 is not None
    assert rec3.status == IdempotencyStatus.COMPLETED
    assert rec3.status_code == 201
    assert rec3.body == b'{"id": 42}'
    assert rec3.headers == [("content-type", "application/json")]

    await backend.close()


@pytest.mark.asyncio
async def test_sqlite_backend_file_and_release() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        db_file = Path(tmpdir) / "test_idempotency.db"
        backend = SQLiteBackend(str(db_file))

        locked, _ = await backend.try_lock("file_k1", "fp", ttl=60)
        assert locked is True

        # Release lock
        await backend.release_lock("file_k1")
        assert await backend.get_record("file_k1") is None

        # Re-acquire
        locked2, _ = await backend.try_lock("file_k1", "fp", ttl=60)
        assert locked2 is True

        # Clear
        await backend.clear()
        assert await backend.get_record("file_k1") is None

        await backend.close()


@pytest.mark.asyncio
async def test_sqlite_backend_ttl_expiration() -> None:
    backend = SQLiteBackend(":memory:")
    locked, _ = await backend.try_lock("exp_k", "fp", ttl=1)
    assert locked is True

    # Manually update expires_at in SQLite
    conn = await backend._get_conn()
    await conn.execute(
        "UPDATE idempotency_records SET expires_at = ? WHERE key = ?",
        (time.time() - 10, "exp_k"),
    )
    await conn.commit()

    # Should be expired
    assert await backend.get_record("exp_k") is None

    # Should allow acquiring again
    locked2, _ = await backend.try_lock("exp_k", "fp_new", ttl=60)
    assert locked2 is True

    await backend.close()


@pytest.mark.asyncio
async def test_sqlite_backend_timeout_wait() -> None:
    backend = SQLiteBackend(":memory:")
    await backend.try_lock("k_wait", "fp", ttl=60)

    async def complete_later() -> None:
        await asyncio.sleep(0.05)
        await backend.store_response("k_wait", 200, [], b"sqlite_done", ttl=60)

    asyncio.create_task(complete_later())

    locked, rec = await backend.try_lock("k_wait", "fp", ttl=60, timeout=0.3)
    assert locked is False
    assert rec is not None
    assert rec.status == IdempotencyStatus.COMPLETED
    assert rec.body == b"sqlite_done"

    await backend.close()
