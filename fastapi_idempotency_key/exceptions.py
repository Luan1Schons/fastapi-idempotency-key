from __future__ import annotations

from typing import Optional

from starlette.exceptions import HTTPException


class IdempotencyError(HTTPException):
    """Base exception for all idempotency-related errors."""

    status_code: int = 500
    detail: str = "Idempotency error."

    def __init__(
        self,
        detail: Optional[str] = None,
        status_code: Optional[int] = None,
        headers: Optional[dict[str, str]] = None,
    ) -> None:
        code = status_code if status_code is not None else self.status_code
        msg = detail if detail is not None else self.detail
        super().__init__(status_code=code, detail=msg, headers=headers)
        self.status_code = code
        self.detail = msg


class IdempotencyKeyMissingError(IdempotencyError):
    """Raised when an idempotency key is required but missing from the request (HTTP 400)."""

    status_code = 400
    detail = "Idempotency-Key header is required."


class IdempotencyConflictError(IdempotencyError):
    """Raised when a concurrent request with the same idempotency key is currently in progress (HTTP 409)."""

    status_code = 409
    detail = "A request with this idempotency key is currently in progress."


class IdempotencyPayloadMismatchError(IdempotencyError):
    """Raised when an idempotency key is reused with a different request payload or fingerprint (HTTP 422)."""

    status_code = 422
    detail = "Idempotency key was previously used with a different request payload."


class IdempotencyStorageError(IdempotencyError):
    """Raised when the storage backend fails to perform an operation (HTTP 500)."""

    status_code = 500
    detail = "Idempotency storage operation failed."
