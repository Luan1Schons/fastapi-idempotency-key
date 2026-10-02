from __future__ import annotations

import time
import pytest

from fastapi_idempotency_key.status import IdempotencyRecord, IdempotencyStatus


def test_status_values() -> None:
    assert IdempotencyStatus.IN_PROGRESS.value == "in_progress"
    assert IdempotencyStatus.COMPLETED.value == "completed"
    assert IdempotencyStatus.FAILED.value == "failed"


def test_record_expiration() -> None:
    now = time.time()
    # No expiration
    r1 = IdempotencyRecord(
        key="k1",
        fingerprint="fp1",
        status=IdempotencyStatus.IN_PROGRESS,
        expires_at=0.0,
    )
    assert not r1.is_expired()

    # Expired in past
    r2 = IdempotencyRecord(
        key="k2",
        fingerprint="fp2",
        status=IdempotencyStatus.IN_PROGRESS,
        expires_at=now - 10,
    )
    assert r2.is_expired()

    # Expires in future
    r3 = IdempotencyRecord(
        key="k3",
        fingerprint="fp3",
        status=IdempotencyStatus.IN_PROGRESS,
        expires_at=now + 100,
    )
    assert not r3.is_expired()


def test_record_to_dict_and_from_dict() -> None:
    headers = [("content-type", "application/json"), ("x-custom", "value")]
    body = b'{"hello": "world", "binary": "\x00\x01\x02"}'
    rec = IdempotencyRecord(
        key="idemp_123",
        fingerprint="fp_abc",
        status=IdempotencyStatus.COMPLETED,
        status_code=201,
        headers=headers,
        body=body,
        created_at=1000.0,
        expires_at=2000.0,
    )

    d = rec.to_dict()
    assert d["key"] == "idemp_123"
    assert d["fingerprint"] == "fp_abc"
    assert d["status"] == "completed"
    assert d["status_code"] == 201
    assert d["headers"] == headers
    assert isinstance(d["body"], str)

    restored = IdempotencyRecord.from_dict(d)
    assert restored.key == rec.key
    assert restored.fingerprint == rec.fingerprint
    assert restored.status == IdempotencyStatus.COMPLETED
    assert restored.status_code == 201
    assert restored.headers == headers
    assert restored.body == body
    assert restored.created_at == 1000.0
    assert restored.expires_at == 2000.0
