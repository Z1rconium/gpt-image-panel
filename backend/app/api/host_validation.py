"""Host, forwarded-host, origin, and CSRF validation.

This is the trust boundary for the ``Host``/``Origin``/``X-Forwarded-*``
headers; see ``.trellis/spec/backend/auth-and-security.md``.
"""

import re
from urllib.parse import urlsplit

from fastapi import Request

from ..core import security as auth
from ..core import settings as config
from ..core.validators import split_allowlist


CSRF_SAFE_METHODS = {"GET", "HEAD", "OPTIONS", "TRACE"}
_INVALID_HOST_RE = re.compile(r"[\x00-\x1f\x7f]")


def normalize_origin(value: str | None) -> str | None:
    if not value:
        return None

    parts = urlsplit(value.strip())
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        return None

    try:
        port = parts.port
    except ValueError:
        return None

    host = parts.hostname.lower()
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"

    if port and not (
        (parts.scheme == "http" and port == 80)
        or (parts.scheme == "https" and port == 443)
    ):
        host = f"{host}:{port}"

    return f"{parts.scheme.lower()}://{host}"


def _is_valid_forwarded_host(value: str) -> bool:
    """Reject empty, control-char-containing, or overly long forwarded host values."""
    if not value or len(value) > 255:
        return False
    if _INVALID_HOST_RE.search(value):
        return False
    return True


def normalize_host(value: str | None) -> str | None:
    if not value:
        return None
    candidate = value.strip()
    if not _is_valid_forwarded_host(candidate):
        return None

    try:
        parts = urlsplit(f"//{candidate}")
        port = parts.port
    except ValueError:
        return None

    if (
        not parts.hostname
        or parts.username is not None
        or parts.password is not None
        or parts.path not in {"", "/"}
        or parts.query
        or parts.fragment
    ):
        return None

    host = parts.hostname.lower()
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    if port is not None:
        host = f"{host}:{port}"
    return host


def _configured_public_origin() -> str | None:
    public_origin = str(getattr(config, "PUBLIC_ORIGIN", "") or "").strip()
    if not public_origin:
        return None
    return normalize_origin(public_origin)


def _has_invalid_public_origin() -> bool:
    return bool(str(getattr(config, "PUBLIC_ORIGIN", "") or "").strip()) and (
        _configured_public_origin() is None
    )


def _allowed_hosts_configured() -> bool:
    return bool(split_allowlist(getattr(config, "ALLOWED_HOSTS", ""), separators=","))


def _host_has_port(host: str) -> bool:
    try:
        return urlsplit(f"//{host}").port is not None
    except ValueError:
        return False


def _add_allowed_host(allowed: set[str], host: str | None, scheme: str | None = None) -> None:
    if not host:
        return
    allowed.add(host)
    if _host_has_port(host):
        return
    if scheme == "https":
        allowed.add(f"{host}:443")
    elif scheme == "http":
        allowed.add(f"{host}:80")


def _configured_allowed_hosts() -> set[str]:
    allowed: set[str] = set()
    for value in split_allowlist(getattr(config, "ALLOWED_HOSTS", ""), separators=","):
        if "://" in value:
            origin = normalize_origin(value)
            if origin:
                origin_parts = urlsplit(origin)
                normalized = normalize_host(origin_parts.netloc)
                scheme = origin_parts.scheme
            else:
                normalized = None
                scheme = None
        else:
            normalized = normalize_host(value)
            scheme = None
        _add_allowed_host(allowed, normalized, scheme)

    public_origin = _configured_public_origin()
    if public_origin:
        public_parts = urlsplit(public_origin)
        public_host = normalize_host(public_parts.netloc)
        _add_allowed_host(allowed, public_host, public_parts.scheme)
    return allowed


def _host_allowed(host: str) -> bool:
    allowed_hosts = _configured_allowed_hosts()
    if not allowed_hosts and _allowed_hosts_configured():
        return False
    return not allowed_hosts or host in allowed_hosts


def _is_trusted_proxy_request(request: Request) -> bool:
    return bool(
        config.TRUST_PROXY_HEADERS
        and auth.is_trusted_proxy(request.client.host if request.client else "")
    )


def _first_forwarded_host(request: Request) -> str | None:
    forwarded_host = request.headers.get("x-forwarded-host")
    if not forwarded_host:
        return None
    return forwarded_host.split(",", 1)[0].strip()


def request_host_allowed(request: Request) -> tuple[bool, str]:
    if _has_invalid_public_origin():
        return False, "PUBLIC_ORIGIN is invalid"

    host = normalize_host(request.headers.get("host") or request.url.netloc)
    if not host or not _host_allowed(host):
        return False, "Host is not allowed"

    if _is_trusted_proxy_request(request):
        forwarded_host = _first_forwarded_host(request)
        if forwarded_host:
            normalized_forwarded_host = normalize_host(forwarded_host)
            if (
                not normalized_forwarded_host
                or not _host_allowed(normalized_forwarded_host)
            ):
                return False, "Forwarded host is not allowed"

    return True, ""


def get_request_origin(request: Request) -> str | None:
    public_origin = _configured_public_origin()
    if public_origin:
        return public_origin

    scheme = request.url.scheme
    host = normalize_host(request.headers.get("host") or request.url.netloc)
    if not host:
        return None

    if _is_trusted_proxy_request(request):
        forwarded_proto = request.headers.get("x-forwarded-proto")
        if forwarded_proto:
            trusted_scheme = forwarded_proto.split(",", 1)[0].strip().lower()
            if trusted_scheme in {"http", "https"}:
                scheme = trusted_scheme
        forwarded_host = _first_forwarded_host(request)
        if forwarded_host:
            normalized_forwarded_host = normalize_host(forwarded_host)
            if not normalized_forwarded_host:
                return None
            host = normalized_forwarded_host

    return normalize_origin(f"{scheme}://{host}")


def _request_is_https(request: Request) -> bool:
    """Whether this request reached the browser over HTTPS.

    Uses the same trusted-proxy-aware origin resolution as the CSRF check, so
    HSTS is only emitted for genuine HTTPS (or a configured HTTPS
    ``PUBLIC_ORIGIN``) and never over plain HTTP.
    """
    origin = get_request_origin(request)
    return bool(origin and origin.startswith("https://"))


def csrf_origin_allowed(request: Request) -> bool:
    if (
        not config.CSRF_ORIGIN_CHECK_ENABLED
        or request.method.upper() in CSRF_SAFE_METHODS
    ):
        return True

    host_allowed, _detail = request_host_allowed(request)
    if not host_allowed:
        return False

    expected_origin = get_request_origin(request)
    if not expected_origin:
        return False

    # Browser fetch metadata reflects the page-visible request target before a
    # local/dev proxy rewrites the upstream Host header.
    sec_fetch_site = request.headers.get("sec-fetch-site", "").strip().lower()
    if sec_fetch_site == "same-origin":
        return True
    if sec_fetch_site == "cross-site":
        return False

    origin = request.headers.get("origin")
    if origin is not None:
        return normalize_origin(origin) == expected_origin

    referer = request.headers.get("referer")
    if referer:
        return normalize_origin(referer) == expected_origin

    # Auth-exempt mutation endpoints still need a browser source signal. If the
    # request has no Origin, Referer, or same-origin fetch metadata, we cannot
    # distinguish it from a cross-site form/request.
    return False
