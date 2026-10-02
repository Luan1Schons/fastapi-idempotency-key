from __future__ import annotations

import asyncio
import functools
import inspect
import json
from typing import Any, Callable, List, Optional, Tuple, TypeVar, cast

from starlette.requests import Request
from starlette.responses import Response

from fastapi_idempotency_key.backends.base import BaseIdempotencyBackend
from fastapi_idempotency_key.backends.memory import MemoryBackend
from fastapi_idempotency_key.exceptions import (
    IdempotencyConflictError,
    IdempotencyKeyMissingError,
    IdempotencyPayloadMismatchError,
)
from fastapi_idempotency_key.fingerprint import calculate_request_fingerprint
from fastapi_idempotency_key.status import IdempotencyStatus

_DEFAULT_BACKEND: Optional[MemoryBackend] = None


def _get_default_backend() -> MemoryBackend:
    global _DEFAULT_BACKEND
    if _DEFAULT_BACKEND is None:
        _DEFAULT_BACKEND = MemoryBackend()
    return _DEFAULT_BACKEND


F = TypeVar("F", bound=Callable[..., Any])


def idempotent(
    expire: int = 86400,
    header_name: str = "Idempotency-Key",
    replay_header_name: str = "Idempotency-Replayed",
    backend: Optional[BaseIdempotencyBackend] = None,
    required: bool = False,
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
) -> Callable[[F], F]:
    """Decorator for FastAPI and Starlette endpoint handlers to enforce idempotency."""

    def decorator(func: F) -> F:
        sig = inspect.signature(func)
        has_request = any(
            p.name == "request" or p.annotation is Request
            for p in sig.parameters.values()
        )

        @functools.wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            req: Optional[Request] = None
            if has_request:
                req = kwargs.get("request")
                if req is None:
                    for arg in args:
                        if isinstance(arg, Request):
                            req = arg
                            break
            else:
                req = kwargs.pop("request", None)

            # If request object is not available (e.g. direct function call in unit test)
            if req is None:
                if inspect.iscoroutinefunction(func):
                    return await func(*args, **kwargs)
                return func(*args, **kwargs)

            # If idempotency was already handled by ASGI middleware, avoid double processing
            if req.scope.get("_idempotency_handled"):
                if required and header_name not in req.headers:
                    raise IdempotencyKeyMissingError()
                if inspect.iscoroutinefunction(func):
                    return await func(*args, **kwargs)
                return func(*args, **kwargs)

            # Check header
            key = req.headers.get(header_name)
            if not key:
                if required:
                    raise IdempotencyKeyMissingError()
                if inspect.iscoroutinefunction(func):
                    return await func(*args, **kwargs)
                return func(*args, **kwargs)

            # Fingerprint request
            body_bytes = await req.body()
            method = req.method
            path = req.url.path
            query_string = req.url.query
            fingerprint = calculate_request_fingerprint(
                method=method,
                path=path,
                query_string=query_string,
                body=body_bytes,
            )

            active_backend = backend or _get_default_backend()
            acquired, record = await active_backend.try_lock(
                key=key,
                fingerprint=fingerprint,
                ttl=expire,
                timeout=timeout,
            )

            if not acquired:
                if (
                    record is not None
                    and record.status == IdempotencyStatus.IN_PROGRESS
                ):
                    raise IdempotencyConflictError()

                if record is not None and record.status == IdempotencyStatus.COMPLETED:
                    if record.fingerprint != fingerprint:
                        raise IdempotencyPayloadMismatchError()

                    headers_dict = dict(record.headers)
                    headers_dict[replay_header_name] = "true"
                    return Response(
                        content=record.body,
                        status_code=record.status_code or 200,
                        headers=headers_dict,
                    )

            # Lock acquired: execute endpoint
            try:
                if inspect.iscoroutinefunction(func):
                    res = await func(*args, **kwargs)
                else:
                    res = func(*args, **kwargs)
            except Exception:
                await active_backend.release_lock(key)
                raise

            # Cache response
            if isinstance(res, Response):
                status_code = res.status_code
                response_body = bytes(res.body)
                raw_headers: List[Tuple[str, str]] = [
                    (k, v) for k, v in res.headers.items()
                ]
                if status_code in cache_statuses:
                    await active_backend.store_response(
                        key=key,
                        status_code=status_code,
                        headers=raw_headers,
                        body=response_body,
                        ttl=expire,
                    )
                else:
                    await active_backend.release_lock(key)
                return res
            else:
                json_bytes = json.dumps(res, default=str).encode("utf-8")
                await active_backend.store_response(
                    key=key,
                    status_code=200,
                    headers=[("content-type", "application/json")],
                    body=json_bytes,
                    ttl=expire,
                )
                return res

        if not has_request:
            new_params = list(sig.parameters.values()) + [
                inspect.Parameter(
                    "request",
                    inspect.Parameter.KEYWORD_ONLY,
                    annotation=Request,
                )
            ]
            wrapper.__signature__ = sig.replace(parameters=new_params)  # type: ignore[attr-defined]
        else:
            wrapper.__signature__ = sig  # type: ignore[attr-defined]

        return cast(F, wrapper)

    return decorator
