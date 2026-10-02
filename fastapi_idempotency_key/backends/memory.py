from __future__ import annotations

import asyncio
from collections import OrderedDict
import copy
import time
from typing import List, Optional, Tuple

from fastapi_idempotency_key.backends.base import BaseIdempotencyBackend
from fastapi_idempotency_key.status import IdempotencyRecord, IdempotencyStatus


class MemoryBackend(BaseIdempotencyBackend):
    """Thread-safe and async-safe in-memory idempotency store with TTL and LRU eviction."""

    def __init__(self, max_keys: Optional[int] = None) -> None:
        self.max_keys = max_keys
        self._store: OrderedDict[str, IdempotencyRecord] = OrderedDict()
        self._lock = asyncio.Lock()

    def _purge_expired_locked(self) -> None:
        """Removes all expired records from the store. Must be called while holding self._lock."""
        now = time.time()
        expired_keys = [
            k
            for k, v in self._store.items()
            if v.expires_at > 0.0 and now >= v.expires_at
        ]
        for k in expired_keys:
            del self._store[k]

    def _evict_if_needed_locked(self) -> None:
        """Evicts oldest keys if store exceeds max_keys. Must be called while holding self._lock."""
        if self.max_keys is None or self.max_keys <= 0:
            return

        if len(self._store) >= self.max_keys:
            self._purge_expired_locked()

        while len(self._store) >= self.max_keys:
            self._store.popitem(last=False)

    async def _try_lock_once(
        self, key: str, fingerprint: str, ttl: int
    ) -> Tuple[bool, Optional[IdempotencyRecord]]:
        async with self._lock:
            now = time.time()
            if key in self._store:
                record = self._store[key]
                if record.is_expired():
                    del self._store[key]
                else:
                    self._store.move_to_end(key)
                    return False, copy.deepcopy(record)

            self._evict_if_needed_locked()

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
            self._store[key] = new_record
            return True, None

    async def store_response(
        self,
        key: str,
        status_code: int,
        headers: List[Tuple[str, str]],
        body: bytes,
        ttl: int,
    ) -> None:
        async with self._lock:
            now = time.time()
            expires_at = now + ttl if ttl > 0 else 0.0

            if key in self._store:
                record = self._store[key]
                record.status = IdempotencyStatus.COMPLETED
                record.status_code = status_code
                record.headers = list(headers)
                record.body = body
                record.expires_at = expires_at
                self._store.move_to_end(key)
            else:
                self._evict_if_needed_locked()
                new_record = IdempotencyRecord(
                    key=key,
                    fingerprint="",
                    status=IdempotencyStatus.COMPLETED,
                    status_code=status_code,
                    headers=list(headers),
                    body=body,
                    created_at=now,
                    expires_at=expires_at,
                )
                self._store[key] = new_record

    async def release_lock(self, key: str) -> None:
        async with self._lock:
            if key in self._store:
                del self._store[key]

    async def get_record(self, key: str) -> Optional[IdempotencyRecord]:
        async with self._lock:
            if key in self._store:
                record = self._store[key]
                if record.is_expired():
                    del self._store[key]
                    return None
                self._store.move_to_end(key)
                return copy.deepcopy(record)
            return None

    async def clear(self) -> None:
        async with self._lock:
            self._store.clear()

    async def close(self) -> None:
        await self.clear()
