from __future__ import annotations

import pytest

from fastapi_idempotency_key.fingerprint import calculate_request_fingerprint


def test_fingerprint_deterministic() -> None:
    fp1 = calculate_request_fingerprint(
        "POST", "/payments", "amount=100&currency=USD", b'{"user": 1}'
    )
    fp2 = calculate_request_fingerprint(
        "POST", "/payments", "amount=100&currency=USD", b'{"user": 1}'
    )
    assert fp1 == fp2
    assert len(fp1) == 64


def test_fingerprint_method_case_insensitive() -> None:
    fp1 = calculate_request_fingerprint("post", "/payments", "a=1", b"")
    fp2 = calculate_request_fingerprint("POST", "/payments", "a=1", b"")
    assert fp1 == fp2


def test_fingerprint_path_trailing_slash_normalization() -> None:
    fp1 = calculate_request_fingerprint("POST", "/payments", "", b"")
    fp2 = calculate_request_fingerprint("POST", "/payments/", "", b"")
    assert fp1 == fp2


def test_fingerprint_root_path() -> None:
    fp1 = calculate_request_fingerprint("GET", "/", "", b"")
    fp2 = calculate_request_fingerprint("GET", "/", "", b"")
    assert fp1 == fp2


def test_fingerprint_query_string_order_independent() -> None:
    fp1 = calculate_request_fingerprint("POST", "/api/v1", "a=1&b=2&c=3", b"")
    fp2 = calculate_request_fingerprint("POST", "/api/v1", "c=3&a=1&b=2", b"")
    assert fp1 == fp2


def test_fingerprint_query_string_bytes_or_str() -> None:
    fp1 = calculate_request_fingerprint("POST", "/items", b"sort=asc&limit=10", b"")
    fp2 = calculate_request_fingerprint("POST", "/items", "sort=asc&limit=10", b"")
    assert fp1 == fp2


def test_fingerprint_body_change() -> None:
    fp1 = calculate_request_fingerprint("POST", "/items", "", b'{"a": 1}')
    fp2 = calculate_request_fingerprint("POST", "/items", "", b'{"a": 2}')
    assert fp1 != fp2


def test_fingerprint_headers_canonicalization() -> None:
    h1 = {"X-Tenant": "Alpha", "Authorization": "Bearer 123"}
    h2 = {"authorization": "Bearer 123", "x-tenant": "Alpha"}
    fp1 = calculate_request_fingerprint("POST", "/items", "", b"", headers=h1)
    fp2 = calculate_request_fingerprint("POST", "/items", "", b"", headers=h2)
    assert fp1 == fp2

    h3 = {"X-Tenant": "Beta", "Authorization": "Bearer 123"}
    fp3 = calculate_request_fingerprint("POST", "/items", "", b"", headers=h3)
    assert fp1 != fp3
