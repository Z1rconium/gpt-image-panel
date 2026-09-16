import json
import logging
from typing import Any
from urllib.parse import urlsplit

from ...core.redaction import redact_sensitive_text
from ...core import validators as ssrf

logger = logging.getLogger(__name__)


class UpstreamApiError(Exception):
    pass


class UpstreamImageDownloadError(UpstreamApiError):
    pass


MAX_PERSISTABLE_UPSTREAM_ERROR_CHARS = 2000


def _sanitize_upstream_error_text(value: Any) -> str:
    return redact_sensitive_text(value)[:MAX_PERSISTABLE_UPSTREAM_ERROR_CHARS]


async def _warn_if_socks5_upstream_resolves_private(
    upstream_url: str,
    socks5_proxy: str | None,
) -> None:
    if not socks5_proxy:
        return

    parsed = urlsplit(upstream_url)
    hostname = (parsed.hostname or "").lower()
    if not hostname:
        return

    _resolved_host, resolved_ips = await ssrf.resolve_hostname_async(hostname)
    private_ips = [ip for ip in resolved_ips if ssrf.is_private_ip(ip)]
    if not private_ips:
        return

    logger.warning(
        "SOCKS5 proxy is enabled and upstream host '%s' resolved locally to "
        "private/internal IP(s): %s. Pre-connection SSRF validation still blocks "
        "private local resolutions, but the SOCKS5 proxy is the trust boundary "
        "for remote DNS and upstream network reachability.",
        hostname,
        ", ".join(private_ips),
    )


def get_upstream_error_message(
    status: int,
    response_text: str,
    is_json_response: bool,
) -> str:
    if is_json_response:
        try:
            error_body = json.loads(response_text)
            if isinstance(error_body, dict):
                error = error_body.get("error")
                if isinstance(error, dict):
                    return _sanitize_upstream_error_text(error.get("message", response_text))
            return _sanitize_upstream_error_text(response_text)
        except Exception:
            return _sanitize_upstream_error_text(response_text)
    return f"HTTP {status}: {_sanitize_upstream_error_text(response_text[:200])}"


def raise_upstream_error(
    status: int,
    response_text: str,
    is_json_response: bool,
    api_path: str,
):
    error_msg = get_upstream_error_message(status, response_text, is_json_response)
    unsupported_markers = (
        "not support",
        "not_supported",
        "unsupported",
        "not found",
        "unknown endpoint",
        "no route",
    )
    if api_path == "/v1/images/edits" and (
        status in {404, 405, 501}
        or any(marker in error_msg.lower() for marker in unsupported_markers)
    ):
        raise UpstreamApiError(
            f"Upstream API does not support /v1/images/edits ({status}): {error_msg}"
        )
    raise UpstreamApiError(f"Upstream API error ({status}): {error_msg}")
