"""Row mapping and normalisation between SQLite rows and the schema models,
plus the gallery filter and sort SQL they feed."""

import hashlib
import json
import re
import secrets
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Iterator, Sequence
from urllib.parse import quote

from ...core import settings as config
from ...core.cdn import signed_media_url
from ...core.constants import ACTIVE_GENERATE_JOB_STATUSES
from ...core.utils import utc_now
from ...schemas.gallery import GalleryEntry, GalleryFilterOptions
from ...schemas.snippets import PromptSnippet
from ...core.media import (
    IMAGE_CONTENT_TYPE_FORMATS,
    IMAGE_EXTENSION_FORMATS,
    IMAGE_FILE_EXTENSIONS,
    IMAGE_FORMAT_CONTENT_TYPES,
    THUMBNAIL_CONTENT_TYPE,
    THUMBNAIL_EXTENSION,
    detect_image_format,
    generate_image_id,
    get_image_dimensions,
    image_dimension_metadata as _image_dimension_metadata,
    safe_image_path,
    safe_thumbnail_path,
    validate_image_header_bytes,
    validate_image_bytes,
)
from ..thumbnails import (
    create_thumbnail_temp as _create_thumbnail_temp_unlocked,
    create_thumbnail_temp_from_path as _create_thumbnail_temp_from_path_unlocked,
    delete_thumbnail as _delete_thumbnail_unlocked,
    promote_thumbnail_temp as _promote_thumbnail_temp_unlocked,
    thumbnail_filename_for_image as _thumbnail_filename_for_image,
    thumbnail_url_for_filename as _thumbnail_url_for_filename,
)
from . import state as db_state
from .connection import (
    _table_columns,
)

