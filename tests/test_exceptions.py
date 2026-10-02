from __future__ import annotations

import pytest

from fastapi_idempotency_key.exceptions import (
    IdempotencyConflictError,
    IdempotencyError,
    IdempotencyKeyMissingError,
    IdempotencyPayloadMismatchError,
    IdempotencyStorageError,
)


def test_exception_hierarchy() -> None:
    assert issubclass(IdempotencyError, Exception)
    assert issubclass(IdempotencyKeyMissingError, IdempotencyError)
    assert issubclass(IdempotencyConflictError, IdempotencyError)
    assert issubclass(IdempotencyPayloadMismatchError, IdempotencyError)
    assert issubclass(IdempotencyStorageError, IdempotencyError)


def test_exception_status_codes_and_defaults() -> None:
    err_missing = IdempotencyKeyMissingError()
    assert err_missing.status_code == 400
    assert "required" in err_missing.detail

    err_conflict = IdempotencyConflictError()
    assert err_conflict.status_code == 409
    assert "in progress" in err_conflict.detail

    err_mismatch = IdempotencyPayloadMismatchError()
    assert err_mismatch.status_code == 422
    assert "payload" in err_mismatch.detail

    err_storage = IdempotencyStorageError("DB broke")
    assert err_storage.status_code == 500
    assert err_storage.detail == "DB broke"
