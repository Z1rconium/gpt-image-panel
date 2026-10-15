"""Access-control middleware and exception handlers (composition).

Host/CSRF validation lives in :mod:`host_validation`, response header handling
in :mod:`security_headers`, the error envelope in :mod:`errors`, and selective
compression in :mod:`compression`. This module wires them onto the app.
"""

import logging
import re
import time

from fastapi import HTTPException, Request
from fastapi.exceptions import RequestValidationError

from ..core.errors import DomainError
from ..core import security as auth
from ..core import settings as config
from ..core.observability import metrics
from .compression import _GZIP_BYPASS_PREFIXES
from .errors import (
    _correlation_id,
    _error_code_for_status,
    _request_validation_detail,
    error_response,
)
from .host_validation import (
    _request_is_https,
    csrf_origin_allowed,
    request_host_allowed,
)
from .security_headers import _add_vary_cookie, apply_security_headers


logger = logging.getLogger(__name__)

AUTH_EXEMPT_PATHS = {
    "/",
    "/api/access",
    "/api/access/status",
    "/favicon.ico",
    "/favicon.svg",
    "/health",
}
AUTH_EXEMPT_PREFIXES = ("/_app/",)
_API_CACHE_CONTROL_PRESERVE_PREFIXES = _GZIP_BYPASS_PREFIXES
_GENERATE_JOB_MASK_RE = re.compile(r"^/api/generate/[^/]+/mask$")


def register_middleware(app):
    @app.middleware("http")
    async def access_control_middleware(request: Request, call_next):
        request_started_at = time.perf_counter()
        correlation_id = _correlation_id(request)
        host_allowed, host_detail = request_host_allowed(request)
        if not host_allowed:
            logger.warning(
                "Rejected request: host check failed path=%s client=%s detail=%s",
                request.url.path,
                auth.get_client_ip(request),
                host_detail,
            )
            return error_response(request, 400, "host_not_allowed", host_detail)

        if request.url.path != "/health":
            client_ip = auth.get_client_ip(request)
            if not auth.is_ip_allowed(client_ip):
                logger.warning(
                    "Rejected request: ip not allowed path=%s client=%s",
                    request.url.path,
                    client_ip,
                )
                return error_response(
                    request,
                    403,
                    "ip_not_allowed",
                    "IP address is not allowed",
                )

        if not csrf_origin_allowed(request):
            logger.warning(
                "Rejected request: csrf check failed path=%s client=%s",
                request.url.path,
                auth.get_client_ip(request),
            )
            return error_response(
                request,
                403,
                "csrf_rejected",
                "CSRF origin check failed",
            )

        if (
            config.ACCESS_KEY
            and request.url.path not in AUTH_EXEMPT_PATHS
            and not request.url.path.startswith(AUTH_EXEMPT_PREFIXES)
        ):
            token = request.cookies.get(config.ACCESS_KEY_COOKIE_NAME)
            if not auth.verify_access_token(token):
                logger.warning(
                    "Rejected request: access key required path=%s client=%s",
                    request.url.path,
                    auth.get_client_ip(request),
                )
                return error_response(
                    request,
                    401,
                    "authentication_required",
                    "Access key required",
                )

        try:
            response = await call_next(request)
        finally:
            elapsed_ms = (time.perf_counter() - request_started_at) * 1000
            metrics.observe_ms("http.request", elapsed_ms)

        preserves_cache_control = (
            response.status_code < 400
            and "Cache-Control" in response.headers
            and (
                request.url.path.startswith(_API_CACHE_CONTROL_PRESERVE_PREFIXES)
                # job_id is unique and its promoted mask file is never
                # rewritten, so this one route may set its own long-lived
                # Cache-Control (see get_generate_job_mask); the job_id
                # segment rules out prefix matching like the routes above.
                or bool(_GENERATE_JOB_MASK_RE.match(request.url.path))
            )
        )
        if response.status_code >= 400 or (
            request.url.path.startswith("/api/") and not preserves_cache_control
        ):
            response.headers["Cache-Control"] = "private, no-store"
            _add_vary_cookie(response)
        elif preserves_cache_control:
            _add_vary_cookie(response)
        elif request.url.path == "/":
            response.headers["Cache-Control"] = "no-cache"

        response.headers["X-Correlation-ID"] = correlation_id

        return apply_security_headers(response, hsts=_request_is_https(request))


def register_exception_handlers(app):
    @app.exception_handler(HTTPException)
    async def http_exception_handler(request: Request, exc: HTTPException):
        logger.warning(
            "Request rejected path=%s client=%s status=%s detail=%s correlation_id=%s",
            request.url.path,
            auth.get_client_ip(request),
            exc.status_code,
            exc.detail,
            _correlation_id(request),
        )
        response = error_response(
            request,
            exc.status_code,
            _error_code_for_status(exc.status_code),
            exc.detail,
        )
        # Preserve control headers such as Retry-After that a handler set on the
        # HTTPException; the JSON envelope is built here and would drop them.
        for name, value in (exc.headers or {}).items():
            response.headers.setdefault(name, value)
        return response

    @app.exception_handler(DomainError)
    async def domain_error_handler(request: Request, exc: DomainError):
        # Services raise DomainError; the status and envelope stay here, so the
        # response is identical to the HTTPException path.
        logger.warning(
            "Request rejected path=%s client=%s status=%s detail=%s correlation_id=%s",
            request.url.path,
            auth.get_client_ip(request),
            exc.status_code,
            exc.detail,
            _correlation_id(request),
        )
        return error_response(
            request,
            exc.status_code,
            _error_code_for_status(exc.status_code),
            exc.detail,
        )

    @app.exception_handler(RequestValidationError)
    async def request_validation_handler(request: Request, exc: RequestValidationError):
        logger.warning(
            "Request validation failed path=%s client=%s correlation_id=%s",
            request.url.path,
            auth.get_client_ip(request),
            _correlation_id(request),
        )
        return error_response(
            request,
            422,
            "validation_error",
            _request_validation_detail(exc),
        )

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception):
        logger.exception(
            "Unhandled request error path=%s client=%s",
            request.url.path,
            auth.get_client_ip(request),
        )
        return error_response(request, 500, "internal_error", "Internal Server Error")
