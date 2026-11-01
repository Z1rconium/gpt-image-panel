import json
import os
import re
from dataclasses import dataclass
from threading import RLock
from typing import Literal
from urllib.parse import urlsplit

from . import settings as config


SecretPurpose = Literal[
    "upstream_api",
    "prompt_optimizer",
    "upstream_proxy",
    "webhook_url",
    "r2_access_key_id",
    "r2_secret_access_key",
    "nodeimage_api_key",
]

ALLOWED_SECRET_PURPOSES: frozenset[str] = frozenset(
    {
        "upstream_api",
        "prompt_optimizer",
        "upstream_proxy",
        "webhook_url",
        "r2_access_key_id",
        "r2_secret_access_key",
        "nodeimage_api_key",
    }
)
SECRET_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{2,127}$")
ENV_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
ENV_VAR_REF_RE = re.compile(r"^\$\{([A-Za-z_][A-Za-z0-9_]*)\}$")


def get_env_var_ref_name(value: str | None) -> str | None:
    match = ENV_VAR_REF_RE.match(str(value or "").strip())
    return match.group(1) if match else None


def is_malformed_env_var_ref(value: str | None) -> bool:
    normalized = str(value or "").strip()
    return bool(normalized) and ("${" in normalized or "}" in normalized) and not get_env_var_ref_name(normalized)


def resolve_env_var_ref(value: str | None) -> str:
    normalized = str(value or "").strip()
    env_var = get_env_var_ref_name(normalized)
    if env_var:
        return os.getenv(env_var, "").strip()
    return normalized


def normalize_secret_env_ref_or_plaintext(
    value: str | None,
    *,
    field_name: str,
    normalizer=None,
) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        return ""
    if is_malformed_env_var_ref(normalized):
        raise ValueError(
            f"{field_name} env ref must be formatted as ${{ENV_VAR_NAME}}."
        )

    env_var = get_env_var_ref_name(normalized)
    if env_var:
        if not config.ALLOW_LEGACY_ENV_REFS and env_var not in declared_env_names():
            raise ValueError(
                f"{field_name} references environment variable {env_var}, which is not declared "
                "in SECRET_REGISTRY_JSON. Declare it with a purpose and origin and use its secret_id."
            )
        return f"${{{env_var}}}"

    if normalized in configured_secret_ids():
        return normalize_secret_id(normalized, field_name=field_name)

    if not config.ALLOW_PLAINTEXT_SECRETS:
        raise ValueError(
            f"{field_name} must use ${{ENV_VAR_NAME}} unless "
            "ALLOW_PLAINTEXT_SECRETS=true."
        )

    return normalizer(normalized) if normalizer is not None else normalized


class SecretRegistryError(ValueError):
    pass


@dataclass(frozen=True)
class SecretEntry:
    secret_id: str
    purpose: str
    origin: str
    env_name: str | None = None
    value: str | None = None

    def resolve(self) -> str:
        if self.env_name:
            return os.getenv(self.env_name, "").strip()
        return str(self.value or "").strip()


_registry: dict[str, SecretEntry] = {}
_env_bindings: dict[str, tuple[SecretEntry, ...]] = {}
_registry_lock = RLock()
_registry_generation = 0
_ACTIVE_SECRET_CONFIG_NAMES = (
    "ACCESS_KEY",
    "CDN_SIGNING_SECRET",
    "WEBHOOK_SIGNING_SECRET",
    "DEFAULT_API_KEY",
    "PROMPT_OPTIMIZER_API_KEY",
    "DEFAULT_UPSTREAM_SOCKS5_PROXY",
    "R2_ACCESS_KEY_ID",
    "R2_SECRET_ACCESS_KEY",
    "NODEIMAGE_API_KEY",
)
_active_secret_values_cache: tuple[
    int,
    tuple[str, ...],
    tuple[str, ...],
] | None = None


def invalidate_active_secret_values_cache() -> None:
    global _active_secret_values_cache
    with _registry_lock:
        _active_secret_values_cache = None


