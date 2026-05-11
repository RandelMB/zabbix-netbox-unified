from __future__ import annotations

import json
import logging
import time
import uuid
from contextvars import ContextVar
from typing import Any

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware


request_id_var: ContextVar[str] = ContextVar("request_id", default="")
operation_id_var: ContextVar[str] = ContextVar("operation_id", default="")
correlation_id_var: ContextVar[str] = ContextVar("correlation_id", default="")
sync_id_var: ContextVar[str] = ContextVar("sync_id", default="")


def _new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def get_request_id() -> str:
    value = request_id_var.get()
    if value:
        return value
    generated = _new_id("req")
    request_id_var.set(generated)
    return generated


def get_operation_id() -> str:
    value = operation_id_var.get()
    if value:
        return value
    generated = _new_id("op")
    operation_id_var.set(generated)
    return generated


def get_request_context() -> dict[str, str]:
    return {
        "request_id": get_request_id(),
        "operation_id": get_operation_id(),
        "correlation_id": correlation_id_var.get(),
        "sync_id": sync_id_var.get(),
    }


def log_event(logger: Any, level: str, event: str, **fields: Any) -> None:
    payload = {"event": event, **get_request_context(), **{key: value for key, value in fields.items() if value not in (None, "", [])}}
    log_method = getattr(logger, level.lower(), logger.info)
    log_method(json.dumps(payload, default=str, ensure_ascii=True))


class RequestContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        logger = logging.getLogger("uvicorn.error")
        started = time.perf_counter()
        request_id = request.headers.get("X-Request-ID") or _new_id("req")
        operation_id = request.headers.get("X-Operation-ID") or _new_id("op")
        request_id_var.set(request_id)
        operation_id_var.set(operation_id)
        correlation_id_var.set(request.headers.get("X-Correlation-ID", ""))
        sync_id_var.set(request.headers.get("X-Sync-ID", ""))

        response = await call_next(request)
        duration_ms = round((time.perf_counter() - started) * 1000, 2)
        log_event(
            logger,
            "info",
            "http_request_complete",
            method=request.method,
            path=request.url.path,
            status_code=response.status_code,
            duration_ms=duration_ms,
        )
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Operation-ID"] = operation_id
        response.headers["X-Response-Time-MS"] = str(duration_ms)
        return response
