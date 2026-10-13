"""Background work leases and fenced concurrency slots."""

import sqlite3
from datetime import datetime, timedelta, timezone

from ...core.utils import utc_now
from ..db.connection import _connect, _transaction
from ..db.schema import _ensure_database
from ..db.settings_store import _coerce_iso_datetime


def _acquire_background_lease_on_conn(
    conn: sqlite3.Connection,
    *,
    name: str,
    owner: str,
    lease_expires_at: str,
    now: str,
    completed_ttl_seconds: int | None = None,
) -> bool:
    row = conn.execute(
        """
        SELECT owner, lease_expires_at, completed_at
        FROM background_leases
        WHERE name = ?
        """,
        (name,),
    ).fetchone()
    if row:
        completed_at = str(row["completed_at"] or "")
        if completed_ttl_seconds is not None and completed_at:
            now_dt = _coerce_iso_datetime(now) or datetime.now(timezone.utc)
            cutoff = (now_dt - timedelta(seconds=max(0, int(completed_ttl_seconds or 0)))).isoformat()
            if completed_at > cutoff:
                return False

        active_owner = str(row["owner"] or "")
        active_until = str(row["lease_expires_at"] or "")
        if active_until > now and active_owner != owner:
            return False

    conn.execute(
        """
        INSERT INTO background_leases (
            name,
            owner,
            lease_expires_at,
            updated_at,
            completed_at
        )
        VALUES (?, ?, ?, ?, NULL)
        ON CONFLICT(name) DO UPDATE SET
            owner = excluded.owner,
            lease_expires_at = excluded.lease_expires_at,
            updated_at = excluded.updated_at,
            completed_at = NULL
        """,
        (name, owner, lease_expires_at, now),
    )
    return True


def acquire_background_lease(
    *,
    name: str,
    owner: str,
    lease_expires_at: str,
    now: str | None = None,
    completed_ttl_seconds: int | None = None,
) -> bool:
    _ensure_database()
    normalized_name = str(name or "").strip()
    normalized_owner = str(owner or "").strip()
    if not normalized_name or not normalized_owner:
        return False
    now = now or utc_now()
    with _connect() as conn:
        with _transaction(conn):
            return _acquire_background_lease_on_conn(
                conn,
                name=normalized_name,
                owner=normalized_owner,
                lease_expires_at=lease_expires_at,
                now=now,
                completed_ttl_seconds=completed_ttl_seconds,
            )


def complete_background_lease(
    *,
    name: str,
    owner: str,
    now: str | None = None,
) -> bool:
    _ensure_database()
    normalized_name = str(name or "").strip()
    normalized_owner = str(owner or "").strip()
    if not normalized_name or not normalized_owner:
        return False
    now = now or utc_now()
    with _connect() as conn:
        with _transaction(conn):
            cursor = conn.execute(
                """
                UPDATE background_leases
                SET lease_expires_at = ?,
                    updated_at = ?,
                    completed_at = ?
                WHERE name = ? AND owner = ?
                """,
                (now, now, now, normalized_name, normalized_owner),
            )
    return int(cursor.rowcount or 0) > 0


def release_background_lease(
    *,
    name: str,
    owner: str,
    now: str | None = None,
) -> bool:
    _ensure_database()
    normalized_name = str(name or "").strip()
    normalized_owner = str(owner or "").strip()
    if not normalized_name or not normalized_owner:
        return False
    now = now or utc_now()
    with _connect() as conn:
        with _transaction(conn):
            cursor = conn.execute(
                """
                UPDATE background_leases
                SET lease_expires_at = ?,
                    updated_at = ?
                WHERE name = ? AND owner = ?
                """,
                (now, now, normalized_name, normalized_owner),
            )
    return int(cursor.rowcount or 0) > 0


def acquire_background_slot(
    *,
    name_prefix: str,
    owner: str,
    slot_count: int,
    lease_expires_at: str,
    now: str | None = None,
) -> str | None:
    _ensure_database()
    normalized_prefix = str(name_prefix or "").strip().rstrip(":")
    normalized_owner = str(owner or "").strip()
    if not normalized_prefix or not normalized_owner:
        return None
    slots = max(1, int(slot_count or 1))
    now = now or utc_now()
    with _connect() as conn:
        with _transaction(conn):
            for index in range(slots):
                name = f"{normalized_prefix}:{index}"
                if _acquire_background_lease_on_conn(
                    conn,
                    name=name,
                    owner=normalized_owner,
                    lease_expires_at=lease_expires_at,
                    now=now,
                ):
                    return name
    return None


def renew_background_slot(
    *,
    name: str,
    owner: str,
    lease_expires_at: str,
    now: str | None = None,
) -> bool:
    """Extend a slot only while the caller still owns an unexpired lease."""
    _ensure_database()
    normalized_name = str(name or "").strip()
    normalized_owner = str(owner or "").strip()
    if not normalized_name or not normalized_owner:
        return False
    now = now or utc_now()
    with _connect() as conn:
        with _transaction(conn):
            cursor = conn.execute(
                """
                UPDATE background_leases
                SET lease_expires_at = ?, updated_at = ?
                WHERE name = ? AND owner = ? AND lease_expires_at > ? AND completed_at IS NULL
                """,
                (lease_expires_at, now, normalized_name, normalized_owner, now),
            )
    return int(cursor.rowcount or 0) > 0


def release_background_slot(
    *,
    name: str,
    owner: str,
    now: str | None = None,
) -> bool:
    return release_background_lease(name=name, owner=owner, now=now)
