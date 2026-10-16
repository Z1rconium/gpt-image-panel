"""Security response headers and cache-related response header helpers."""

from fastapi import Response

from .csp import CONTENT_SECURITY_POLICY


# Disable powerful platform features this application never uses, as a
# defense-in-depth complement to the CSP. Features the UI relies on (e.g.
# clipboard, fullscreen, picture-in-picture) are intentionally left unlisted so
# they keep their browser defaults.
PERMISSIONS_POLICY = (
    "accelerometer=(), camera=(), display-capture=(), geolocation=(), "
    "gyroscope=(), magnetometer=(), microphone=(), midi=(), payment=(), usb=()"
)
# Application-level HSTS fallback for deployments whose reverse proxy does not
# set it. Only sent over HTTPS (see host_validation._request_is_https);
# includeSubDomains is deliberately omitted so a shared parent domain is not
# affected.
STRICT_TRANSPORT_SECURITY = "max-age=31536000"


def apply_security_headers(response: Response, *, hsts: bool = False) -> Response:
    if "Content-Security-Policy" not in response.headers:
        response.headers["Content-Security-Policy"] = CONTENT_SECURITY_POLICY
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "same-origin"
    response.headers["Permissions-Policy"] = PERMISSIONS_POLICY
    if hsts and "Strict-Transport-Security" not in response.headers:
        response.headers["Strict-Transport-Security"] = STRICT_TRANSPORT_SECURITY
    return response


def _add_vary_cookie(response: Response) -> None:
    vary = response.headers.get("Vary")
    if not vary:
        response.headers["Vary"] = "Cookie"
        return
    if not any(value.strip().lower() == "cookie" for value in vary.split(",")):
        response.headers["Vary"] = f"{vary}, Cookie"
