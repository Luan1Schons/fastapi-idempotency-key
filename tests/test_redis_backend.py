from __future__ import annotations

import json
import pytest

from fastapi_idempotency_key.backends.redis import RedisBackend
from fastapi_idempotency_key.status import IdempotencyRecord, IdempotencyStatus


class FakeAsyncRedis:
    def __init__(self) -> None:
        self.store: dict[str, str] = {}
        self.closed = False

    async def eval(self, script: str, numkeys: int, *args: str) -> list[object]:
        key = args[0]
        val = args[1]
        ttl = int(args[2])

        if "if not existing then" in script:
            # LUA_TRY_LOCK
            existing = self.store.get(key)
            if existing is None:
                self.store[key] = val
                return [1, None]
            else:
                return [0, existing]
        elif "LUA_STORE_RESPONSE" in script or "SET" in script:
            # LUA_STORE_RESPONSE
            self.store[key] = val
            return [1]
        return [0]

    async def get(self, key: str) -> str | None:
        return self.store.get(key)

    async def delete(self, *keys: str) -> int:
        count = 0
        for k in keys:
            if k in self.store:
                del self.store[k]
                count += 1
        return count

    async def scan(
        self, cursor: int = 0, match: str | None = None, count: int = 100
    ) -> tuple[int, list[str]]:
        keys = list(self.store.keys())
        if match:
            prefix = match.rstrip("*")
            keys = [k for k in keys if k.startswith(prefix)]
        return 0, keys

    async def aclose(self) -> None:
        self.closed = True

    async def close(self) -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_redis_backend_try_lock_and_store() -> None:
    fake_client = FakeAsyncRedis()
    backend = RedisBackend(redis=fake_client, prefix="test:")

    # Initial lock
    locked, rec = await backend.try_lock("redis_k1", "fp_1", ttl=60)
    assert locked is True
    assert rec is None

    # Key in progress
    locked2, rec2 = await backend.try_lock("redis_k1", "fp_1", ttl=60)
    assert locked2 is False
    assert rec2 is not None
    assert rec2.status == IdempotencyStatus.IN_PROGRESS
    assert rec2.fingerprint == "fp_1"

    # Store response
    await backend.store_response(
        "redis_k1",
        status_code=200,
        headers=[("content-type", "text/plain")],
        body=b"redis response",
        ttl=60,
    )

    # Subsequent lock attempt returns completed
    locked3, rec3 = await backend.try_lock("redis_k1", "fp_1", ttl=60)
    assert locked3 is False
    assert rec3 is not None
    assert rec3.status == IdempotencyStatus.COMPLETED
    assert rec3.status_code == 200
    assert rec3.body == b"redis response"

    # Get record
    rec4 = await backend.get_record("redis_k1")
    assert rec4 is not None
    assert rec4.body == b"redis response"

    # Release lock
    await backend.release_lock("redis_k1")
    assert await backend.get_record("redis_k1") is None

    # Clear
    await backend.try_lock("k2", "fp2", ttl=60)
    await backend.clear()
    assert await backend.get_record("k2") is None

    await backend.close()
    assert fake_client.closed is True
