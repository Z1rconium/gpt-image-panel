"""Settings, secret references, overall config, and API preset persistence.
Everything here reads or writes the settings_kv table."""

import json
import logging
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Iterator, Sequence

from ...core import settings as config
from ...core.api_paths import default_model_for_api_path, normalize_api_preset
from ...core.secrets import configured_secret_ids
from ...core.utils import utc_now
from ...core.validators import (
    get_env_var_ref_name,
    is_malformed_env_var_ref,
    normalize_secret_env_ref_or_plaintext,
    normalize_r2_endpoint_url,
    normalize_socks5_proxy_url,
    normalize_webhook_url,
)
from .connection import (
    _connect,
    _get_setting_value,
    _secure_data_storage_permissions,
    _set_setting_value,
    _transaction,
)
from .schema import (
    _ensure_database,
)

from .constants import (
    AI_ASSISTANT_SETTINGS_KEY,
    DATA_DIR_MODE,
    DATA_FILE_MODE,
    DATA_PERMISSION_CHECK_INTERVAL_SECONDS,
    EditSourceQueueFullError,
    GALLERY_COLUMNS,
    GALLERY_COUNT_CACHE_SECONDS,
    GALLERY_FTS_MIN_QUERY_LENGTH,
    GALLERY_FTS_VERSION,
    GALLERY_FTS_VERSION_KEY,
    GALLERY_IMPORT_BATCH_SIZE,
    GALLERY_JOB_COLUMNS,
    GALLERY_ORPHAN_FILE_TTL_SECONDS,
    GALLERY_ORPHAN_GC_BATCH_SIZE,
    GALLERY_PAGE_ANCHOR_INTERVAL_PAGES,
    GALLERY_PAGE_ANCHOR_INVALIDATING_UPDATE_FIELDS,
    GALLERY_PAGE_ANCHOR_MAX_PER_QUERY,
    GALLERY_PAGE_ANCHOR_SMALL_OFFSET_THRESHOLD,
    GALLERY_SYNC_BATCH_SIZE,
    GALLERY_TOTAL_BYTES_CACHE_SECONDS,
    GENERATE_JOB_COLUMNS,
    IMAGE_JOB_UNIT_COLUMNS,
    INTEGER_GALLERY_COLUMNS,
    INTEGER_GENERATE_JOB_COLUMNS,
    ImageJobQueueFullError,
    MAX_PERSISTED_JOB_TEXT_CHARS,
    NODEIMAGE_SETTINGS_KEY,
    PROMPT_OPTIMIZER_SETTINGS_KEY,
    PROMPT_SNIPPET_COLUMNS,
    R2_BACKUP_SETTINGS_KEY,
    REQUIRED_GALLERY_COLUMNS,
    SETTINGS_ACTIVE_PRESET_KEY,
    SQLITE_IN_CLAUSE_CHUNK_SIZE,
    SQLITE_TIMEOUT_SECONDS,
    THUMBNAIL_CPU_SLOT_LEASE_SECONDS,
    THUMBNAIL_JOB_LEASE_SECONDS,
    THUMBNAIL_JOB_MAX_ATTEMPTS,
    UPSTREAM_SOCKS5_PROXY_KEY,
    WEBHOOK_URL_KEY,
    WORKER_METRIC_SNAPSHOT_TTL_SECONDS,
    _GALLERY_BYTES_CACHE_MAX_SIZE,
    _GALLERY_COUNT_CACHE_MAX_SIZE,
    _GALLERY_INTERNAL_COLUMNS,
)

logger = logging.getLogger(__name__)


def _normalize_stored_api_key(value: str | None) -> str:
    normalized = str(value or "").strip()
    if get_env_var_ref_name(normalized):
        return normalized
    try:
        return normalize_secret_env_ref_or_plaintext(normalized, field_name="API key")
    except ValueError:
        return normalized


def _normalize_stored_socks5_proxy(value: str | None) -> str:
    normalized = str(value or "").strip()
    if get_env_var_ref_name(normalized):
        return normalized
    try:
        return normalize_secret_env_ref_or_plaintext(normalized, field_name="SOCKS5 proxy URL")
    except ValueError:
        return normalized


def _normalize_stored_webhook_url(value: str | None) -> str:
    normalized = str(value or "").strip()
    if get_env_var_ref_name(normalized):
        return normalized
    try:
        return normalize_secret_env_ref_or_plaintext(normalized, field_name="Webhook URL")
    except ValueError:
        return normalized


