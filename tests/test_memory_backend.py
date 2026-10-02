from __future__ import annotations

import asyncio
import time
import pytest

from fastapi_idempotency_key.backends.memory import MemoryBackend
from fastapi_idempotency_key.status import IdempotencyStatus


@pytest.mark.asyncio
async def test_memory_backend_lock_and_store() -> None:
    backend = MemoryBackend()

    # Lock acquisition
    locked, rec = await backend.try_lock("key1", "fp1", ttl=60)
    assert locked is True
    assert rec is None

    # Key in progress
    locked2, rec2 = await backend.try_lock("key1", "fp1", ttl=60)
    assert locked2 is False
    assert rec2 is not None
    assert rec2.status == IdempotencyStatus.IN_PROGRESS
    assert rec2.fingerprint == "fp1"

    # Store response
    await backend.store_response(
        "key1",
        status_code=200,
        headers=[("content-type", "application/json")],
        body=b'{"ok": true}',
        ttl=60,
    )

    # Subsequent lock attempt returns completed
    locked3, rec3 = await backend.try_lock("key1", "fp1", ttl=60)
    assert locked3 is False
    assert rec3 is not None
    assert rec3.status == IdempotencyStatus.COMPLETED
    assert rec3.status_code == 200
    assert rec3.body == b'{"ok": true}'


@pytest.mark.asyncio
async def test_memory_backend_release_lock() -> None:
    backend = MemoryBackend()
    locked, _ = await backend.try_lock("key1", "fp1", ttl=60)
    assert locked is True

    await backend.release_lock("key1")

    # Lock can now be re-acquired
    locked2, rec2 = await backend.try_lock("key1", "fp2", ttl=60)
    assert locked2 is True
    assert rec2 is None


@pytest.mark.asyncio
async def test_memory_backend_ttl_expiration() -> None:
    backend = MemoryBackend()
    # Acquire with 1s TTL
    locked, _ = await backend.try_lock("key_exp", "fp", ttl=1)
    assert locked is True

    # Manually backdate expiration for fast test
    rec = await backend.get_record("key_exp")
    assert rec is not None
    rec.expires_at = time.time() - 1
    backend._store["key_exp"] = rec

    # Record should now be considered expired
    fetched = await backend.get_record("key_exp")
    assert fetched is None

    # Re-locking acquires lock
    locked2, _ = await backend.try_lock("key_exp", "fp_new", ttl=60)
    assert locked2 is True


@pytest.mark.asyncio
async def test_memory_backend_lru_eviction() -> None:
    backend = MemoryBackend(max_keys=2)
    await backend.try_lock("k1", "fp1", ttl=60)
    await backend.try_lock("k2", "fp2", ttl=60)

    # Access k1 so k2 is older
    await backend.get_record("k1")

    # Add k3, k2 should be evicted
    await backend.try_lock("k3", "fp3", ttl=60)

    assert await backend.get_record("k2") is None
    assert await backend.get_record("k1") is not None
    assert await backend.get_record("k3") is not None


@pytest.mark.asyncio
async def test_memory_backend_timeout_wait() -> None:
    backend = MemoryBackend()
    await backend.try_lock("k_wait", "fp", ttl=60)

    async def complete_after_delay() -> None:
        await asyncio.sleep(0.05)
        await backend.store_response("k_wait", 200, [], b"done", ttl=60)

    asyncio.create_task(complete_after_delay())

    # Should wait and return completed record
    locked, rec = await backend.try_lock("k_wait", "fp", ttl=60, timeout=0.3)
    assert locked is False
    assert rec is not None
    assert rec.status == IdempotencyStatus.COMPLETED
    assert rec.body == b"done"


@pytest.mark.asyncio
async def test_memory_backend_clear_and_close() -> None:
    backend = MemoryBackend()
    await backend.try_lock("k1", "fp1", ttl=60)
    await backend.clear()
    assert await backend.get_record("k1") is None
    await backend.close()