def canonical_origin(url: str | None) -> str:
    value = str(url or "").strip()
    if not value:
        raise SecretRegistryError("target URL is required")
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as exc:
        raise SecretRegistryError("target URL has an invalid port") from exc
    scheme = parsed.scheme.lower()
    if scheme not in {"https", "http", "socks5"}:
        raise SecretRegistryError("target URL has an unsupported scheme")
    if not parsed.hostname:
        raise SecretRegistryError("target URL has no hostname")
    if port is None:
        if scheme == "https":
            port = 443
        elif scheme == "http":
            port = 80
        else:
            raise SecretRegistryError("SOCKS5 target URL must declare a port")
    hostname = parsed.hostname.lower().rstrip(".")
    host = f"[{hostname}]" if ":" in hostname else hostname
    return f"{scheme}://{host}:{port}"


def same_origin(left: str | None, right: str | None) -> bool:
    try:
        return canonical_origin(left) == canonical_origin(right)
    except SecretRegistryError:
        return False


def _validate_entry(secret_id: str, raw: object) -> SecretEntry:
    if not SECRET_ID_RE.fullmatch(secret_id):
        raise SecretRegistryError(
            "secret_id must be 3-128 ASCII letters, digits, dots, underscores, or hyphens"
        )
    if not isinstance(raw, dict):
        raise SecretRegistryError(f"Secret registry entry '{secret_id}' must be an object")
    purpose = str(raw.get("purpose") or "").strip()
    if purpose not in ALLOWED_SECRET_PURPOSES:
        raise SecretRegistryError(f"Secret registry entry '{secret_id}' has an invalid purpose")
    origin = canonical_origin(str(raw.get("origin") or ""))
    env_name = str(raw.get("env") or "").strip()
    if not ENV_NAME_RE.fullmatch(env_name):
        raise SecretRegistryError(f"Secret registry entry '{secret_id}' has an invalid env name")
    return SecretEntry(
        secret_id=secret_id,
        purpose=purpose,
        origin=origin,
        env_name=env_name,
    )


def _builtin_candidates() -> tuple[tuple[str, str, str, str], ...]:
    return (
        (
            "builtin-default-api-key",
            "upstream_api",
            config.DEFAULT_API_URL,
            config.DEFAULT_API_KEY,
        ),
        (
            "builtin-prompt-optimizer-key",
            "prompt_optimizer",
            config.PROMPT_OPTIMIZER_API_URL,
            config.PROMPT_OPTIMIZER_API_KEY,
        ),
        (
            "builtin-upstream-proxy",
            "upstream_proxy",
            config.DEFAULT_UPSTREAM_SOCKS5_PROXY,
            config.DEFAULT_UPSTREAM_SOCKS5_PROXY,
        ),
        (
            "builtin-r2-access-key-id",
            "r2_access_key_id",
            config.R2_ENDPOINT_URL,
            config.R2_ACCESS_KEY_ID,
        ),
        (
            "builtin-r2-secret-access-key",
            "r2_secret_access_key",
            config.R2_ENDPOINT_URL,
            config.R2_SECRET_ACCESS_KEY,
        ),
        (
            "builtin-nodeimage-api-key",
            "nodeimage_api_key",
            "https://api.nodeimage.com",
            config.NODEIMAGE_API_KEY,
        ),
    )


def _builtin_env_bindings() -> dict[str, list[SecretEntry]]:
    """Startup config such as DEFAULT_API_KEY=${NAME} is operator-owned: it binds NAME to its purpose/origin."""
    bindings: dict[str, list[SecretEntry]] = {}
    for secret_id, purpose, target_url, value in _builtin_candidates():
        env_name = get_env_var_ref_name(value)
        if not env_name or not str(target_url or "").strip():
            continue
        try:
            origin = canonical_origin(target_url)
        except SecretRegistryError:
            continue
        bindings.setdefault(env_name, []).append(
            SecretEntry(secret_id=secret_id, purpose=purpose, origin=origin, env_name=env_name)
        )
    return bindings


