from __future__ import annotations

from fastapi_idempotency_key.backends.base import BaseIdempotencyBackend
from fastapi_idempotency_key.backends.memory import MemoryBackend
from fastapi_idempotency_key.backends.redis import RedisBackend
from fastapi_idempotency_key.backends.sqlite import SQLiteBackend
from fastapi_idempotency_key.decorator import idempotent
from fastapi_idempotency_key.exceptions import (
    IdempotencyConflictError,
    IdempotencyError,
    IdempotencyKeyMissingError,
    IdempotencyPayloadMismatchError,
    IdempotencyStorageError,
)
from fastapi_idempotency_key.fingerprint import calculate_request_fingerprint
from fastapi_idempotency_key.middleware import IdempotencyMiddleware
from fastapi_idempotency_key.status import IdempotencyRecord, IdempotencyStatus

__version__ = "0.1.1"

__all__ = [
    "BaseIdempotencyBackend",
    "IdempotencyConflictError",
    "IdempotencyError",
    "IdempotencyKeyMissingError",
    "IdempotencyMiddleware",
    "IdempotencyPayloadMismatchError",
    "IdempotencyRecord",
    "IdempotencyStatus",
    "IdempotencyStorageError",
    "MemoryBackend",
    "RedisBackend",
    "SQLiteBackend",
    "calculate_request_fingerprint",
    "idempotent",
]
