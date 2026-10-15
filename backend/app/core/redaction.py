import logging
import re
from typing import Any
from urllib.parse import urlsplit, urlunsplit


logger = logging.getLogger(__name__)
_secret_lookup_failure_logged = False

_URL_RE = re.compile(r"(?i)\b(?:https?|socks5)://[^\s<>\"']+")
_AUTH_RE = re.compile(
    r"(?i)(authorization\s*[:=]\s*)(?:bearer\s+)?[^\s,;\]}]+"
)
_BEARER_RE = re.compile(r"(?i)\bbearer\s+[^\s,;\]}]+")
_CREDENTIAL_RE = re.compile(
    r'''(?ix)
    (["']?(?:api[_-]?key|access[_-]?key|secret(?:_access_key)?|token|password|passwd)["']?
    \s*[:=]\s*["']?)
    [^"',;\s}\]]+
    ''',
)
_REDACTION_HINTS = (
    "://",
    "authorization",
    "bearer",
    "api_key",
    "apikey",
    "api-key",
    "access_key",
    "accesskey",
    "access-key",
    "secret",
    "secretaccesskey",
    "token",
    "password",
    "passwd",
)


def mask_url(
    url: str | None,
    *,
    token: str,
    invalid: str,
    require_scheme: bool = True,
    hide_path: bool = False,
) -> str:
    """Mask credentials, query and fragment in a URL, preserving scheme and host.

    This is the single URL-masking primitive. The logging scrub
    (``_redact_url``) and the API/display masking (``validators.redact_url``)
    differ only in token/invalid/require_scheme/hide_path, so they delegate
    here instead of re-implementing the parsing/bracketing logic.
    """
    value = str(url or "").strip()
    if not value:
        return ""
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        return invalid
    if not parsed.hostname or (require_scheme and not parsed.scheme):
        return invalid
    hostname = parsed.hostname
    host = f"[{hostname}]" if ":" in hostname and not hostname.startswith("[") else hostname
    if port is not None:
        host = f"{host}:{port}"
    if parsed.username is not None or parsed.password is not None:
        host = f"{token}@{host}"
    path = parsed.path
    if hide_path and path and path != "/":
        path = f"/{token}"
    query = token if parsed.query else ""
    fragment = token if parsed.fragment else ""
    return urlunsplit((parsed.scheme, host, path, query, fragment))


def _redact_url(match: re.Match[str]) -> str:
    raw = match.group(0)
    trailing = ""
    while raw and raw[-1] in ".,)":
        trailing = raw[-1] + trailing
        raw = raw[:-1]
    redacted = mask_url(
        raw,
        token="[REDACTED]",
        invalid="[REDACTED_URL]",
        require_scheme=False,
    )
    return redacted + trailing


def _active_secrets() -> tuple[str, ...]:
    global _secret_lookup_failure_logged
    try:
        from .secrets import active_secret_values

        return active_secret_values()
    except Exception as exc:  # noqa: BLE001 - redaction must never raise
        if not _secret_lookup_failure_logged:
            _secret_lookup_failure_logged = True
            # Class name only: the message could itself carry secret material.
            logger.warning(
                "Registered secrets are unavailable for redaction (%s); "
                "only explicit and pattern-based redaction applies",
                type(exc).__name__,
            )
        return ()


def redact_sensitive_text(value: Any, *, secret_values: tuple[str | None, ...] = ()) -> str:
    text = str(value or "")
    for secret in sorted((value for value in secret_values if value), key=len, reverse=True):
        text = text.replace(secret, "[REDACTED]")
    for secret in _active_secrets():
        if secret and secret in text:
            text = text.replace(secret, "[REDACTED]")

    lowered = text.lower()
    if not any(hint in lowered for hint in _REDACTION_HINTS):
        return text

    text = _URL_RE.sub(_redact_url, text)
    text = _AUTH_RE.sub(r"\1[REDACTED]", text)
    text = _BEARER_RE.sub("Bearer [REDACTED]", text)
    return _CREDENTIAL_RE.sub(r"\1[REDACTED]", text)
