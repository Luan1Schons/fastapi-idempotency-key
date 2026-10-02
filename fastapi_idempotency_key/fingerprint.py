from __future__ import annotations

import hashlib
from typing import Dict, Optional
from urllib.parse import parse_qsl, urlencode


def calculate_request_fingerprint(
    method: str,
    path: str,
    query_string: bytes | str = "",
    body: bytes = b"",
    headers: Optional[Dict[str, str]] = None,
) -> str:
    """Computes a deterministic SHA-256 hash for an incoming HTTP request.

    Components included:
    - HTTP method (normalized uppercase)
    - Request path (normalized without redundant trailing slash)
    - Query string (sorted parameters for determinism)
    - Headers (if provided, sorted by lowercased name)
    - Body bytes
    """
    hasher = hashlib.sha256()

    # 1. Method
    hasher.update(method.strip().upper().encode("utf-8"))
    hasher.update(b"\n")

    # 2. Path normalization
    norm_path = path.strip()
    if len(norm_path) > 1 and norm_path.endswith("/"):
        norm_path = norm_path.rstrip("/")
    hasher.update(norm_path.encode("utf-8"))
    hasher.update(b"\n")

    # 3. Canonicalized query string
    if isinstance(query_string, bytes):
        raw_query = query_string.decode("utf-8", errors="replace")
    else:
        raw_query = query_string or ""

    if raw_query:
        params = parse_qsl(raw_query, keep_blank_values=True)
        sorted_params = sorted(params, key=lambda kv: (kv[0], kv[1]))
        canonical_query = urlencode(sorted_params)
    else:
        canonical_query = ""
    hasher.update(canonical_query.encode("utf-8"))
    hasher.update(b"\n")

    # 4. Canonicalized headers (optional)
    if headers:
        for k in sorted(headers.keys(), key=str.lower):
            hasher.update(f"{k.lower().strip()}:{headers[k].strip()}\n".encode("utf-8"))
    else:
        hasher.update(b"\n")

    # 5. Raw body
    hasher.update(body)

    return hasher.hexdigest()