def _normalize_stored_r2_access_key_id(value: str | None) -> str:
    normalized = str(value or "").strip()
    if get_env_var_ref_name(normalized):
        return normalized
    try:
        return normalize_secret_env_ref_or_plaintext(normalized, field_name="R2 access key ID")
    except ValueError:
        return normalized


def _normalize_stored_r2_secret_access_key(value: str | None) -> str:
    normalized = str(value or "").strip()
    if get_env_var_ref_name(normalized):
        return normalized
    try:
        return normalize_secret_env_ref_or_plaintext(normalized, field_name="R2 secret access key")
    except ValueError:
        return normalized


def _normalize_stored_nodeimage_api_key(value: str | None) -> str:
    normalized = str(value or "").strip()
    if get_env_var_ref_name(normalized):
        return normalized
    try:
        return normalize_secret_env_ref_or_plaintext(
            normalized,
            field_name="NodeImage API key",
        )
    except ValueError:
        return normalized


def _default_secret_reference(secret_id: str, value: str | None) -> str:
    if secret_id in configured_secret_ids():
        return secret_id
    normalized = str(value or "").strip()
    if get_env_var_ref_name(normalized):
        return normalized
    return ""


def _default_env_backed_secret_reference(
    secret_id: str,
    env_var: str,
    value: str | None,
) -> str:
    if secret_id in configured_secret_ids():
        return secret_id
    normalized = str(value or "").strip()
    if not normalized:
        return ""
    env_ref = get_env_var_ref_name(normalized)
    if env_ref:
        return f"${{{env_ref}}}"
    return f"${{{env_var}}}"


def _default_settings() -> dict:
    return {
        "active_preset_id": "default",
        "upstream_socks5_proxy": _default_secret_reference(
            "builtin-upstream-proxy",
            config.DEFAULT_UPSTREAM_SOCKS5_PROXY,
        ),
        "webhook_url": "",
        "presets": [
            {
                "id": "default",
                "name": "Default",
                "api_url": config.DEFAULT_API_URL.rstrip("/"),
                "api_key": _default_secret_reference(
                    "builtin-default-api-key",
                    config.DEFAULT_API_KEY,
                ),
                "api_path": config.DEFAULT_API_PATH,
                "default_model": default_model_for_api_path(config.DEFAULT_API_PATH),
                "default_response_format": "url",
            }
        ],
        "prompt_optimizer": _default_prompt_optimizer_settings(),
        "ai_assistant": _default_ai_assistant_settings(),
        "r2_backup": _default_r2_backup_settings(),
        "nodeimage": _default_nodeimage_settings(),
    }


def _coerce_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def _default_prompt_optimizer_settings() -> dict:
    return {
        "enabled": config.PROMPT_OPTIMIZER_ENABLED,
        "api_url": config.PROMPT_OPTIMIZER_API_URL,
        "api_key": _default_secret_reference(
            "builtin-prompt-optimizer-key",
            config.PROMPT_OPTIMIZER_API_KEY,
        ),
        "model": config.PROMPT_OPTIMIZER_MODEL,
        "timeout_seconds": config.PROMPT_OPTIMIZER_TIMEOUT_SECONDS,
    }


def _default_ai_assistant_settings() -> dict:
    return {
        "enabled": config.AI_ASSISTANT_ENABLED,
        "vision_model": config.AI_ASSISTANT_VISION_MODEL or config.PROMPT_OPTIMIZER_MODEL,
    }


def _normalize_r2_key_prefix(value: Any, default: str = "gallery/") -> str:
    raw = str(value if value is not None else default).strip()
    if not raw:
        return ""
    parts = [part for part in raw.strip("/").split("/") if part]
    return f"{'/'.join(parts)}/" if parts else ""


def _default_r2_backup_settings() -> dict:
    return {
        "enabled": config.R2_BACKUP_ENABLED,
        "endpoint_url": normalize_r2_endpoint_url(config.R2_ENDPOINT_URL),
        "bucket_name": config.R2_BUCKET_NAME,
        "region": config.R2_REGION or "auto",
        "key_prefix": _normalize_r2_key_prefix(config.R2_KEY_PREFIX),
        "access_key_id": _default_env_backed_secret_reference(
            "builtin-r2-access-key-id",
            "R2_ACCESS_KEY_ID",
            config.R2_ACCESS_KEY_ID,
        ),
        "secret_access_key": _default_env_backed_secret_reference(
            "builtin-r2-secret-access-key",
            "R2_SECRET_ACCESS_KEY",
            config.R2_SECRET_ACCESS_KEY,
        ),
        "sync_interval_hours": config.R2_SYNC_INTERVAL_HOURS,
    }


