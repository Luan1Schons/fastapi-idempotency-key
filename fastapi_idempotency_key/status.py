from __future__ import annotations

import base64
from dataclasses import dataclass, field
from enum import Enum
import time
from typing import Any, List, Optional, Tuple


class IdempotencyStatus(str, Enum):
    """Execution status of an idempotent request."""

    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class IdempotencyRecord:
    """Represents a cached idempotency key state and reponse payload."""

    key: str
    fingerprint: str
    status: IdempotencyStatus
    status_code: Optional[int] = None
    headers: List[Tuple[str, str]] = field(default_factory=list)
    body: bytes = b""
    created_at: float = field(default_factory=time.time)
    expires_at: float = 0.0

    def is_expired(self) -> bool:
        """Returns True if the record has an expiration timestamp and it has passed."""
        if self.expires_at <= 0.0:
            return False
        return time.time() >= self.expires_at

    def to_dict(self) -> dict[str, Any]:
        """Serializes the record to a dictionary suitable for JSON storage."""
        return {
            "key": self.key,
            "fingerprint": self.fingerprint,
            "status": (
                self.status.value
                if isinstance(self.status, IdempotencyStatus)
                else str(self.status)
            ),
            "status_code": self.status_code,
            "headers": [(str(k), str(v)) for k, v in self.headers],
            "body": base64.b64encode(self.body).decode("ascii"),
            "created_at": self.created_at,
            "expires_at": self.expires_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> IdempotencyRecord:
        """Deserializes an IdempotencyRecord from a dictionary."""
        body_val = data.get("body", b"")
        if isinstance(body_val, str):
            body_bytes = base64.b64decode(body_val.encode("ascii"))
        elif isinstance(body_val, bytes):
            body_bytes = body_val
        else:
            body_bytes = b""

        status_val = data["status"]
        if isinstance(status_val, str):
            status = IdempotencyStatus(status_val)
        else:
            status = status_val

        raw_headers = data.get("headers", [])
        headers = [(str(k), str(v)) for k, v in raw_headers]

        return cls(
            key=str(data["key"]),
            fingerprint=str(data["fingerprint"]),
            status=status,
            status_code=data.get("status_code"),
            headers=headers,
            body=body_bytes,
            created_at=float(data.get("created_at", time.time())),
            expires_at=float(data.get("expires_at", 0.0)),
        )
