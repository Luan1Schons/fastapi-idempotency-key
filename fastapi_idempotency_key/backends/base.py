from __future__ import annotations

from abc import ABC, abstractmethod
import asyncio
import time
from typing import Any, List, Optional, Tuple

from fastapi_idempotency_key.status import IdempotencyRecord, IdempotencyStatus


class BaseIdempotencyBackend(ABC):
    """Abstract base class for all idempotency storage backends."""

    @abstractmethod
    async def _try_lock_once(
        self, key: str, fingerprint: str, ttl: int
    ) -> Tuple[bool, Optional[IdempotencyRecord]]:
        """Atomically attempts to acquire the lock once.

        Returns:
            (True, None) if lock was acquired (record created with IN_PROGRESS).
            (False, existing_record) if key already exists.
        """
        pass

    async def try_lock(
        self, key: str, fingerprint: str, ttl: int, timeout: float = 0.0
    ) -> Tuple[bool, Optional[IdempotencyRecord]]:
        """Atomically acquires the lock or waits up to timeout seconds.

        If key doesn't exist or is expired: atomically creates lock with
        status IN_PROGRESS and returns (True, None).

        If key exists and is valid:
        - If status is COMPLETED, returns (False, existing_record) immediately.
        - If status is IN_PROGRESS and timeout > 0, waits for completion or release.
        - If timeout expires, returns (False, existing_record).
        """
        if timeout <= 0.0:
            return await self._try_lock_once(key, fingerprint, ttl)

        start_time = time.monotonic()
        poll_interval = 0.05
        while True:
            locked, record = await self._try_lock_once(key, fingerprint, ttl)
            if locked:
                return True, None

            if record is not None and record.status == IdempotencyStatus.COMPLETED:
                return False, record

            elapsed = time.monotonic() - start_time
            if elapsed >= timeout:
                return False, record

            sleep_time = min(poll_interval, timeout - elapsed)
            if sleep_time > 0:
                await asyncio.sleep(sleep_time)

    @abstractmethod
    async def store_response(
        self,
        key: str,
        status_code: int,
        headers: List[Tuple[str, str]],
        body: bytes,
        ttl: int,
    ) -> None:
        """Stores the response and transitions record status to COMPLETED."""
        pass

    @abstractmethod
    async def release_lock(self, key: str) -> None:
        """Releases or deletes the lock so the client can retry."""
        pass

    @abstractmethod
    async def get_record(self, key: str) -> Optional[IdempotencyRecord]:
        """Retrieves the record for a key, or None if expired or not found."""
        pass

    @abstractmethod
    async def clear(self) -> None:
        """Clears all records stored in this backend."""
        pass

    async def close(self) -> None:
        """Closes any underlying resources (connections, pools). Default is no-op."""
        pass

    async def __aenter__(self) -> BaseIdempotencyBackend:
        return self

    async def __aexit__(
        self,
        exc_type: Optional[type[BaseException]],
        exc_val: Optional[BaseException],
        exc_tb: Optional[Any],
    ) -> None:
        await self.close()
