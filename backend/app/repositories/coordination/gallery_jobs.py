"""Gallery job lifecycle, admission, and import upload reservations."""

import json
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Sequence

from ...core.utils import json_dumps_canonical, utc_now
from ..db.connection import (
    _connect,
    _iter_sqlite_in_chunks,
    _transaction,
    _unique_sqlite_values,
)
from ..db.constants import GALLERY_JOB_COLUMNS
from ..db.rows import (
    _coerce_nonnegative_int,
    _gallery_job_from_row,
    _gallery_job_values,
    _json_loads_dict,
    _normalize_gallery_job,
)
from ..db.schema import _ensure_database


def create_gallery_job(**job: Any) -> dict[str, Any]:
    _ensure_database()
    normalized = _normalize_gallery_job(job)
    columns_sql = ", ".join(GALLERY_JOB_COLUMNS)
    placeholders_sql = ", ".join("?" for _ in GALLERY_JOB_COLUMNS)
    with _connect() as conn:
        with _transaction(conn):
            conn.execute(
                f"INSERT INTO gallery_jobs ({columns_sql}) VALUES ({placeholders_sql})",
                _gallery_job_values(normalized),
            )
    return normalized | {"payload": _json_loads_dict(normalized.get("payload_json"))}


def get_gallery_job(kind: str, job_id: str) -> dict[str, Any] | None:
    _ensure_database()
    with _connect() as conn:
        row = conn.execute(
            f"""
            SELECT {", ".join(GALLERY_JOB_COLUMNS)}
            FROM gallery_jobs
            WHERE kind = ? AND job_id = ?
            """,
            (kind, job_id),
        ).fetchone()
    return _gallery_job_from_row(row) if row else None


def get_gallery_jobs_updated_at_edges(kind: str, job_ids: set[str]) -> dict[str, str]:
    _ensure_database()
    unique_job_ids = _unique_sqlite_values(job_ids)
    if not unique_job_ids:
        return {}
    rows_by_job_id: dict[str, str] = {}
    with _connect() as conn:
        for chunk in _iter_sqlite_in_chunks(unique_job_ids):
            placeholders = ", ".join("?" for _ in chunk)
            rows = conn.execute(
                f"""
                SELECT job_id, updated_at
                FROM gallery_jobs
                WHERE kind = ? AND job_id IN ({placeholders})
                """,
                (kind, *chunk),
            ).fetchall()
            rows_by_job_id.update({str(row["job_id"]): str(row["updated_at"]) for row in rows})
    return rows_by_job_id


