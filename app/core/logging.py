from __future__ import annotations

import json
import hashlib
import logging
import re
import time
import uuid
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any

from fastapi import Request, Response

from app.core.config import get_settings
_REQUEST_ID = ContextVar[str | None]("request_id", default=None)
_SUBJECT = ContextVar[str | None]("log_subject", default=None)
_SAFE_REQUEST_ID = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")

_OPERATIONAL_FIELDS = (
    "request_id",
    "subject",
    "method",
    "path",
    "status_code",
    "duration_ms",
    "intent",
    "provider",
    "provider_status",
    "train_status",
    "direction",
    "elapsed_seconds",
    "attempt",
    "provider_business_code",
    "provider_reason",
    "model_calls",
    "model_input_tokens",
    "model_output_tokens",
    "estimated_cost_micros",
    "cost_estimate_configured",
    "stage",
    "trip_saved",
    "db_operation",
    "db_status",
    "error_code",
    "exception_type",
    "failure_stage",
    "validation_codes",
    "candidate_type",
    "candidate_kind",
    "schema_error_count",
    "schema_error_locations",
    "schema_error_types",
)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for field in _OPERATIONAL_FIELDS:
            value = getattr(record, field, None)
            if value is not None:
                payload[field] = value
        return json.dumps(payload, ensure_ascii=False, default=str)


def configure_logging() -> None:
    configured_level = get_settings().log_level.upper()
    level = getattr(logging, configured_level, logging.INFO)
    root_logger = logging.getLogger()
    root_logger.setLevel(level)
    if not any(
        getattr(handler, "_travel_assistant_json", False)
        for handler in root_logger.handlers
    ):
        handler = logging.StreamHandler()
        handler.setFormatter(JsonFormatter())
        handler._travel_assistant_json = True  # type: ignore[attr-defined]
        root_logger.addHandler(handler)
    # Canonical request logs are emitted below with redacted route paths.
    logging.getLogger("uvicorn.access").disabled = True
    # HTTP client request lines can contain provider query data; app adapters log
    # only stable provider/error metadata instead.
    logging.getLogger("httpx").setLevel(logging.WARNING)


def current_request_id() -> str | None:
    return _REQUEST_ID.get()


def operational_context(**fields: Any) -> dict[str, Any]:
    context = dict(fields)
    request_id = _REQUEST_ID.get()
    subject = _SUBJECT.get()
    if request_id is not None:
        context.setdefault("request_id", request_id)
    if subject is not None:
        context.setdefault("subject", subject)
    return context


@contextmanager
def log_subject(subject: str) -> Iterator[None]:
    token = _SUBJECT.set(subject)
    try:
        yield
    finally:
        _SUBJECT.reset(token)


@contextmanager
def correlation_context(request_id: str, subject: str | None = None) -> Iterator[None]:
    """Bind correlation fields for non-HTTP operations and deterministic tests."""
    if not _SAFE_REQUEST_ID.fullmatch(request_id):
        raise ValueError("invalid request id")
    request_token = _REQUEST_ID.set(request_id)
    subject_token = _SUBJECT.set(subject)
    try:
        yield
    finally:
        _SUBJECT.reset(subject_token)
        _REQUEST_ID.reset(request_token)


def hashed_log_subject(kind: str, value: object) -> str:
    """Create a stable log-only subject without exposing an owner or share key."""
    digest = hashlib.sha256(str(value).encode("utf-8")).hexdigest()
    return f"{kind}-digest:{digest}"


@contextmanager
def database_operation(
    operation: str, *, subject: str | None = None
) -> Iterator[None]:
    """Emit one safe, request-correlated result for a database operation."""
    try:
        yield
    except Exception as exc:
        fields = {
            "db_operation": operation,
            "db_status": "failure",
            "exception_type": type(exc).__name__,
        }
        if subject is not None and _SUBJECT.get() is None:
            fields["subject"] = subject
        logging.getLogger("app.database").warning(
            "database_result",
            extra=operational_context(**fields),
        )
        raise
    else:
        fields = {"db_operation": operation, "db_status": "success"}
        if subject is not None and _SUBJECT.get() is None:
            fields["subject"] = subject
        logging.getLogger("app.database").info(
            "database_result",
            extra=operational_context(**fields),
        )


def request_log_path(request: Request) -> str:
    route = request.scope.get("route")
    template = getattr(route, "path", None)
    return template if isinstance(template, str) and template.startswith("/") else "/{unmatched}"


def _request_id(header_value: str | None) -> str:
    if header_value and _SAFE_REQUEST_ID.fullmatch(header_value):
        return header_value
    return str(uuid.uuid4())


async def request_context(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    request_id = _request_id(request.headers.get("X-Request-ID"))
    request.state.request_id = request_id
    started_at = time.perf_counter()
    status_code = 500
    request_token = _REQUEST_ID.set(request_id)
    try:
        response = await call_next(request)
        status_code = response.status_code
    except Exception:
        logging.getLogger("app.request").exception(
            "request_failed",
            extra=operational_context(
                method=request.method,
                path=request_log_path(request),
                status_code=status_code,
                duration_ms=round((time.perf_counter() - started_at) * 1000, 3),
            ),
        )
        raise
    finally:
        _REQUEST_ID.reset(request_token)

    duration_ms = round((time.perf_counter() - started_at) * 1000, 3)
    response.headers["X-Request-ID"] = request_id
    request_token = _REQUEST_ID.set(request_id)
    subject_token = _SUBJECT.set(getattr(request.state, "log_subject", None))
    try:
        logging.getLogger("app.request").info(
            "request_complete",
            extra=operational_context(
                intent=getattr(request.state, "log_intent", None),
                method=request.method,
                path=request_log_path(request),
                status_code=status_code,
                duration_ms=duration_ms,
            ),
        )
    finally:
        _SUBJECT.reset(subject_token)
        _REQUEST_ID.reset(request_token)
    return response
