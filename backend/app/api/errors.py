"""Error envelope construction and correlation IDs.

Every rejected request returns one JSON envelope with a correlation id and
redacted detail; see ``.trellis/spec/backend/logging-and-errors.md``.
"""

import uuid

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from ..core.redaction import redact_sensitive_text
from .security_headers import apply_security_headers


def _correlation_id(request: Request) -> str:
    correlation_id = getattr(request.state, "correlation_id", None)
    if not correlation_id:
        correlation_id = uuid.uuid4().hex
        request.state.correlation_id = correlation_id
    return correlation_id


def _safe_error_detail(detail: object) -> str:
    if isinstance(detail, str):
        return redact_sensitive_text(detail)
    return redact_sensitive_text(str(detail or ""))


def error_response(
    request: Request,
    status_code: int,
    error_code: str,
    detail: object | None = None,
) -> JSONResponse:
    correlation_id = _correlation_id(request)
    safe_detail = _safe_error_detail(detail) if detail is not None else None
    content = {
        "status": "error",
        "error_code": error_code,
        "correlation_id": correlation_id,
    }
    if safe_detail:
        content["detail"] = safe_detail
        content["message"] = safe_detail
        content["error"] = safe_detail
    response = JSONResponse(
        status_code=status_code,
        content=content,
    )
    response.headers["X-Correlation-ID"] = correlation_id
    response.headers["Cache-Control"] = "private, no-store"
    return apply_security_headers(response)


def _request_validation_detail(exc: RequestValidationError) -> str:
    messages: list[str] = []
    for error in exc.errors():
        raw_loc = error.get("loc") or ()
        loc_parts = [str(part) for part in raw_loc if part != "body"]
        location = ".".join(loc_parts)
        message = str(error.get("msg") or "Invalid input")
        if message.startswith("Value error, "):
            message = message.removeprefix("Value error, ")
        messages.append(f"{location} - {message}" if location else message)
    return "; ".join(messages) or "Request validation failed"


def _error_code_for_status(status_code: int) -> str:
    return {
        400: "bad_request",
        401: "authentication_required",
        403: "forbidden",
        404: "not_found",
        405: "method_not_allowed",
        409: "conflict",
        413: "request_too_large",
        422: "validation_error",
        429: "rate_limited",
        502: "upstream_error",
        503: "service_unavailable",
        504: "upstream_timeout",
    }.get(status_code, "internal_error" if status_code >= 500 else "request_rejected")
