"""Persistent access failures and client lockouts."""

import ipaddress
import sqlite3
import time
from typing import Any

from ..db.connection import _connect, _transaction
from ..db.schema import _ensure_database


def _normalize_access_client_ip(client_ip: str) -> str:
    value = str(client_ip or "unknown").strip()[:256] or "unknown"
    try:
        return ipaddress.ip_address(value).compressed
    except ValueError:
        return value


def _cleanup_expired_access_failures_on_conn(
    conn: sqlite3.Connection,
    now: float,
    lockout_seconds: int,
) -> int:
    cutoff = float(now) - max(1, int(lockout_seconds or 1))
    cursor = conn.execute(
        "DELETE FROM access_failures WHERE last_failed_at <= ?",
        (cutoff,),
    )
    return int(cursor.rowcount or 0)


def get_access_lockout(
    client_ip: str,
    *,
    max_failures: int,
    lockout_seconds: int,
    now: float | None = None,
) -> int:
    """Return remaining lockout seconds for a client IP, or 0 when allowed."""

    _ensure_database()
    current_time = float(time.time() if now is None else now)
    max_count = max(1, int(max_failures or 1))
    lockout = max(1, int(lockout_seconds or 1))
    normalized_ip = _normalize_access_client_ip(client_ip)
    with _connect() as conn:
        row = conn.execute(
            """
            SELECT failure_count, last_failed_at
            FROM access_failures
            WHERE client_ip = ?
            """,
            (normalized_ip,),
        ).fetchone()
        if not row:
            return 0
        elapsed = current_time - float(row["last_failed_at"] or 0)
        if elapsed < lockout and int(row["failure_count"] or 0) >= max_count:
            return max(1, int(lockout - elapsed))
    return 0


def record_access_failure(
    client_ip: str,
    *,
    lockout_seconds: int,
    max_entries: int,
    now: float | None = None,
) -> int:
    """Record one failed access attempt and return the updated count."""

    _ensure_database()
    current_time = float(time.time() if now is None else now)
    lockout = max(1, int(lockout_seconds or 1))
    max_size = max(1, int(max_entries or 1))
    normalized_ip = _normalize_access_client_ip(client_ip)
    with _connect() as conn:
        with _transaction(conn):
            _cleanup_expired_access_failures_on_conn(conn, current_time, lockout)
            row = conn.execute(
                """
                SELECT failure_count, first_failed_at
                FROM access_failures
                WHERE client_ip = ?
                """,
                (normalized_ip,),
            ).fetchone()
            count = 1 if not row else int(row["failure_count"] or 0) + 1
            first_failed_at = current_time if not row else float(row["first_failed_at"] or current_time)
            conn.execute(
                """
                INSERT INTO access_failures (
                    client_ip,
                    failure_count,
                    first_failed_at,
                    last_failed_at
                )
                VALUES (?, ?, ?, ?)
                ON CONFLICT(client_ip) DO UPDATE SET
                    failure_count = excluded.failure_count,
                    first_failed_at = excluded.first_failed_at,
                    last_failed_at = excluded.last_failed_at
                """,
                (normalized_ip, count, first_failed_at, current_time),
            )
            overflow_row = conn.execute(
                "SELECT COUNT(*) - ? FROM access_failures",
                (max_size,),
            ).fetchone()
            overflow = max(0, int(overflow_row[0] or 0) if overflow_row else 0)
            if overflow:
                conn.execute(
                    """
                    DELETE FROM access_failures
                    WHERE client_ip IN (
                        SELECT client_ip
                        FROM access_failures
                        ORDER BY last_failed_at ASC, client_ip ASC
                        LIMIT ?
                    )
                    """,
                    (overflow,),
                )
    return count


def clear_access_failure(client_ip: str) -> bool:
    _ensure_database()
    normalized_ip = _normalize_access_client_ip(client_ip)
    with _connect() as conn:
        with _transaction(conn):
            cursor = conn.execute(
                "DELETE FROM access_failures WHERE client_ip = ?",
                (normalized_ip,),
            )
    return int(cursor.rowcount or 0) > 0


def list_access_failures() -> list[dict[str, Any]]:
    _ensure_database()
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT client_ip, failure_count, first_failed_at, last_failed_at
            FROM access_failures
            ORDER BY last_failed_at ASC, client_ip ASC
            """
        ).fetchall()
    return [dict(row) for row in rows]
