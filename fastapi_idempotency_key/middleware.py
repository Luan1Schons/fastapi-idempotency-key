from __future__ import annotations

import json
from typing import Any, List, Optional, Tuple

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from fastapi_idempotency_key.backends.base import BaseIdempotencyBackend
from fastapi_idempotency_key.backends.memory import MemoryBackend
from fastapi_idempotency_key.fingerprint import calculate_request_fingerprint
from fastapi_idempotency_key.status import IdempotencyStatus


class IdempotencyMiddleware:
    """Pure ASGI middleware providing Stripe-grade Idempotency-Key support for FastAPI and Starlette."""

    def __init__(
        self,
        app: ASGIApp,
        backend: Optional[BaseIdempotencyBackend] = None,
        header_name: str = "Idempotency-Key",
        replay_header_name: str = "Idempotency-Replayed",
        enforce_methods: Tuple[str, ...] = ("POST", "PATCH", "PUT"),
        default_ttl: int = 86400,
        timeout: float = 0.0,
        cache_statuses: Tuple[int, ...] = (
            200,
            201,
            202,
            204,
            301,
            302,
            307,
            308,
            400,
            404,
            422,
        ),
        on_conflict_status: int = 409,
        on_mismatch_status: int = 422,
        required: bool = False,
    ) -> None:
        self.app = app
        self.backend = backend or MemoryBackend()
        self.header_name = header_name
        self.replay_header_name = replay_header_name
        self.enforce_methods = tuple(m.upper() for m in enforce_methods)
        self.default_ttl = default_ttl
        self.timeout = timeout
        self.cache_statuses = cache_statuses
        self.on_conflict_status = on_conflict_status
        self.on_mismatch_status = on_mismatch_status
        self.required = required

        self._header_name_bytes = self.header_name.lower().encode("latin-1")
        self._replay_header_bytes = self.replay_header_name.lower().encode("latin-1")

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        method = scope.get("method", "").upper()
        if method not in self.enforce_methods:
            await self.app(scope, receive, send)
            return

        # Look up Idempotency-Key header in ASGI headers list: List[Tuple[bytes, bytes]]
        idempotency_key: Optional[str] = None
        for raw_name, raw_val in scope.get("headers", []):
            if raw_name.lower() == self._header_name_bytes:
                val_str = raw_val.decode("latin-1").strip()
                if val_str:
                    idempotency_key = val_str
                break

        if not idempotency_key:
            if self.required:
                await self._send_json_response(
                    send,
                    400,
                    {"detail": f"{self.header_name} header is required."},
                )
                return
            await self.app(scope, receive, send)
            return

        # Buffer request body to allow fingerprinting and replay to downstream app
        body_chunks: List[bytes] = []
        received_messages: List[Message] = []
        more_body = True
        while more_body:
            message = await receive()
            received_messages.append(message)
            chunk = message.get("body", b"")
            if chunk:
                body_chunks.append(chunk)
            more_body = message.get("more_body", False)

        request_body = b"".join(body_chunks)

        msg_index = 0

        async def custom_receive() -> Message:
            nonlocal msg_index
            if msg_index < len(received_messages):
                msg = received_messages[msg_index]
                msg_index += 1
                return msg
            return await receive()

        # Compute request fingerprint
        path = scope.get("root_path", "") + scope.get("path", "")
        query_string = scope.get("query_string", b"")
        fingerprint = calculate_request_fingerprint(
            method=method,
            path=path,
            query_string=query_string,
            body=request_body,
        )

        # Attempt to acquire lock
        acquired, record = await self.backend.try_lock(
            key=idempotency_key,
            fingerprint=fingerprint,
            ttl=self.default_ttl,
            timeout=self.timeout,
        )

        if not acquired:
            if record is not None and record.status == IdempotencyStatus.IN_PROGRESS:
                await self._send_json_response(
                    send,
                    self.on_conflict_status,
                    {
                        "detail": "A request with this idempotency key is currently in progress."
                    },
                )
                return

            if record is not None and record.status == IdempotencyStatus.COMPLETED:
                if record.fingerprint != fingerprint:
                    await self._send_json_response(
                        send,
                        self.on_mismatch_status,
                        {
                            "detail": (
                                "Idempotency key was previously used with a different request payload."
                            )
                        },
                    )
                    return

                # Replay cached response
                resp_headers: List[Tuple[bytes, bytes]] = []
                has_content_length = False
                for h_name, h_val in record.headers:
                    h_lower = h_name.lower().encode("latin-1")
                    if h_lower == self._replay_header_bytes:
                        continue
                    if h_lower == b"content-length":
                        has_content_length = True
                        resp_headers.append(
                            (b"content-length", str(len(record.body)).encode("ascii"))
                        )
                    else:
                        resp_headers.append(
                            (h_name.encode("latin-1"), h_val.encode("latin-1"))
                        )

                if not has_content_length and record.body:
                    resp_headers.append(
                        (b"content-length", str(len(record.body)).encode("ascii"))
                    )

                resp_headers.append(
                    (self.replay_header_name.encode("latin-1"), b"true")
                )

                await send(
                    {
                        "type": "http.response.start",
                        "status": record.status_code or 200,
                        "headers": resp_headers,
                    }
                )
                await send(
                    {
                        "type": "http.response.body",
                        "body": record.body,
                        "more_body": False,
                    }
                )
                return

        # Lock acquired: execute downstream application
        scope["_idempotency_handled"] = True

        response_status: int = 500
        response_headers: List[Tuple[str, str]] = []
        response_body_chunks: List[bytes] = []

        async def capture_send(message: Message) -> None:
            nonlocal response_status, response_headers
            msg_type = message.get("type")
            if msg_type == "http.response.start":
                response_status = message.get("status", 200)
                raw_headers = message.get("headers", [])
                response_headers = [
                    (k.decode("latin-1"), v.decode("latin-1")) for k, v in raw_headers
                ]
                await send(message)
            elif msg_type == "http.response.body":
                chunk = message.get("body", b"")
                if chunk:
                    response_body_chunks.append(chunk)
                await send(message)
            else:
                await send(message)

        try:
            await self.app(scope, custom_receive, capture_send)
        except Exception:
            await self.backend.release_lock(idempotency_key)
            raise

        full_response_body = b"".join(response_body_chunks)

        if response_status in self.cache_statuses:
            await self.backend.store_response(
                key=idempotency_key,
                status_code=response_status,
                headers=response_headers,
                body=full_response_body,
                ttl=self.default_ttl,
            )
        else:
            # Server errors or uncacheable statuses release lock so requests can retry
            await self.backend.release_lock(idempotency_key)

    async def _send_json_response(
        self, send: Send, status: int, data: dict[str, Any]
    ) -> None:
        body = json.dumps(data).encode("utf-8")
        headers = [
            (b"content-type", b"application/json"),
            (b"content-length", str(len(body)).encode("ascii")),
        ]
        await send(
            {"type": "http.response.start", "status": status, "headers": headers}
        )
        await send({"type": "http.response.body", "body": body, "more_body": False})