def _builtin_entries() -> dict[str, SecretEntry]:
    # Direct startup secrets never pass through the web settings API. Stable IDs
    # let defaults use the same purpose/origin checks as operator-declared entries.
    candidates = _builtin_candidates()
    entries: dict[str, SecretEntry] = {}
    for secret_id, purpose, target_url, value in candidates:
        normalized_value = str(value or "").strip()
        if not normalized_value or not str(target_url or "").strip():
            continue
        # Legacy placeholders are deliberately not resolved. Operators must
        # declare them in SECRET_REGISTRY_JSON under an opaque ID instead.
        if "${" in normalized_value or "}" in normalized_value:
            continue
        try:
            origin = canonical_origin(target_url)
        except SecretRegistryError:
            continue
        entries[secret_id] = SecretEntry(
            secret_id=secret_id,
            purpose=purpose,
            origin=origin,
            value=normalized_value,
        )
    return entries


def configure_registry(raw_json: str | None = None) -> None:
    raw = str(raw_json if raw_json is not None else os.getenv("SECRET_REGISTRY_JSON", "")).strip()
    declared: dict[str, SecretEntry] = {}
    if raw:
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise SecretRegistryError("SECRET_REGISTRY_JSON must be valid JSON") from exc
        if not isinstance(payload, dict):
            raise SecretRegistryError("SECRET_REGISTRY_JSON must be a JSON object")
        for raw_id, raw_entry in payload.items():
            secret_id = str(raw_id).strip()
            declared[secret_id] = _validate_entry(secret_id, raw_entry)

    entries = _builtin_entries()
    overlap = entries.keys() & declared.keys()
    if overlap:
        raise SecretRegistryError(f"Reserved secret_id cannot be overridden: {sorted(overlap)[0]}")
    entries.update(declared)
    bindings = _builtin_env_bindings()
    for entry in declared.values():
        if entry.env_name:
            bindings.setdefault(entry.env_name, []).append(entry)
    with _registry_lock:
        global _registry, _env_bindings, _registry_generation, _active_secret_values_cache
        _registry = entries
        _env_bindings = {name: tuple(items) for name, items in bindings.items()}
        _registry_generation += 1
        _active_secret_values_cache = None


def declared_env_names() -> frozenset[str]:
    with _registry_lock:
        return frozenset(_env_bindings)


def resolve_env_ref(
    env_name: str,
    *,
    purpose: SecretPurpose,
    target_url: str,
    field_name: str = "credential",
) -> str:
    """Resolve ${ENV_NAME} only through a declared binding for this purpose and origin.

    Unknown names are rejected before the process environment is read.
    """
    if config.ALLOW_LEGACY_ENV_REFS:
        value = os.getenv(env_name, "").strip()
        if not value:
            raise SecretRegistryError(f"{field_name} environment variable {env_name} is not set or empty.")
        return value
    with _registry_lock:
        candidates = _env_bindings.get(env_name, ())
    if not candidates:
        raise SecretRegistryError(
            f"{field_name} environment variable {env_name} is not declared in SECRET_REGISTRY_JSON. "
            "Declare it with a purpose and origin, then reference its secret_id."
        )
    error: SecretRegistryError | None = None
    for entry in candidates:
        try:
            _check_entry_binding(
                entry, purpose=purpose, target_url=target_url
            )
        except SecretRegistryError as exc:
            error = exc
            continue
        value = entry.resolve()
        if value:
            return value
        error = SecretRegistryError(f"{field_name} environment variable {env_name} is not set or empty.")
    raise SecretRegistryError(f"{field_name}: {error}") from error


def configured_secret_ids() -> tuple[str, ...]:
    with _registry_lock:
        return tuple(sorted(_registry))