def _default_nodeimage_settings() -> dict:
    return {
        "enabled": False,
        "api_key": _default_env_backed_secret_reference(
            "builtin-nodeimage-api-key",
            "NODEIMAGE_API_KEY",
            config.NODEIMAGE_API_KEY,
        ),
    }


def _coerce_positive_int(value, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _coerce_non_negative_int(value, default: int) -> int:
    if value is None:
        return default
    if isinstance(value, float) and not value.is_integer():
        return 0
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return 0
    return parsed if parsed >= 0 else 0


def _coerce_iso_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _normalize_prompt_optimizer_settings(settings: dict | None) -> dict:
    default = _default_prompt_optimizer_settings()
    if not isinstance(settings, dict):
        return default
    return {
        "enabled": _coerce_bool(settings.get("enabled"), default["enabled"]),
        "api_url": str(settings.get("api_url") or "").strip(),
        "api_key": _normalize_stored_api_key(settings.get("api_key")),
        "model": str(settings.get("model") or default["model"]).strip()
        or default["model"],
        "timeout_seconds": _coerce_positive_int(
            settings.get("timeout_seconds"),
            default["timeout_seconds"],
        ),
    }


def _normalize_ai_assistant_settings(settings: dict | None) -> dict:
    default = _default_ai_assistant_settings()
    if not isinstance(settings, dict):
        return default
    vision_model = (
        str(settings.get("vision_model") or default["vision_model"] or config.PROMPT_OPTIMIZER_MODEL).strip()
        or config.PROMPT_OPTIMIZER_MODEL
    )
    return {
        "enabled": _coerce_bool(settings.get("enabled"), default["enabled"]),
        "vision_model": vision_model,
    }


def _normalize_r2_backup_settings(settings: dict | None) -> dict:
    default = _default_r2_backup_settings()
    if not isinstance(settings, dict):
        return default
    endpoint_url = normalize_r2_endpoint_url(settings.get("endpoint_url") or "")
    bucket_name = str(settings.get("bucket_name") or "").strip()
    access_key_id = _normalize_stored_r2_access_key_id(settings.get("access_key_id"))
    secret_access_key = _normalize_stored_r2_secret_access_key(
        settings.get("secret_access_key")
    )
    return {
        "enabled": default["enabled"]
        or _coerce_bool(settings.get("enabled"), default["enabled"]),
        "endpoint_url": endpoint_url or default["endpoint_url"],
        "bucket_name": bucket_name or default["bucket_name"],
        "region": str(settings.get("region") or default["region"]).strip()
        or default["region"],
        "key_prefix": _normalize_r2_key_prefix(
            settings.get("key_prefix"),
            default["key_prefix"],
        ),
        "access_key_id": access_key_id or default["access_key_id"],
        "secret_access_key": secret_access_key or default["secret_access_key"],
        "sync_interval_hours": _coerce_non_negative_int(
            settings.get("sync_interval_hours"),
            default["sync_interval_hours"],
        ),
    }


def _has_r2_backup_storage_values(settings: dict | None) -> bool:
    if not isinstance(settings, dict):
        return False
    if _coerce_bool(settings.get("enabled"), False):
        return True
    if "sync_interval_hours" in settings:
        try:
            if int(settings.get("sync_interval_hours") or 0) > 0:
                return True
        except (TypeError, ValueError):
            return True
    return any(
        str(settings.get(key) or "").strip()
        for key in (
            "endpoint_url",
            "bucket_name",
            "access_key_id",
            "secret_access_key",
        )
    )


def _store_r2_backup_settings_on_conn(conn: sqlite3.Connection, settings: dict):
    _set_setting_value(conn, R2_BACKUP_SETTINGS_KEY, json.dumps(settings))
    conn.commit()
    _secure_data_storage_permissions()


def _load_r2_backup_settings_from_conn(conn: sqlite3.Connection) -> dict:
    raw = _get_setting_value(conn, R2_BACKUP_SETTINGS_KEY)
    if raw:
        try:
            parsed = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return _default_r2_backup_settings()

        settings = _normalize_r2_backup_settings(parsed)
        if (
            not _has_r2_backup_storage_values(parsed)
            and _has_r2_backup_storage_values(settings)
        ):
            _store_r2_backup_settings_on_conn(conn, settings)
        return settings

    settings = _default_r2_backup_settings()
    if _has_r2_backup_storage_values(settings):
        _store_r2_backup_settings_on_conn(conn, settings)
    return settings


def _normalize_nodeimage_settings(settings: dict | None) -> dict:
    default = _default_nodeimage_settings()
    if not isinstance(settings, dict):
        return default
    api_key = _normalize_stored_nodeimage_api_key(settings.get("api_key"))
    return {
        "enabled": _coerce_bool(settings.get("enabled"), default["enabled"]),
        "api_key": api_key or default["api_key"],
    }


def _has_nodeimage_storage_values(settings: dict | None) -> bool:
    if not isinstance(settings, dict):
        return False
    return _coerce_bool(settings.get("enabled"), False) or bool(
        str(settings.get("api_key") or "").strip()
    )


def _store_nodeimage_settings_on_conn(conn: sqlite3.Connection, settings: dict) -> None:
    _set_setting_value(conn, NODEIMAGE_SETTINGS_KEY, json.dumps(settings))
    conn.commit()
    _secure_data_storage_permissions()


def _load_nodeimage_settings_from_conn(conn: sqlite3.Connection) -> dict:
    raw = _get_setting_value(conn, NODEIMAGE_SETTINGS_KEY)
    if raw:
        try:
            parsed = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return _default_nodeimage_settings()
        settings = _normalize_nodeimage_settings(parsed)
        if (
            not _has_nodeimage_storage_values(parsed)
            and _has_nodeimage_storage_values(settings)
        ):
            _store_nodeimage_settings_on_conn(conn, settings)
        return settings

    settings = _default_nodeimage_settings()
    if _has_nodeimage_storage_values(settings):
        _store_nodeimage_settings_on_conn(conn, settings)
    return settings


def _overall_config_rows(conn: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT name, env_value, override_value, is_env_set, updated_at, override_updated_at
        FROM overall_config_values
        """
    ).fetchall()
    return {
        row["name"]: {
            "name": row["name"],
            "env_value": row["env_value"],
            "override_value": row["override_value"],
            "is_env_set": bool(row["is_env_set"]),
            "updated_at": row["updated_at"],
            "override_updated_at": row["override_updated_at"],
        }
        for row in rows
    }


def sync_overall_config_env_values(env_values: dict[str, tuple[str, bool]]) -> dict[str, dict[str, Any]]:
    from ...core.overall_config import OVERALL_CONFIG_BY_NAME

    _ensure_database()
    now = utc_now()
    scrubbed_plaintext_overrides: list[str] = []
    with _connect() as conn:
        with _transaction(conn):
            for name, (env_value, is_env_set) in env_values.items():
                spec = OVERALL_CONFIG_BY_NAME.get(name)
                stored_env_value = "" if spec and spec.secret else str(env_value or "")
                conn.execute(
                    """
                    INSERT INTO overall_config_values (
                        name,
                        env_value,
                        override_value,
                        is_env_set,
                        updated_at,
                        override_updated_at
                    )
                    VALUES (?, ?, NULL, ?, ?, NULL)
                    ON CONFLICT(name) DO UPDATE SET
                        env_value = excluded.env_value,
                        is_env_set = excluded.is_env_set,
                        updated_at = excluded.updated_at
                    """,
                    (name, stored_env_value, 1 if is_env_set else 0, now),
                )
                if (
                    spec
                    and spec.secret
                    and not config.ALLOW_PLAINTEXT_SECRETS
                ):
                    row = conn.execute(
                        "SELECT override_value FROM overall_config_values WHERE name = ?",
                        (name,),
                    ).fetchone()
                    override = str(row["override_value"] or "") if row else ""
                    if override and not get_env_var_ref_name(override):
                        conn.execute(
                            """
                            UPDATE overall_config_values
                            SET override_value = NULL, override_updated_at = NULL
                            WHERE name = ?
                            """,
                            (name,),
                        )
                        scrubbed_plaintext_overrides.append(name)
            rows = _overall_config_rows(conn)
    if scrubbed_plaintext_overrides:
        logger.warning(
            "Removed legacy plaintext secret overrides from SQLite for %s; "
            "rotate any affected credentials and replace them with environment references",
            ", ".join(sorted(scrubbed_plaintext_overrides)),
        )
    _secure_data_storage_permissions()
    return rows


def list_overall_config_values() -> dict[str, dict[str, Any]]:
    _ensure_database()
    with _connect() as conn:
        return _overall_config_rows(conn)


def save_overall_config_overrides(
    updates: dict[str, str | None],
) -> dict[str, dict[str, Any]]:
    from ...core.overall_config import OVERALL_CONFIG_BY_NAME

    _ensure_database()
    now = utc_now()
    with _connect() as conn:
        with _transaction(conn):
            for name, value in updates.items():
                spec = OVERALL_CONFIG_BY_NAME.get(name)
                if (
                    spec
                    and spec.secret
                    and value
                    and is_malformed_env_var_ref(value)
                ):
                    raise ValueError(
                        f"{name} env ref must be formatted as ${{ENV_VAR_NAME}}"
                    )
                if (
                    spec
                    and spec.secret
                    and value
                    and not get_env_var_ref_name(value)
                    and not config.ALLOW_PLAINTEXT_SECRETS
                ):
                    raise ValueError(
                        f"{name} must use ${{ENV_VAR_NAME}} unless "
                        "ALLOW_PLAINTEXT_SECRETS=true"
                    )
                conn.execute(
                    """
                    INSERT INTO overall_config_values (
                        name,
                        env_value,
                        override_value,
                        is_env_set,
                        updated_at,
                        override_updated_at
                    )
                    VALUES (?, '', ?, 0, ?, ?)
                    ON CONFLICT(name) DO UPDATE SET
                        override_value = excluded.override_value,
                        override_updated_at = excluded.override_updated_at
                    """,
                    (
                        name,
                        value,
                        now,
                        now if value is not None else None,
                    ),
                )
            rows = _overall_config_rows(conn)
    _secure_data_storage_permissions()
    return rows


def _normalize_settings(settings: dict | None) -> dict:
    if not isinstance(settings, dict):
        return _default_settings()

    upstream_socks5_proxy = (
        _normalize_stored_socks5_proxy(settings.get("upstream_socks5_proxy"))
        if settings.get("upstream_socks5_proxy") is not None
        else _normalize_stored_socks5_proxy(config.DEFAULT_UPSTREAM_SOCKS5_PROXY)
    )
    webhook_url = (
        _normalize_stored_webhook_url(settings.get("webhook_url"))
        if settings.get("webhook_url") is not None
        else ""
    )
    r2_backup = (
        _normalize_r2_backup_settings(settings.get("r2_backup"))
        if "r2_backup" in settings
        else _default_r2_backup_settings()
    )
    nodeimage = (
        _normalize_nodeimage_settings(settings.get("nodeimage"))
        if "nodeimage" in settings
        else _default_nodeimage_settings()
    )
    ai_assistant = (
        _normalize_ai_assistant_settings(settings.get("ai_assistant"))
        if "ai_assistant" in settings
        else _default_ai_assistant_settings()
    )

    raw_presets = settings.get("presets")
    if not isinstance(raw_presets, list):
        default_settings = _default_settings()
        default_settings["upstream_socks5_proxy"] = upstream_socks5_proxy
        default_settings["webhook_url"] = webhook_url
        default_settings["ai_assistant"] = ai_assistant
        default_settings["r2_backup"] = r2_backup
        default_settings["nodeimage"] = nodeimage
        return default_settings

    presets: list[dict] = []
    seen_ids: set[str] = set()
    for index, preset in enumerate(raw_presets):
        if not isinstance(preset, dict):
            continue

        normalized_preset = normalize_api_preset(preset, f"preset-{index + 1}")
        normalized_preset["api_key"] = _normalize_stored_api_key(
            normalized_preset.get("api_key")
        )
        preset_id = normalized_preset["id"]
        if preset_id in seen_ids:
            continue
        seen_ids.add(preset_id)
        presets.append(normalized_preset)

    if not presets:
        default_settings = _default_settings()
        default_settings["upstream_socks5_proxy"] = upstream_socks5_proxy
        default_settings["webhook_url"] = webhook_url
        default_settings["ai_assistant"] = ai_assistant
        default_settings["r2_backup"] = r2_backup
        default_settings["nodeimage"] = nodeimage
        return default_settings

    active_preset_id = str(settings.get("active_preset_id") or presets[0]["id"])
    if not any(preset["id"] == active_preset_id for preset in presets):
        active_preset_id = presets[0]["id"]

    return {
        "active_preset_id": active_preset_id,
        "upstream_socks5_proxy": upstream_socks5_proxy,
        "webhook_url": webhook_url,
        "presets": presets,
        "prompt_optimizer": (
            _normalize_prompt_optimizer_settings(settings.get("prompt_optimizer"))
            if "prompt_optimizer" in settings
            else None
        ),
        "ai_assistant": ai_assistant,
        "r2_backup": r2_backup,
        "nodeimage": nodeimage,
    }


def _replace_settings_on_conn(conn: sqlite3.Connection, settings: dict):
    normalized = _normalize_settings(settings)
    now = utc_now()

    conn.execute("DELETE FROM api_presets")
    for position, preset in enumerate(normalized["presets"]):
        conn.execute(
            """
            INSERT INTO api_presets (
                id,
                name,
                api_url,
                api_key,
                api_path,
                default_model,
                default_response_format,
                position,
                created_at,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                preset["id"],
                preset["name"],
                preset["api_url"],
                preset["api_key"],
                preset["api_path"],
                preset["default_model"],
                preset["default_response_format"],
                position,
                now,
                now,
            ),
        )

    _set_setting_value(
        conn,
        SETTINGS_ACTIVE_PRESET_KEY,
        normalized["active_preset_id"],
    )
    _set_setting_value(
        conn,
        UPSTREAM_SOCKS5_PROXY_KEY,
        normalized.get("upstream_socks5_proxy", ""),
    )
    _set_setting_value(
        conn,
        WEBHOOK_URL_KEY,
        normalized.get("webhook_url", ""),
    )
    optimizer = normalized.get("prompt_optimizer")
    if optimizer is not None:
        _set_setting_value(conn, PROMPT_OPTIMIZER_SETTINGS_KEY, json.dumps(optimizer))
    ai_assistant = normalized.get("ai_assistant")
    if ai_assistant is not None:
        _set_setting_value(conn, AI_ASSISTANT_SETTINGS_KEY, json.dumps(ai_assistant))
    r2_backup = normalized.get("r2_backup")
    if r2_backup is not None:
        _set_setting_value(conn, R2_BACKUP_SETTINGS_KEY, json.dumps(r2_backup))
    nodeimage = normalized.get("nodeimage")
    if nodeimage is not None:
        _set_setting_value(conn, NODEIMAGE_SETTINGS_KEY, json.dumps(nodeimage))


def _load_settings_from_conn(conn: sqlite3.Connection) -> dict | None:
    rows = conn.execute(
        """
        SELECT id, name, api_url, api_key, api_path, default_model, default_response_format
        FROM api_presets
        ORDER BY position ASC, id ASC
        """
    ).fetchall()
    if not rows:
        return None

    presets = [
        {
            "id": row["id"],
            "name": row["name"],
            "api_url": row["api_url"],
            "api_key": row["api_key"],
            "api_path": row["api_path"],
            "default_model": row["default_model"],
            "default_response_format": row["default_response_format"],
        }
        for row in rows
    ]
    active_preset_id = _get_setting_value(conn, SETTINGS_ACTIVE_PRESET_KEY)
    if not active_preset_id:
        active_preset_id = presets[0]["id"]
    upstream_socks5_proxy = _get_setting_value(conn, UPSTREAM_SOCKS5_PROXY_KEY)
    if upstream_socks5_proxy is None:
        upstream_socks5_proxy = config.DEFAULT_UPSTREAM_SOCKS5_PROXY
    webhook_url = _get_setting_value(conn, WEBHOOK_URL_KEY) or ""

    optimizer_json = _get_setting_value(conn, PROMPT_OPTIMIZER_SETTINGS_KEY)
    optimizer = None
    if optimizer_json:
        try:
            optimizer = _normalize_prompt_optimizer_settings(json.loads(optimizer_json))
        except (json.JSONDecodeError, TypeError):
            optimizer = _default_prompt_optimizer_settings()
    else:
        optimizer = _default_prompt_optimizer_settings()

    assistant_json = _get_setting_value(conn, AI_ASSISTANT_SETTINGS_KEY)
    ai_assistant = None
    if assistant_json:
        try:
            ai_assistant = _normalize_ai_assistant_settings(json.loads(assistant_json))
        except (json.JSONDecodeError, TypeError):
            ai_assistant = _default_ai_assistant_settings()
    else:
        ai_assistant = _default_ai_assistant_settings()

    r2_backup = _load_r2_backup_settings_from_conn(conn)
    nodeimage = _load_nodeimage_settings_from_conn(conn)

    return _normalize_settings(
        {
            "active_preset_id": active_preset_id,
            "upstream_socks5_proxy": upstream_socks5_proxy,
            "webhook_url": webhook_url,
            "presets": presets,
            "prompt_optimizer": optimizer,
            "ai_assistant": ai_assistant,
            "r2_backup": r2_backup,
            "nodeimage": nodeimage,
        }
    )
