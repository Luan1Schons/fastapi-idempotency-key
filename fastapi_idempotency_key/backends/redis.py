from __future__ import annotations

import asyncio
import json
import time
from typing import Any, List, Optional, Tuple

try:
    import redis.asyncio as aioredis
except ImportError:  # pragma: no cover
    aioredis = None  # type: ignore[assignment]

from fastapi_idempotency_key.backends.base import BaseIdempotencyBackend
from fastapi_idempotency_key.status import IdempotencyRecord, IdempotencyStatus


class RedisBackend(BaseIdempotencyBackend):
    """Redis-backed idempotency storage using redis.asyncio and atomic Lua scripts."""

    LUA_TRY_LOCK = """
    local key = KEYS[1]
    local val = ARGV[1]
    local ttl = tonumber(ARGV[2])

    local existing = redis.call('GET', key)
    if not existing then
        if ttl and ttl > 0 then
            redis.call('SET', key, val, 'EX', ttl)
        else
            redis.call('SET', key, val)
        end
        return {1, nil}
    else
        return {0, existing}
    end
    """

    LUA_STORE_RESPONSE = """
    local key = KEYS[1]
    local val = ARGV[1]
    local ttl = tonumber(ARGV[2])

    if ttl and ttl > 0 then
        redis.call('SET', key, val, 'EX', ttl)
    else
        redis.call('SET', key, val)
    end
    return 1
    """

    def __init__(
        self,
        redis: Optional[Any] = None,
        redis_url: Optional[str] = None,
        prefix: str = "idempotency:",
        **redis_kwargs: Any,
    ) -> None:
        if aioredis is None and redis is None:
            raise ImportError(
                "The 'redis' package is required to use RedisBackend. "
                "Install it with: pip install 'fastapi-idempotency-key[redis]'"
            )

        self.prefix = prefix
        self._owned_client = False

        if redis is not None:
            self.redis = redis
        elif redis_url is not None:
            self.redis = aioredis.from_url(redis_url, **redis_kwargs)
            self._owned_client = True
        else:
            self.redis = aioredis.Redis(**redis_kwargs)
            self._owned_client = True

    def _get_key(self, key: str) -> str:
        return f"{self.prefix}{key}"

    async def _try_lock_once(
        self, key: str, fingerprint: str, ttl: int
    ) -> Tuple[bool, Optional[IdempotencyRecord]]:
        redis_key = self._get_key(key)
        now = time.time()
        expires_at = now + ttl if ttl > 0 else 0.0

        new_record = IdempotencyRecord(
            key=key,
            fingerprint=fingerprint,
            status=IdempotencyStatus.IN_PROGRESS,
            status_code=None,
            headers=[],
            body=b"",
            created_at=now,
            expires_at=expires_at,
        )
        payload = json.dumps(new_record.to_dict())

        res = await self.redis.eval(self.LUA_TRY_LOCK, 1, redis_key, payload, ttl)
        is_locked = bool(res[0])
        if is_locked:
            return True, None

        raw_existing = res[1]
        if isinstance(raw_existing, bytes):
            raw_existing = raw_existing.decode("utf-8")
        data = json.loads(raw_existing)
        existing_record = IdempotencyRecord.from_dict(data)
        return False, existing_record

    async def store_response(
        self,
        key: str,
        status_code: int,
        headers: List[Tuple[str, str]],
        body: bytes,
        ttl: int,
    ) -> None:
        redis_key = self._get_key(key)
        now = time.time()
        expires_at = now + ttl if ttl > 0 else 0.0

        record = IdempotencyRecord(
            key=key,
            fingerprint="",
            status=IdempotencyStatus.COMPLETED,
            status_code=status_code,
            headers=list(headers),
            body=body,
            created_at=now,
            expires_at=expires_at,
        )
        payload = json.dumps(record.to_dict())
        await self.redis.eval(self.LUA_STORE_RESPONSE, 1, redis_key, payload, ttl)

    async def release_lock(self, key: str) -> None:
        redis_key = self._get_key(key)
        await self.redis.delete(redis_key)

    async def get_record(self, key: str) -> Optional[IdempotencyRecord]:
        redis_key = self._get_key(key)
        raw = await self.redis.get(redis_key)
        if not raw:
            return None

        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        data = json.loads(raw)
        record = IdempotencyRecord.from_dict(data)
        if record.is_expired():
            await self.redis.delete(redis_key)
            return None
        return record

    async def clear(self) -> None:
        pattern = f"{self.prefix}*"
        cur = 0
        while True:
            cur, keys = await self.redis.scan(cursor=cur, match=pattern, count=100)
            if keys:
                await self.redis.delete(*keys)
            if cur == 0:
                break

    async def close(self) -> None:
        close_fn = getattr(self.redis, "aclose", None) or getattr(
            self.redis, "close", None
        )
        if close_fn:
            res = close_fn()
            if asyncio.iscoroutine(res):
                await res