from .state import (
    _add_verified_thumbnail,
    _remove_verified_thumbnail,
    _clear_verified_thumbnails,
    _invalidate_filter_options_cache,
    _bump_filter_options_cache_version,
    _get_filter_options_cache_version,
    _invalidate_gallery_total_bytes_cache,
    _invalidate_gallery_count_cache,
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
    REAL_GALLERY_COLUMNS,
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


@dataclass(frozen=True)
class GalleryPage:
    total: int
    total_bytes: int
    page: int
    page_size: int
    total_pages: int
    has_prev: bool
    has_next: bool
    next_cursor: str | None
    prev_cursor: str | None
    images: list[GalleryEntry]
    filter_options: GalleryFilterOptions
    query_elapsed_ms: float = 0.0
    timings_ms: dict[str, float] = field(default_factory=dict)
    counts_included: bool = True
    filter_options_included: bool = True


@dataclass
class _PreparedGalleryFile:
    filename: str
    image_temp_path: Path
    image_format: str | None = None
    image_width: int | None = None
    image_height: int | None = None
    thumbnail_filename: str | None = None
    thumbnail_temp_path: Path | None = None


@dataclass(frozen=True)
class _GalleryFilterOptionsCacheEntry:
    version: int
    options: GalleryFilterOptions


@dataclass(frozen=True)
class _GalleryPaginationState:
    rows: list[sqlite3.Row]
    has_prev: bool
    has_next: bool
    effective_page: int
    total: int
    total_pages: int


@dataclass(frozen=True)
class _GalleryQueryComponents:
    where_sql: str
    params: list[Any]
    query_key: str
    requested_page: int
    page_size: int
    include_counts: bool
    include_filter_options: bool
    include_total_bytes: bool
    decoded_cursor: tuple[int, str] | None
    direction: str
    has_filters: bool


def _normalize_gallery_entry(entry: dict[str, Any]) -> dict[str, Any] | None:
    if not isinstance(entry, dict):
        return None

    entry_id = entry.get("id")
    filename = entry.get("filename")
    if not entry_id or not filename:
        return None

    normalized: dict[str, Any] = {
        "id": str(entry_id),
        "prompt": str(entry.get("prompt") or ""),
        "size": str(entry.get("size") or ""),
        "filename": str(filename),
        "created_at": str(entry.get("created_at") or utc_now()),
        "favorite": _normalize_gallery_favorite(entry.get("favorite")),
    }

    for column in GALLERY_COLUMNS:
        if column in REQUIRED_GALLERY_COLUMNS or column == "favorite":
            continue
        value = entry.get(column)
        if value is None:
            continue
        if column in INTEGER_GALLERY_COLUMNS:
            try:
                normalized[column] = int(value)
            except (TypeError, ValueError):
                continue
        elif column in REAL_GALLERY_COLUMNS:
            try:
                normalized[column] = float(value)
            except (TypeError, ValueError):
                continue
        elif column == "thumbnail_filename":
            thumbnail_filename = str(value)
            if safe_thumbnail_path(thumbnail_filename):
                normalized[column] = thumbnail_filename
        else:
            normalized[column] = str(value)

    return normalized


def _normalize_gallery_favorite(value: Any) -> int:
    if isinstance(value, bool):
        return 1 if value else 0
    if isinstance(value, int):
        return 1 if value else 0
    text = str(value or "").strip().lower()
    return 1 if text in {"1", "true", "yes", "on", "favorite", "favorited"} else 0


def _normalize_gallery_filter_bool(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return 1 if value else 0
    if isinstance(value, int):
        return 1 if value else 0
    normalized = str(value).strip().lower()
    if not normalized:
        return None
    if normalized in {"1", "true", "yes", "y", "on", "favorite", "favorited"}:
        return 1
    if normalized in {"0", "false", "no", "n", "off", "unfavorite", "unfavorited"}:
        return 0
    return None


def _gallery_row_values(entry: dict[str, Any]) -> tuple[Any, ...]:
    return tuple(entry.get(column) for column in GALLERY_COLUMNS)


def _gallery_thumbnail_status_for_row(
    row: sqlite3.Row,
    thumbnail_status_map: dict[str, str] | None = None,
) -> str:
    filename = str(row["filename"] or "")
    thumbnail_filename = str(row["thumbnail_filename"] or "").strip()
    if not thumbnail_filename:
        thumbnail_filename = _thumbnail_filename_for_image(filename) or ""

    thumbnail_path = safe_thumbnail_path(thumbnail_filename) if thumbnail_filename else None
    if thumbnail_path and thumbnail_path.is_file():
        return "ready"

    if filename and thumbnail_status_map:
        status = thumbnail_status_map.get(filename)
        if status:
            return status

    return "missing"


def _gallery_entry_from_row(
    row: sqlite3.Row,
    thumbnail_status_map: dict[str, str] | None = None,
) -> dict[str, Any]:
    entry = {
        column: row[column]
        for column in GALLERY_COLUMNS
        if column not in _GALLERY_INTERNAL_COLUMNS
        and (column in REQUIRED_GALLERY_COLUMNS or row[column] is not None)
    }
    entry["favorite"] = bool(entry.get("favorite"))
    if entry.get("thumbnail_filename") and not safe_thumbnail_path(
        str(entry["thumbnail_filename"])
    ):
        entry.pop("thumbnail_filename", None)
    entry["thumbnail_status"] = _gallery_thumbnail_status_for_row(
        row, thumbnail_status_map
    )
    return _attach_gallery_thumbnail_url(entry)


def _like_contains_param(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _fts_phrase_query(value: str) -> str:
    escaped = value.replace('"', '""')
    return f'"{escaped}"'


def _use_prompt_fts(prompt: str) -> bool:
    return bool(
        db_state._gallery_fts_available
        and len(prompt) >= GALLERY_FTS_MIN_QUERY_LENGTH
    )


def _build_gallery_filter_where(filters: dict[str, Any] | None) -> tuple[str, list[Any]]:
    if not filters:
        return "", []

    clauses: list[str] = []
    params: list[Any] = []

    prompt = str(filters.get("prompt") or "").strip()
    if prompt:
        if _use_prompt_fts(prompt):
            clauses.append(
                """
                rowid IN (
                    SELECT rowid
                    FROM gallery_entries_fts
                    WHERE gallery_entries_fts MATCH ?
                )
                """
            )
            params.append(_fts_phrase_query(prompt))
        else:
            clauses.append("prompt COLLATE NOCASE LIKE ? ESCAPE '\\'")
            params.append(_like_contains_param(prompt))

    for key, column in (
        ("model", "model"),
        ("preset", "api_preset_name"),
        ("size", "size"),
    ):
        value = str(filters.get(key) or "").strip()
        if value:
            clauses.append(f"{column} = ?")
            params.append(value)

    favorite = _normalize_gallery_filter_bool(filters.get("favorite"))
    if favorite is not None:
        clauses.append("favorite = ?")
        params.append(favorite)

    mask_only = _normalize_gallery_filter_bool(filters.get("mask_only"))
    if mask_only == 1:
        clauses.append("mask_coverage IS NOT NULL")

    date_from = str(filters.get("date_from") or "").strip()
    if date_from:
        clauses.append("created_at >= ?")
        params.append(date_from)

    date_to = str(filters.get("date_to") or "").strip()
    if date_to:
        clauses.append("created_at <= ?")
        params.append(date_to)

    if not clauses:
        return "", []
    return " WHERE " + " AND ".join(clauses), params


def _gallery_query_key_from_components(
    where_sql: str,
    params: Sequence[Any],
) -> str:
    payload = {
        "sort": "sort_seq_desc_id_desc",
        "where": " ".join(where_sql.split()),
        "params": ["" if value is None else str(value) for value in params],
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _normalize_generate_job(job: dict[str, Any]) -> dict[str, Any]:
    now = utc_now()
    normalized: dict[str, Any] = {
        "job_id": str(job["job_id"]),
        "status": str(job.get("status") or "queued"),
        "created_at": str(job.get("created_at") or now),
        "updated_at": str(job.get("updated_at") or now),
    }

    for column in GENERATE_JOB_COLUMNS:
        if column in {"job_id", "status", "created_at", "updated_at"}:
            continue
        if column == "stage_timings_json":
            value = job.get("stage_timings_json")
            if value is None:
                value = job.get("stage_timings")
            if value is None:
                continue
            if isinstance(value, str):
                try:
                    json.loads(value)
                except json.JSONDecodeError:
                    continue
                normalized[column] = value
            else:
                try:
                    normalized[column] = json.dumps(
                        value,
                        ensure_ascii=False,
                        sort_keys=True,
                    )
                except TypeError:
                    continue
            continue
        if column == "images_json":
            value = job.get("images_json")
            if value is None:
                value = job.get("images")
            if value is None:
                continue
            if isinstance(value, str):
                try:
                    json.loads(value)
                except json.JSONDecodeError:
                    continue
                normalized[column] = value
            else:
                try:
                    normalized[column] = json.dumps(
                        value,
                        ensure_ascii=False,
                        sort_keys=True,
                    )
                except TypeError:
                    continue
            continue
        if column in {"usage_json", "cost_json"}:
            value = job.get(column)
            if value is None:
                value = job.get(column[: -len("_json")])
            if value is None:
                continue
            if isinstance(value, str):
                try:
                    json.loads(value)
                except json.JSONDecodeError:
                    continue
                normalized[column] = value
            else:
                try:
                    normalized[column] = json.dumps(
                        value,
                        ensure_ascii=False,
                        sort_keys=True,
                    )
                except TypeError:
                    continue
            continue
        value = job.get(column)
        if value is None:
            continue
        if column in INTEGER_GENERATE_JOB_COLUMNS:
            try:
                normalized[column] = int(value)
            except (TypeError, ValueError):
                continue
        else:
            text = str(value)
            if column in {"message", "error"}:
                text = _sanitize_persisted_job_text(text)
            normalized[column] = text

    return normalized


def _sanitize_persisted_job_text(value: str) -> str:
    text = str(value or "")[:MAX_PERSISTED_JOB_TEXT_CHARS]
    text = re.sub(
        r"(?i)(authorization\s*[:=]\s*(?:bearer\s+)?)[^\s,;]+",
        r"\1[REDACTED]",
        text,
    )
    return re.sub(
        r'''(?i)(["']?(?:api[_-]?key|access[_-]?key|secret|token)["']?\s*[:=]\s*["']?)[^"',\s}]+''',
        r"\1[REDACTED]",
        text,
    )


def _generate_job_values(job: dict[str, Any]) -> tuple[Any, ...]:
    return tuple(job.get(column) for column in GENERATE_JOB_COLUMNS)


def _upsert_generate_job_on_conn(
    conn: sqlite3.Connection, job: dict[str, Any]
) -> sqlite3.Cursor:
    """Insert or merge a generate job.

    Existing terminal rows are never overwritten: the `ON CONFLICT ... WHERE`
    guard makes the parent job a write-once terminal record. A brand-new job
    (insert) is always accepted. The returned cursor's `rowcount` is 0 when an
    active update was rejected because the stored row is already terminal.
    """
    columns_sql = ", ".join(GENERATE_JOB_COLUMNS)
    placeholders_sql = ", ".join("?" for _ in GENERATE_JOB_COLUMNS)
    updates_sql = ", ".join(
        (
            f"{column} = COALESCE(excluded.{column}, generate_jobs.{column})"
            if column == "webhook_url"
            else f"{column} = excluded.{column}"
        )
        for column in GENERATE_JOB_COLUMNS
        if column != "job_id"
    )
    active_statuses = tuple(sorted(ACTIVE_GENERATE_JOB_STATUSES))
    active_placeholders = ", ".join("?" for _ in active_statuses)
    cursor = conn.execute(
        f"""
        INSERT INTO generate_jobs ({columns_sql})
        VALUES ({placeholders_sql})
        ON CONFLICT(job_id) DO UPDATE SET {updates_sql}
        WHERE generate_jobs.status IN ({active_placeholders})
        """,
        (*_generate_job_values(job), *active_statuses),
    )
    if job.get("status") not in ACTIVE_GENERATE_JOB_STATUSES:
        conn.execute(
            "DELETE FROM edit_source_reservations WHERE job_id = ?",
            (job["job_id"],),
        )
    return cursor


def _generate_job_from_row(row: sqlite3.Row) -> dict[str, Any]:
    job = {
        column: row[column]
        for column in GENERATE_JOB_COLUMNS
        if row[column] is not None
    }
    stage_timings_json = job.pop("stage_timings_json", None)
    if stage_timings_json:
        try:
            job["stage_timings"] = json.loads(stage_timings_json)
        except json.JSONDecodeError:
            job["stage_timings"] = {}
    images_json = job.pop("images_json", None)
    if images_json:
        try:
            job["images"] = json.loads(images_json)
        except json.JSONDecodeError:
            job["images"] = []
    usage_json = job.pop("usage_json", None)
    if usage_json:
        try:
            job["usage"] = json.loads(usage_json)
        except json.JSONDecodeError:
            pass
    cost_json = job.pop("cost_json", None)
    if cost_json:
        try:
            job["cost"] = json.loads(cost_json)
        except json.JSONDecodeError:
            pass
    if "streaming" in job:
        job["streaming"] = bool(job["streaming"])
    if "mask_applied" in job:
        job["mask_applied"] = bool(job["mask_applied"])
    if job.get("paste_back") is not None:
        job["paste_back"] = bool(job["paste_back"])
    if job.get("image_id"):
        job["id"] = job["image_id"]
    if not job.get("images") and job.get("image_id") and job.get("image_url"):
        image_url = str(job["image_url"])
        image: dict[str, Any] = {
            "image_id": str(job["image_id"]),
            "image_url": image_url,
            "filename": image_url.rsplit("/", 1)[-1],
        }
        if job.get("image_width") is not None:
            image["image_width"] = job["image_width"]
        if job.get("image_height") is not None:
            image["image_height"] = job["image_height"]
        job["images"] = [image]
    return job


def _json_loads_dict(value: str | None) -> dict[str, Any]:
    if not value:
        return {}
    try:
        parsed = json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _json_loads_list(value: str | None) -> list[Any]:
    if not value:
        return []
    try:
        parsed = json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return []
    return parsed if isinstance(parsed, list) else []


def _coerce_nonnegative_int(value: Any, default: int = 0) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return default


def _image_job_unit_from_row(row: sqlite3.Row) -> dict[str, Any]:
    unit = {
        column: row[column]
        for column in IMAGE_JOB_UNIT_COLUMNS
        if row[column] is not None
    }
    unit["request"] = _json_loads_dict(unit.pop("request_json", None))
    unit["edit_sources"] = _json_loads_list(unit.pop("edit_sources_json", None))
    unit["result"] = _json_loads_dict(unit.pop("result_json", None))
    unit["stage_timings"] = _json_loads_dict(unit.pop("stage_timings_json", None))
    usage_json = unit.pop("usage_json", None)
    if usage_json:
        try:
            unit["usage"] = json.loads(usage_json)
        except json.JSONDecodeError:
            pass
    cost_json = unit.pop("cost_json", None)
    if cost_json:
        try:
            unit["cost"] = json.loads(cost_json)
        except json.JSONDecodeError:
            pass
    return unit


def _image_job_unit_values(unit: dict[str, Any]) -> tuple[Any, ...]:
    normalized = dict(unit)
    for source_key, json_key in (
        ("request", "request_json"),
        ("edit_sources", "edit_sources_json"),
        ("result", "result_json"),
        ("stage_timings", "stage_timings_json"),
        ("usage", "usage_json"),
        ("cost", "cost_json"),
    ):
        if json_key not in normalized and source_key in normalized:
            normalized[json_key] = json.dumps(
                normalized[source_key],
                ensure_ascii=False,
                sort_keys=True,
            )
    return tuple(normalized.get(column) for column in IMAGE_JOB_UNIT_COLUMNS)


def _normalize_gallery_job(job: dict[str, Any]) -> dict[str, Any]:
    now = utc_now()
    normalized: dict[str, Any] = {
        "job_id": str(job["job_id"]),
        "kind": str(job["kind"]),
        "status": str(job.get("status") or "queued"),
        "progress": _coerce_nonnegative_int(job.get("progress"), 0),
        "created_at": str(job.get("created_at") or now),
        "updated_at": str(job.get("updated_at") or now),
    }
    integer_columns = {
        "requested_count",
        "processed_count",
        "exported_count",
        "missing_count",
        "total_count",
        "compared_count",
        "uploaded_count",
        "pending_upload_count",
        "skipped_existing_count",
        "missing_local_count",
        "failed_count",
        "bytes_total",
        "bytes_written",
        "bytes_uploaded",
    }
    for column in GALLERY_JOB_COLUMNS:
        if column in normalized:
            continue
        if column == "payload_json":
            value = job.get("payload_json")
            if value is None:
                value = job.get("payload")
            if value is None:
                continue
            if isinstance(value, str):
                try:
                    json.loads(value)
                except json.JSONDecodeError:
                    continue
                normalized[column] = value
            else:
                try:
                    normalized[column] = json.dumps(value, ensure_ascii=False, sort_keys=True)
                except TypeError:
                    continue
            continue
        if column in integer_columns:
            value = job.get(column, 0)
            normalized[column] = _coerce_nonnegative_int(value, 0)
        else:
            value = job.get(column)
            if value is None:
                continue
            normalized[column] = str(value)
    return normalized


def _gallery_job_values(job: dict[str, Any]) -> tuple[Any, ...]:
    return tuple(job.get(column) for column in GALLERY_JOB_COLUMNS)


def _gallery_job_from_row(row: sqlite3.Row) -> dict[str, Any]:
    job = {
        column: row[column]
        for column in GALLERY_JOB_COLUMNS
        if row[column] is not None
    }
    job["payload"] = _json_loads_dict(job.pop("payload_json", None))
    return job


def _normalize_prompt_snippet_favorite(value: Any) -> int:
    if isinstance(value, bool):
        return 1 if value else 0
    if isinstance(value, int):
        return 1 if value else 0
    text = str(value or "").strip().lower()
    return 1 if text in {"1", "true", "yes", "on", "favorite", "favorited"} else 0


def _prompt_snippet_from_row(row: sqlite3.Row) -> PromptSnippet:
    return PromptSnippet(
        id=str(row["id"]),
        title=str(row["title"]),
        prompt=str(row["prompt"]),
        favorite=bool(row["favorite"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


def _like_prompt_snippet_query(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _public_file_url(base_url: str, filename: str) -> str:
    return f"{base_url.rstrip('/')}/{quote(filename, safe='')}"


def image_url_for_filename(filename: str) -> str | None:
    if not safe_image_path(filename):
        return None
    if config.PUBLIC_IMAGE_BASE_URL:
        return signed_media_url(config.PUBLIC_IMAGE_BASE_URL, filename)
    return f"/api/image/{quote(filename, safe='')}"


def _attach_gallery_thumbnail_url(entry: dict[str, Any]) -> dict[str, Any]:
    if "image_url" not in entry:
        entry["image_url"] = image_url_for_filename(str(entry.get("filename") or ""))
    if "thumbnail_url" not in entry:
        entry["thumbnail_url"] = _thumbnail_url_for_filename(
            str(entry.get("filename") or "")
        )
    return entry


def _generate_prompt_snippet_id() -> str:
    return f"ps_{secrets.token_urlsafe(12)}"


GALLERY_FILTER_OPTION_FIELDS = (
    ("model", "model"),
    ("preset", "api_preset_name"),
    ("size", "size"),
)


def _filter_option_values_from_mapping(
    mapping: dict[str, Any] | sqlite3.Row,
) -> list[tuple[str, str]]:
    values: list[tuple[str, str]] = []
    for kind, column in GALLERY_FILTER_OPTION_FIELDS:
        value = str(
            mapping[column]
            if isinstance(mapping, sqlite3.Row)
            else mapping.get(column) or ""
        ).strip()
        if value:
            values.append((kind, value))
    return values


def _add_gallery_filter_option_deltas(
    deltas: dict[tuple[str, str], int],
    mapping: dict[str, Any] | sqlite3.Row,
    delta: int,
) -> None:
    for key in _filter_option_values_from_mapping(mapping):
        deltas[key] = deltas.get(key, 0) + delta


def _apply_gallery_filter_option_deltas_on_conn(
    conn: sqlite3.Connection,
    deltas: dict[tuple[str, str], int],
) -> None:
    deltas = {key: delta for key, delta in deltas.items() if delta}
    if not deltas:
        return

    now = utc_now()
    conn.executemany(
        """
        INSERT INTO gallery_filter_options (kind, value, ref_count, updated_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(kind, value) DO UPDATE SET
            ref_count = ref_count + excluded.ref_count,
            updated_at = excluded.updated_at
        """,
        [(kind, value, delta, now) for (kind, value), delta in deltas.items()],
    )
    conn.execute("DELETE FROM gallery_filter_options WHERE ref_count <= 0")
    _bump_filter_options_cache_version()


def _increment_gallery_filter_options_on_conn(
    conn: sqlite3.Connection,
    mapping: dict[str, Any] | sqlite3.Row,
    delta: int,
):
    deltas: dict[tuple[str, str], int] = {}
    _add_gallery_filter_option_deltas(deltas, mapping, delta)
    _apply_gallery_filter_option_deltas_on_conn(conn, deltas)


def _rebuild_gallery_filter_options_on_conn(conn: sqlite3.Connection):
    conn.execute("DELETE FROM gallery_filter_options")
    now = utc_now()
    columns = _table_columns(conn, "gallery_entries")
    for kind, column in GALLERY_FILTER_OPTION_FIELDS:
        if column not in columns:
            continue
        conn.execute(
            f"""
            INSERT INTO gallery_filter_options (kind, value, ref_count, updated_at)
            SELECT ?, TRIM({column}) AS value, COUNT(*) AS ref_count, ?
            FROM gallery_entries
            WHERE {column} IS NOT NULL AND TRIM({column}) != ''
            GROUP BY TRIM({column})
            """,
            (kind, now),
        )
    _bump_filter_options_cache_version()
