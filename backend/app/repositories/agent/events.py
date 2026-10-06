"""Replayable per-turn event log."""

import json
from datetime import datetime, timedelta, timezone
from typing import Any, Sequence

from ...core.utils import utc_now
from ..db import _connect, _ensure_database, _transaction
from ._common import _require_owner


def read_event_cursor(turn_id: str) -> int:
    """Last persisted event sequence for a turn (0 when it has none)."""
    _ensure_database()
    with _connect() as conn:
        row = conn.execute(
            "SELECT COALESCE(MAX(seq), 0) FROM agent_turn_events WHERE turn_id = ?",
            (turn_id,),
        ).fetchone()
    return int(row[0] or 0)


def append_turn_events(
    turn_id: str,
    events: Sequence[tuple[str, dict[str, Any]]],
    *,
    start_seq: int,
    owner: str | None = None,
) -> int:
    """Persist a contiguous batch of turn events in one write transaction.

    The caller owns sequence numbering (a turn has exactly one writer holding
    its lease), so the batch needs no per-event MAX(seq) scan. Returns the last
    sequence written, or ``start_seq - 1`` when the batch is empty.
    """
    if not events:
        return int(start_seq) - 1
    _ensure_database()
    now = utc_now()
    rows = [
        (
            turn_id,
            int(start_seq) + offset,
            event_type,
            json.dumps(data, ensure_ascii=False, separators=(",", ":")),
            now,
        )
        for offset, (event_type, data) in enumerate(events)
    ]
    with _connect() as conn:
        with _transaction(conn):
            _require_owner(conn, owner, turn_id=turn_id)
            conn.executemany(
                """
                INSERT INTO agent_turn_events (turn_id, seq, type, data_json, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                rows,
            )
    return rows[-1][1]


def append_turn_event(turn_id: str, event_type: str, data: dict[str, Any]) -> int:
    _ensure_database()
    with _connect() as conn:
        with _transaction(conn):
            seq = int(
                conn.execute(
                    "SELECT COALESCE(MAX(seq), 0) FROM agent_turn_events WHERE turn_id = ?",
                    (turn_id,),
                ).fetchone()[0]
            ) + 1
            conn.execute(
                """
                INSERT INTO agent_turn_events (turn_id, seq, type, data_json, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    turn_id,
                    seq,
                    event_type,
                    json.dumps(data, ensure_ascii=False, separators=(",", ":")),
                    utc_now(),
                ),
            )
    return seq


def list_turn_events(turn_id: str, *, after_seq: int = 0, limit: int = 200) -> list[dict[str, Any]]:
    _ensure_database()
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT seq, type, data_json FROM agent_turn_events
            WHERE turn_id = ? AND seq > ?
            ORDER BY seq ASC LIMIT ?
            """,
            (turn_id, int(after_seq), max(1, int(limit))),
        ).fetchall()
    events = []
    for row in rows:
        try:
            data = json.loads(row["data_json"])
        except (TypeError, ValueError):
            data = {}
        events.append({"seq": int(row["seq"]), "type": row["type"], "data": data})
    return events


def purge_turn_events(retention_seconds: int) -> int:
    """Drop events of turns that finished longer than ``retention_seconds`` ago."""
    _ensure_database()
    cutoff = (datetime.now(timezone.utc) - timedelta(seconds=max(1, int(retention_seconds)))).isoformat()
    with _connect() as conn:
        with _transaction(conn):
            cursor = conn.execute(
                """
                DELETE FROM agent_turn_events
                WHERE turn_id IN (
                    SELECT id FROM agent_turns
                    WHERE finished_at IS NOT NULL AND finished_at < ?
                )
                """,
                (cutoff,),
            )
            return cursor.rowcount
