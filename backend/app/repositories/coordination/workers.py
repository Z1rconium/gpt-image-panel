"""Worker heartbeats, metric snapshots, and coordination maintenance."""

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any

from ...core.utils import utc_now
from ..db.connection import _connect, _transaction
from ..db.constants import WORKER_METRIC_SNAPSHOT_TTL_SECONDS
from ..db.schema import _ensure_database
from ..db.settings_store import _coerce_iso_datetime
from .sse_slots import _cleanup_expired_sse_slots_on_conn, _count_active_sse_slots_on_conn


def mark_worker_heartbeat(worker_id: str, active_units: int = 0) -> None:
    _ensure_database()
    now = utc_now()
    with _connect() as conn:
        with _transaction(conn):
            conn.execute(
                """
                INSERT INTO worker_heartbeats (worker_id, last_seen_at, active_units)
                VALUES (?, ?, ?)
                ON CONFLICT(worker_id) DO UPDATE SET
                    last_seen_at = excluded.last_seen_at,
                    active_units = excluded.active_units
                """,
                (worker_id, now, max(0, int(active_units or 0))),
            )


def _worker_metric_snapshots_on_conn(conn: sqlite3.Connection, now: datetime) -> list[dict[str, Any]]:
    cutoff = (now - timedelta(seconds=WORKER_METRIC_SNAPSHOT_TTL_SECONDS)).isoformat()
    rows = conn.execute(
        """
        SELECT worker_id, snapshot_json, updated_at
        FROM worker_metric_snapshots
        WHERE updated_at > ?
        ORDER BY updated_at DESC
        """,
        (cutoff,),
    ).fetchall()
    workers: list[dict[str, Any]] = []
    for row in rows:
        updated_at = str(row["updated_at"] or "")
        updated_dt = _coerce_iso_datetime(updated_at)
        try:
            snapshot = json.loads(row["snapshot_json"] or "{}")
        except (json.JSONDecodeError, TypeError):
            snapshot = {}
        age_seconds = (
            max(0.0, (now - updated_dt).total_seconds())
            if updated_dt is not None
            else None
        )
        workers.append(
            {
                "worker_id": str(row["worker_id"]),
                "updated_at": updated_at,
                "age_seconds": round(age_seconds, 3) if age_seconds is not None else None,
                "snapshot": snapshot if isinstance(snapshot, dict) else {},
            }
        )
    return workers


def get_runtime_coordination_metrics() -> dict[str, Any]:
    _ensure_database()
    now_dt = datetime.now(timezone.utc)
    now = now_dt.isoformat()
    with _connect() as conn:
        active_sse_slots = _count_active_sse_slots_on_conn(conn, now=now)

        heartbeat_rows = conn.execute(
            """
            SELECT worker_id, last_seen_at, active_units
            FROM worker_heartbeats
            """
        ).fetchall()
        heartbeat_ages = []
        active_worker_count = 0
        worker_active_units = 0
        for row in heartbeat_rows:
            seen_at = _coerce_iso_datetime(str(row["last_seen_at"] or ""))
            if seen_at is None:
                continue
            age_seconds = max(0.0, (now_dt - seen_at).total_seconds())
            heartbeat_ages.append(age_seconds)
            if age_seconds <= WORKER_METRIC_SNAPSHOT_TTL_SECONDS:
                active_worker_count += 1
                worker_active_units += max(0, int(row["active_units"] or 0))

        lease_rows = conn.execute(
            """
            SELECT name, owner, lease_expires_at, updated_at, completed_at
            FROM background_leases
            ORDER BY name
            """
        ).fetchall()
        active_leases = [
            {
                "name": str(row["name"]),
                "owner": str(row["owner"]),
                "lease_expires_at": str(row["lease_expires_at"] or ""),
                "updated_at": str(row["updated_at"] or ""),
                "completed_at": str(row["completed_at"] or "") or None,
            }
            for row in lease_rows
            if str(row["lease_expires_at"] or "") > now
        ]
        worker_snapshots = _worker_metric_snapshots_on_conn(conn, now_dt)

    return {
        "gauges": {
            "sse.active_connections": active_sse_slots,
            "sse.expired_slots_cleaned": 0,
            "workers.active": active_worker_count,
            "workers.heartbeat_age_max_seconds": round(max(heartbeat_ages), 3)
            if heartbeat_ages
            else 0.0,
            "workers.active_units": worker_active_units,
            "background_leases.active": len(active_leases),
        },
        "background_leases": active_leases,
        "workers": worker_snapshots,
    }


def refresh_runtime_coordination_metrics(
    worker_id: str,
    snapshot: dict[str, Any],
) -> dict[str, Any]:
    """Perform low-frequency coordination maintenance and snapshot publication."""
    _ensure_database()
    normalized_worker_id = str(worker_id or "").strip()
    now = utc_now()
    payload = json.dumps(snapshot, sort_keys=True, separators=(",", ":"))
    with _connect() as conn:
        with _transaction(conn):
            expired_sse_slots = _cleanup_expired_sse_slots_on_conn(conn, now)
            if normalized_worker_id:
                conn.execute(
                    """
                    INSERT INTO worker_metric_snapshots (
                        worker_id, snapshot_json, updated_at
                    )
                    VALUES (?, ?, ?)
                    ON CONFLICT(worker_id) DO UPDATE SET
                        snapshot_json = excluded.snapshot_json,
                        updated_at = excluded.updated_at
                    """,
                    (normalized_worker_id, payload, now),
                )
        runtime = get_runtime_coordination_metrics()
    runtime.setdefault("gauges", {})["sse.expired_slots_cleaned"] = expired_sse_slots
    return runtime