def normalize_secret_id(value: str | None, *, field_name: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        return ""
    if "${" in normalized or "}" in normalized:
        raise SecretRegistryError(f"{field_name} must use a predeclared secret_id")
    if not SECRET_ID_RE.fullmatch(normalized):
        raise SecretRegistryError(f"{field_name} must be a valid predeclared secret_id")
    with _registry_lock:
        if normalized not in _registry:
            raise SecretRegistryError(f"{field_name} references an unknown secret_id")
    return normalized


def secret_entry(secret_id: str) -> SecretEntry:
    with _registry_lock:
        entry = _registry.get(str(secret_id or "").strip())
    if entry is None:
        raise SecretRegistryError("unknown secret_id")
    return entry


def validate_secret_binding(
    secret_id: str,
    *,
    purpose: SecretPurpose,
    target_url: str,
) -> SecretEntry:
    return _check_entry_binding(
        secret_entry(secret_id), purpose=purpose, target_url=target_url
    )


def _check_entry_binding(
    entry: SecretEntry,
    *,
    purpose: SecretPurpose,
    target_url: str,
) -> SecretEntry:
    if entry.purpose != purpose:
        raise SecretRegistryError("secret_id is not permitted for this purpose")
    if entry.origin != canonical_origin(target_url):
        raise SecretRegistryError("secret_id is not bound to the target origin")

    return entry


def resolve_secret(
    secret_id: str,
    *,
    purpose: SecretPurpose,
    target_url: str,
) -> str:
    entry = validate_secret_binding(
        secret_id,
        purpose=purpose,
        target_url=target_url,
    )
    value = entry.resolve()
    if not value:
        raise SecretRegistryError("secret_id resolves to an empty value")
    return value


def resolve_secret_reference(
    value: object,
    *,
    purpose: SecretPurpose,
    target_url: str,
    field_name: str,
) -> str:
    """Resolve a settings secret reference without exposing its source details.

    Settings may contain an empty value, an explicit environment reference, a
    predeclared registry ID, or (when plaintext storage is enabled at the
    settings boundary) a literal value. Registry IDs are always validated for
    purpose and origin before resolution.
    """

    raw = str(value or "").strip()
    if not raw:
        return ""

    env_var = get_env_var_ref_name(raw)
    if env_var:
        return resolve_env_ref(
            env_var,
            purpose=purpose,
            target_url=target_url,
            field_name=field_name,
        )

    if "${" in raw or "}" in raw:
        raise SecretRegistryError(
            f"{field_name} env ref must be formatted as ${{ENV_VAR_NAME}}."
        )

    if raw in configured_secret_ids():
        try:
            return resolve_secret(
                raw,
                purpose=purpose,
                target_url=target_url,
            )
        except SecretRegistryError as exc:
            raise SecretRegistryError(f"{field_name}: {exc}") from exc

    return raw


def resolve_secret_reference_or_raise(
    value: object,
    *,
    purpose: SecretPurpose,
    target_url: str,
    field_name: str,
    error_type: type[Exception],
) -> str:
    """Resolve a settings secret reference, mapping registry errors to ``error_type``.

    Callers use this to translate ``SecretRegistryError`` into their own
    configuration-domain error without re-implementing the try/except each time.
    """
    try:
        return resolve_secret_reference(
            value,
            purpose=purpose,
            target_url=target_url,
            field_name=field_name,
        )
    except SecretRegistryError as exc:
        raise error_type(str(exc)) from exc


def active_secret_values() -> tuple[str, ...]:
    global _active_secret_values_cache

    config_key = tuple(
        str(getattr(config, name, "") or "").strip()
        for name in _ACTIVE_SECRET_CONFIG_NAMES
    )
    with _registry_lock:
        cache = _active_secret_values_cache
        if cache and cache[0] == _registry_generation and cache[1] == config_key:
            return cache[2]
        generation = _registry_generation
        entries = tuple(_registry.values())

    values: set[str] = set()
    for entry in entries:
        value = entry.resolve()
        if value:
            values.add(value)

    for normalized in config_key:
        if normalized:
            values.add(normalized)
    result = tuple(sorted(values, key=len, reverse=True))
    with _registry_lock:
        if _registry_generation == generation:
            _active_secret_values_cache = (generation, config_key, result)
    return result
