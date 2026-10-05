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


def _redact_url(match: re.Match[str]) -> str:
    raw = match.group(0)
    trailing = ""
    while raw and raw[-1] in ".,)":
        trailing = raw[-1] + trailing
        raw = raw[:-1]
    try:
        parsed = urlsplit(raw)
        port = parsed.port
    except ValueError:
        return "[REDACTED_URL]" + trailing
    if not parsed.hostname:
        return "[REDACTED_URL]" + trailing
    hostname = parsed.hostname
    host = f"[{hostname}]" if ":" in hostname else hostname
    if port is not None:
        host = f"{host}:{port}"
    if parsed.username is not None or parsed.password is not None:
        host = f"[REDACTED]@{host}"
    query = "[REDACTED]" if parsed.query else ""
    fragment = "[REDACTED]" if parsed.fragment else ""
    return urlunsplit((parsed.scheme, host, parsed.path, query, fragment)) + trailing


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