_GALLERY_JOB_UPDATE_COLUMNS = set(GALLERY_JOB_COLUMNS) - {"job_id", "kind", "created_at"}
_GALLERY_JOB_INTEGER_UPDATE_COLUMNS = {
    "progress",
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


def _normalize_gallery_job_updates(updates: dict[str, Any]) -> dict[str, Any]:
    normalized: dict[str, Any] = {}
    for key, value in updates.items():
        if key == "payload":
            key = "payload_json"
        if key not in _GALLERY_JOB_UPDATE_COLUMNS:
            continue
        if key == "payload_json":
            try:
                normalized[key] = (
                    value
                    if isinstance(value, str)
                    else json_dumps_canonical(value or {})
                )
            except TypeError:
                continue
            continue
        if key in _GALLERY_JOB_INTEGER_UPDATE_COLUMNS:
            normalized[key] = _coerce_nonnegative_int(value, 0)
        else:
            normalized[key] = None if value is None else str(value)
    normalized["updated_at"] = str(updates.get("updated_at") or utc_now())
    return normalized


def update_gallery_job(
    job_id: str,
    updates: dict[str, Any],
    *,
    lease_owner: str | None = None,
) -> dict[str, Any] | None:
    _ensure_database()
    if not updates:
        with _connect() as conn:
            if lease_owner:
                row = conn.execute(
                    f"SELECT {', '.join(GALLERY_JOB_COLUMNS)} FROM gallery_jobs WHERE job_id = ? AND lease_owner = ?",
                    (job_id, lease_owner),
                ).fetchone()
            else:
                row = conn.execute(
                    f"SELECT {', '.join(GALLERY_JOB_COLUMNS)} FROM gallery_jobs WHERE job_id = ?",
                    (job_id,),
                ).fetchone()
        return _gallery_job_from_row(row) if row else None

    normalized = _normalize_gallery_job_updates(updates)

    assignments = ", ".join(f"{key} = ?" for key in normalized)
    with _connect() as conn:
        with _transaction(conn):
            if lease_owner:
                if "payload_json" in normalized:
                    current_payload_row = conn.execute(
                        "SELECT payload_json FROM gallery_jobs WHERE job_id = ? AND lease_owner = ?",
                        (job_id, lease_owner),
                    ).fetchone()
                    current_payload = _json_loads_dict(
                        current_payload_row["payload_json"]
                    ) if current_payload_row else {}
                    next_payload = _json_loads_dict(normalized["payload_json"])
                    if current_payload.get("cancel_requested"):
                        next_payload["cancel_requested"] = True
                        if current_payload.get("cancel_requested_at"):
                            next_payload.setdefault(
                                "cancel_requested_at",
                                current_payload["cancel_requested_at"],
                            )
                        normalized["payload_json"] = json.dumps(
                            next_payload,
                            ensure_ascii=False,
                            sort_keys=True,
                        )
                cursor = conn.execute(
                    f"""
                    UPDATE gallery_jobs
                    SET {assignments}
                    WHERE job_id = ?
                        AND lease_owner = ?
                        AND status = 'running'
                        AND (
                            lease_expires_at IS NULL
                            OR lease_expires_at > ?
                        )
                    """,
                    (*normalized.values(), job_id, lease_owner, utc_now()),
                )
                if int(cursor.rowcount or 0) <= 0:
                    return None
            else:
                conn.execute(
                    f"UPDATE gallery_jobs SET {assignments} WHERE job_id = ?",
                    (*normalized.values(), job_id),
                )
            row = conn.execute(
                f"SELECT {', '.join(GALLERY_JOB_COLUMNS)} FROM gallery_jobs WHERE job_id = ?",
                (job_id,),
            ).fetchone()
    return _gallery_job_from_row(row) if row else None


def update_gallery_job_progress(
    job_id: str,
    updates: dict[str, Any],
    *,
    lease_owner: str | None = None,
) -> bool:
    """Update a gallery job without fetching the row back.

    Used for high-frequency export/sync progress writes where SSE only needs the
    updated_at edge and will read the full row on its own poll.
    """
    _ensure_database()
    normalized = _normalize_gallery_job_updates(updates)
    if not normalized:
        return False
    assignments = ", ".join(f"{key} = ?" for key in normalized)
    with _connect() as conn:
        with _transaction(conn):
            if lease_owner:
                cursor = conn.execute(
                    f"""
                    UPDATE gallery_jobs
                    SET {assignments}
                    WHERE job_id = ?
                        AND lease_owner = ?
                        AND status = 'running'
                        AND (
                            lease_expires_at IS NULL
                            OR lease_expires_at > ?
                        )
                    """,
                    (*normalized.values(), job_id, lease_owner, utc_now()),
                )
            else:
                cursor = conn.execute(
                    f"UPDATE gallery_jobs SET {assignments} WHERE job_id = ?",
                    (*normalized.values(), job_id),
                )
    return cursor.rowcount > 0


def request_gallery_job_cancellation(kind: str, job_id: str) -> dict[str, Any] | None:
    """Request cooperative cancellation while preserving completed item results."""
    _ensure_database()
    normalized_kind = str(kind or "").strip()
    normalized_job_id = str(job_id or "").strip()
    if not normalized_kind or not normalized_job_id:
        return None
    now = utc_now()
    with _connect() as conn:
        with _transaction(conn):
            row = conn.execute(
                f"""
                SELECT {', '.join(GALLERY_JOB_COLUMNS)}
                FROM gallery_jobs
                WHERE kind = ? AND job_id = ?
                """,
                (normalized_kind, normalized_job_id),
            ).fetchone()
            if not row:
                return None

            status = str(row["status"] or "")
            payload = _json_loads_dict(row["payload_json"])
            if status == "queued":
                ids = [str(value) for value in payload.get("ids") or [] if str(value)]
                existing_results = [
                    value
                    for value in payload.get("results") or []
                    if isinstance(value, dict) and str(value.get("image_id") or "")
                ]
                existing_ids = {str(value["image_id"]) for value in existing_results}
                results = list(existing_results)
                for image_id in ids:
                    if image_id in existing_ids:
                        continue
                    results.append(
                        {
                            "image_id": image_id,
                            "filename": None,
                            "status": "cancelled",
                            "url": None,
                            "markdown": None,
                            "error": "Cancelled before upload",
                        }
                    )
                payload["results"] = results
                requested_count = max(
                    int(row["requested_count"] or 0),
                    len(ids),
                )
                uploaded_count = sum(value.get("status") == "ok" for value in results)
                failed_count = sum(value.get("status") == "error" for value in results)
                conn.execute(
                    """
                    UPDATE gallery_jobs
                    SET status = 'cancelled',
                        stage = 'cancelled',
                        message = 'NodeImage upload cancelled',
                        progress = 100,
                        requested_count = ?,
                        processed_count = ?,
                        uploaded_count = ?,
                        failed_count = ?,
                        completed_at = ?,
                        updated_at = ?,
                        error = NULL,
                        lease_owner = NULL,
                        lease_expires_at = NULL,
                        payload_json = ?
                    WHERE kind = ? AND job_id = ? AND status = 'queued'
                    """,
                    (
                        requested_count,
                        requested_count,
                        uploaded_count,
                        failed_count,
                        now,
                        now,
                        json_dumps_canonical(payload),
                        normalized_kind,
                        normalized_job_id,
                    ),
                )
            elif status == "running":
                payload["cancel_requested"] = True
                payload["cancel_requested_at"] = now
                conn.execute(
                    """
                    UPDATE gallery_jobs
                    SET stage = 'cancelling',
                        message = 'Cancelling NodeImage upload',
                        updated_at = ?,
                        payload_json = ?
                    WHERE kind = ? AND job_id = ? AND status = 'running'
                    """,
                    (
                        now,
                        json_dumps_canonical(payload),
                        normalized_kind,
                        normalized_job_id,
                    ),
                )
            row = conn.execute(
                f"""
                SELECT {', '.join(GALLERY_JOB_COLUMNS)}
                FROM gallery_jobs
                WHERE kind = ? AND job_id = ?
                """,
                (normalized_kind, normalized_job_id),
            ).fetchone()
    return _gallery_job_from_row(row) if row else None


def renew_gallery_job_lease(
    *,
    job_id: str,
    lease_owner: str,
    lease_expires_at: str,
    now: str | None = None,
) -> bool:
    _ensure_database()
    current_time = now or utc_now()
    with _connect() as conn:
        with _transaction(conn):
            cursor = conn.execute(
                """
                UPDATE gallery_jobs
                SET lease_expires_at = ?,
                    updated_at = ?
                WHERE job_id = ?
                    AND status = 'running'
                    AND lease_owner = ?
                    AND (
                        lease_expires_at IS NULL
                        OR lease_expires_at > ?
                    )
                """,
                (lease_expires_at, current_time, job_id, lease_owner, current_time),
            )
    return cursor.rowcount > 0


def count_active_gallery_jobs(kind: str) -> int:
    _ensure_database()
    now = utc_now()
    with _connect() as conn:
        row = conn.execute(
            """
            SELECT COUNT(*)
            FROM gallery_jobs
            WHERE kind = ?
                AND (
                    status = 'queued'
                    OR (
                        status = 'running'
                        AND (lease_expires_at IS NULL OR lease_expires_at > ?)
                    )
                )
            """,
            (kind, now),
        ).fetchone()
    return int(row[0] or 0) if row else 0


def _cleanup_expired_import_upload_reservations_on_conn(
    conn: sqlite3.Connection,
    now: str,
) -> int:
    cursor = conn.execute(
        "DELETE FROM import_upload_reservations WHERE lease_expires_at <= ?",
        (now,),
    )
    return int(cursor.rowcount or 0)


def reserve_import_upload_capacity(
    *,
    reservation_id: str,
    client_ip: str,
    byte_count: int,
    max_total_bytes: int,
    per_ip_limit: int,
    lease_expires_at: str,
    now: str | None = None,
) -> tuple[bool, str]:
    _ensure_database()
    normalized_id = str(reservation_id or "").strip()
    normalized_ip = str(client_ip or "unknown").strip()[:256] or "unknown"
    reserved_bytes = _coerce_nonnegative_int(byte_count, 0)
    total_limit = max(1, int(max_total_bytes or 1))
    ip_limit = max(1, int(per_ip_limit or 1))
    if not normalized_id or reserved_bytes <= 0:
        return False, "invalid"
    now = now or utc_now()
    with _connect() as conn:
        with _transaction(conn):
            _cleanup_expired_import_upload_reservations_on_conn(conn, now)
            total_row = conn.execute(
                """
                SELECT COALESCE(SUM(byte_count), 0)
                FROM import_upload_reservations
                WHERE lease_expires_at > ?
                """,
                (now,),
            ).fetchone()
            current_bytes = int(total_row[0] or 0) if total_row else 0
            if current_bytes + reserved_bytes > total_limit:
                return False, "temp_space"

            window_start = (
                datetime.now(timezone.utc) - timedelta(seconds=60)
            ).isoformat()
            ip_row = conn.execute(
                """
                SELECT COUNT(*)
                FROM import_upload_reservations
                WHERE client_ip = ? AND created_at > ?
                """,
                (normalized_ip, window_start),
            ).fetchone()
            if int(ip_row[0] or 0) >= ip_limit:
                return False, "ip_rate"

            conn.execute(
                """
                INSERT INTO import_upload_reservations (
                    reservation_id,
                    client_ip,
                    byte_count,
                    created_at,
                    updated_at,
                    lease_expires_at
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    normalized_id,
                    normalized_ip,
                    reserved_bytes,
                    now,
                    now,
                    lease_expires_at,
                ),
            )
    return True, "reserved"


def resize_import_upload_reservation(
    reservation_id: str,
    byte_count: int,
    *,
    now: str | None = None,
) -> bool:
    _ensure_database()
    normalized_id = str(reservation_id or "").strip()
    if not normalized_id:
        return False
    now = now or utc_now()
    with _connect() as conn:
        with _transaction(conn):
            _cleanup_expired_import_upload_reservations_on_conn(conn, now)
            cursor = conn.execute(
                """
                UPDATE import_upload_reservations
                SET byte_count = ?,
                    updated_at = ?
                WHERE reservation_id = ?
                    AND lease_expires_at > ?
                """,
                (
                    _coerce_nonnegative_int(byte_count, 0),
                    now,
                    normalized_id,
                    now,
                ),
            )
    return int(cursor.rowcount or 0) > 0


def release_import_upload_reservation(
    reservation_id: str,
    *,
    now: str | None = None,
) -> bool:
    _ensure_database()
    normalized_id = str(reservation_id or "").strip()
    if not normalized_id:
        return False
    now = now or utc_now()
    with _connect() as conn:
        with _transaction(conn):
            _cleanup_expired_import_upload_reservations_on_conn(conn, now)
            cursor = conn.execute(
                "DELETE FROM import_upload_reservations WHERE reservation_id = ?",
                (normalized_id,),
            )
    return int(cursor.rowcount or 0) > 0


def reserve_gallery_job_capacity(
    *,
    job: dict[str, Any],
    counted_kinds: Sequence[str],
    max_active: int,
) -> dict[str, Any] | None:
    _ensure_database()
    normalized = _normalize_gallery_job(job)
    kinds = [str(kind) for kind in counted_kinds if str(kind)]
    if normalized["kind"] not in kinds:
        kinds.append(normalized["kind"])
    placeholders = ", ".join("?" for _ in kinds)
    now = utc_now()
    columns_sql = ", ".join(GALLERY_JOB_COLUMNS)
    placeholders_sql = ", ".join("?" for _ in GALLERY_JOB_COLUMNS)
    with _connect() as conn:
        with _transaction(conn):
            row = conn.execute(
                f"""
                SELECT COUNT(*)
                FROM gallery_jobs
                WHERE kind IN ({placeholders})
                    AND (
                        status = 'queued'
                        OR (
                            status = 'running'
                            AND (lease_expires_at IS NULL OR lease_expires_at > ?)
                        )
                    )
                """,
                (*kinds, now),
            ).fetchone()
            active_count = int(row[0] or 0) if row else 0
            if active_count >= max(1, int(max_active or 1)):
                return None
            conn.execute(
                f"INSERT INTO gallery_jobs ({columns_sql}) VALUES ({placeholders_sql})",
                _gallery_job_values(normalized),
            )
    return normalized | {"payload": _json_loads_dict(normalized.get("payload_json"))}


def has_claimable_gallery_job(
    *,
    kind: str,
    now: str,
) -> bool:
    """Read-only precheck used to skip an empty gallery claim transaction."""
    _ensure_database()
    with _connect() as conn:
        row = conn.execute(
            """
            SELECT 1
            FROM gallery_jobs
            WHERE kind = ?
                AND (
                    status = 'queued'
                    OR (
                        status = 'running'
                        AND lease_expires_at IS NOT NULL
                        AND lease_expires_at <= ?
                    )
                )
            LIMIT 1
            """,
            (kind, now),
        ).fetchone()
    return row is not None


def claim_next_gallery_job(
    *,
    kind: str,
    worker_id: str,
    lease_expires_at: str,
    now: str,
    running_limit: int,
    counted_kinds: Sequence[str] | None = None,
) -> dict[str, Any] | None:
    _ensure_database()
    active_kinds = [str(value) for value in (counted_kinds or (kind,)) if str(value)]
    if kind not in active_kinds:
        active_kinds.append(kind)
    active_placeholders = ", ".join("?" for _ in active_kinds)
    with _connect() as conn:
        with _transaction(conn):
            running_row = conn.execute(
                f"""
                SELECT COUNT(*)
                FROM gallery_jobs
                WHERE kind IN ({active_placeholders}) AND status = 'running'
                    AND (lease_expires_at IS NULL OR lease_expires_at > ?)
                """,
                (*active_kinds, now),
            ).fetchone()
            if int(running_row[0] or 0) >= max(1, int(running_limit or 1)):
                return None

            row = conn.execute(
                f"""
                SELECT {", ".join(GALLERY_JOB_COLUMNS)}
                FROM gallery_jobs
                WHERE kind = ?
                    AND (
                        status = 'queued'
                        OR (status = 'running' AND lease_expires_at IS NOT NULL AND lease_expires_at <= ?)
                    )
                ORDER BY
                    CASE WHEN status = 'running' THEN 0 ELSE 1 END,
                    created_at ASC
                LIMIT 1
                """,
                (kind, now),
            ).fetchone()
            if not row:
                return None

            started_at = row["started_at"] or now
            conn.execute(
                """
                UPDATE gallery_jobs
                SET status = 'running',
                    lease_owner = ?,
                    lease_expires_at = ?,
                    started_at = ?,
                    updated_at = ?
                WHERE job_id = ?
                """,
                (worker_id, lease_expires_at, started_at, now, row["job_id"]),
            )
            claimed = conn.execute(
                f"SELECT {', '.join(GALLERY_JOB_COLUMNS)} FROM gallery_jobs WHERE job_id = ?",
                (row["job_id"],),
            ).fetchone()
    return _gallery_job_from_row(claimed) if claimed else None


def cleanup_expired_gallery_jobs(kind: str) -> list[dict[str, Any]]:
    _ensure_database()
    now = utc_now()
    with _connect() as conn:
        with _transaction(conn):
            rows = conn.execute(
                f"""
                SELECT {", ".join(GALLERY_JOB_COLUMNS)}
                FROM gallery_jobs
                WHERE kind = ?
                    AND status = 'running'
                    AND lease_expires_at IS NOT NULL
                    AND lease_expires_at <= ?
                """,
                (kind, now),
            ).fetchall()
            if rows:
                conn.executemany(
                    "DELETE FROM gallery_jobs WHERE job_id = ?",
                    [(row["job_id"],) for row in rows],
                )
    return [_gallery_job_from_row(row) for row in rows]


def cleanup_stale_gallery_jobs(
    kind: str,
    ttl_seconds: int,
    terminal_statuses: Sequence[str] = ("success", "error"),
) -> list[dict[str, Any]]:
    _ensure_database()
    cutoff = datetime.fromtimestamp(
        time.time() - max(0, int(ttl_seconds or 0)),
        tz=timezone.utc,
    ).isoformat()
    statuses = [str(status) for status in terminal_statuses if str(status)]
    if not statuses:
        return []
    placeholders = ", ".join("?" for _ in statuses)
    with _connect() as conn:
        with _transaction(conn):
            rows = conn.execute(
                f"""
                SELECT {", ".join(GALLERY_JOB_COLUMNS)}
                FROM gallery_jobs
                WHERE kind = ? AND status IN ({placeholders})
                    AND updated_at <= ?
                """,
                (kind, *statuses, cutoff),
            ).fetchall()
            if rows:
                conn.executemany(
                    "DELETE FROM gallery_jobs WHERE job_id = ?",
                    [(row["job_id"],) for row in rows],
                )
    return [_gallery_job_from_row(row) for row in rows]


def delete_gallery_job(kind: str, job_id: str) -> dict[str, Any] | None:
    _ensure_database()
    with _connect() as conn:
        with _transaction(conn):
            row = conn.execute(
                f"""
                SELECT {", ".join(GALLERY_JOB_COLUMNS)}
                FROM gallery_jobs
                WHERE kind = ? AND job_id = ?
                """,
                (kind, job_id),
            ).fetchone()
            if row:
                conn.execute("DELETE FROM gallery_jobs WHERE job_id = ?", (job_id,))
    return _gallery_job_from_row(row) if row else None


def list_gallery_job_ids_with_files(kind: str) -> set[str]:
    _ensure_database()
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT job_id
            FROM gallery_jobs
            WHERE kind = ? AND path IS NOT NULL
            """,
            (kind,),
        ).fetchall()
    return {str(row["job_id"]) for row in rows}
