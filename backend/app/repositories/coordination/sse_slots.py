"""SQLite leases for global and per-client SSE connection limits."""

import sqlite3

from ...core.utils import utc_now
from ..db.connection import _connect, _transaction
from ..db.schema import _ensure_database


def _cleanup_expired_sse_slots_on_conn(conn: sqlite3.Connection, now: str) -> int:
    cursor = conn.execute(
        "DELETE FROM sse_slots WHERE lease_expires_at <= ?",
        (now,),
    )
    return int(cursor.rowcount if cursor.rowcount is not None else 0)


def acquire_sse_slot(
    *,
    client_ip: str,
    connection_id: str,
    lease_expires_at: str,
    max_global: int,
    max_per_ip: int,
    now: str | None = None,
) -> tuple[bool, str]:
    _ensure_database()
    normalized_ip = str(client_ip or "unknown")[:256]
    normalized_connection_id = str(connection_id or "").strip()
    if not normalized_connection_id:
        return False, "invalid_connection"
    now = now or utc_now()
    global_limit = max(1, int(max_global or 1))
    ip_limit = max(1, int(max_per_ip or 1))
    with _connect() as conn:
        with _transaction(conn):
            _cleanup_expired_sse_slots_on_conn(conn, now)
            row = conn.execute("SELECT COUNT(*) FROM sse_slots").fetchone()
            global_count = int(row[0] or 0) if row else 0
            if global_count >= global_limit:
                return False, "global_limit"

            row = conn.execute(
                "SELECT COUNT(*) FROM sse_slots WHERE client_ip = ?",
                (normalized_ip,),
            ).fetchone()
            per_ip_count = int(row[0] or 0) if row else 0
            if per_ip_count >= ip_limit:
                return False, "per_ip_limit"

            conn.execute(
                """
                INSERT INTO sse_slots (
                    connection_id,
                    client_ip,
                    acquired_at,
                    lease_expires_at
                )
                VALUES (?, ?, ?, ?)
                """,
                (normalized_connection_id, normalized_ip, now, lease_expires_at),
            )
    return True, "acquired"


def refresh_sse_slot(
    *,
    connection_id: str,
    lease_expires_at: str,
    now: str | None = None,
) -> bool:
    _ensure_database()
    normalized_connection_id = str(connection_id or "").strip()
    if not normalized_connection_id:
        return False
    now = now or utc_now()
    with _connect() as conn:
        with _transaction(conn):
            _cleanup_expired_sse_slots_on_conn(conn, now)
            cursor = conn.execute(
                """
                UPDATE sse_slots
                SET lease_expires_at = ?
                WHERE connection_id = ?
                """,
                (lease_expires_at, normalized_connection_id),
            )
    return int(cursor.rowcount or 0) > 0


def release_sse_slot(connection_id: str, *, now: str | None = None) -> bool:
    _ensure_database()
    normalized_connection_id = str(connection_id or "").strip()
    if not normalized_connection_id:
        return False
    now = now or utc_now()
    with _connect() as conn:
        with _transaction(conn):
            _cleanup_expired_sse_slots_on_conn(conn, now)
            cursor = conn.execute(
                "DELETE FROM sse_slots WHERE connection_id = ?",
                (normalized_connection_id,),
            )
    return int(cursor.rowcount or 0) > 0


def _count_active_sse_slots_on_conn(
    conn: sqlite3.Connection,
    *,
    now: str,
    client_ip: str | None = None,
) -> int:
    if client_ip is None:
        row = conn.execute(
            "SELECT COUNT(*) FROM sse_slots WHERE lease_expires_at > ?",
            (now,),
        ).fetchone()
    else:
        row = conn.execute(
            """
            SELECT COUNT(*)
            FROM sse_slots
            WHERE client_ip = ? AND lease_expires_at > ?
            """,
            (client_ip, now),
        ).fetchone()
    return int(row[0] or 0) if row else 0


def count_active_sse_slots(client_ip: str | None = None) -> int:
    _ensure_database()
    now = utc_now()
    with _connect() as conn:
        with _transaction(conn):
            _cleanup_expired_sse_slots_on_conn(conn, now)
            return _count_active_sse_slots_on_conn(conn, now=now, client_ip=client_ip)
