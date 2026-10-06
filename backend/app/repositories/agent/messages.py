"""Agent messages and the image references attached to them."""

import json
from typing import Any, Sequence

from ...core.utils import utc_now
from ..db import _connect, _ensure_database, _transaction
from ._common import _IMAGE_SELECT_SQL, _image_from_row, _message_from_row, _new_id, _require_owner
from .turns import LEGACY_IMAGE_WAIT_TIMEOUT_ERROR


def list_messages(
    conversation_id: str,
    *,
    before_seq: int | None = None,
    limit: int = 200,
    turn_ids: Sequence[str] | None = None,
) -> list[dict[str, Any]]:
    """Return up to ``limit`` messages older than ``before_seq``, in ascending order."""
    _ensure_database()
    params: list[Any] = [conversation_id]
    where = "conversation_id = ?"
    if turn_ids is not None:
        if not turn_ids:
            return []
        where += f" AND turn_id IN ({','.join('?' for _ in turn_ids)})"
        params.extend(turn_ids)
    if before_seq is not None:
        where += " AND seq < ?"
        params.append(int(before_seq))
    params.append(max(1, int(limit)))
    with _connect() as conn:
        rows = conn.execute(
            f"SELECT * FROM agent_messages WHERE {where} ORDER BY seq DESC LIMIT ?",
            params,
        ).fetchall()
    return [_message_from_row(row) for row in reversed(rows)]


def get_message(message_id: str) -> dict[str, Any] | None:
    _ensure_database()
    with _connect() as conn:
        row = conn.execute("SELECT * FROM agent_messages WHERE id = ?", (message_id,)).fetchone()
    return _message_from_row(row) if row else None


def update_message_content(message_id: str, *, text: str, blocks: list[dict[str, Any]], owner: str | None = None) -> None:
    _ensure_database()
    with _connect() as conn:
        with _transaction(conn):
            _require_owner(conn, owner, message_id=message_id)
            conn.execute(
                "UPDATE agent_messages SET text = ?, blocks_json = ?, updated_at = ? WHERE id = ?",
                (text, json.dumps(blocks, ensure_ascii=False, separators=(",", ":")), utc_now(), message_id),
            )


def list_conversation_images(
    conversation_id: str,
    *,
    message_ids: Sequence[str] | None = None,
    turn_ids: Sequence[str] | None = None,
) -> list[dict[str, Any]]:
    _ensure_database()
    params: list[Any] = [conversation_id]
    where = "i.conversation_id = ?"
    if turn_ids is not None:
        if not turn_ids:
            return []
        where += f" AND i.turn_id IN ({','.join('?' for _ in turn_ids)})"
        params.extend(turn_ids)
    if message_ids is not None:
        if not message_ids:
            return []
        placeholders = ",".join("?" for _ in message_ids)
        where += f" AND i.message_id IN ({placeholders})"
        params.extend(message_ids)
    with _connect() as conn:
        rows = conn.execute(
            f"""
            {_IMAGE_SELECT_SQL}
            WHERE {where}
            ORDER BY i.round_no ASC, i.role DESC, i.image_index ASC
            """,
            params,
        ).fetchall()
    return [_image_from_row(row) for row in rows]


def get_image_by_label(conversation_id: str, ref_label: str) -> dict[str, Any] | None:
    _ensure_database()
    with _connect() as conn:
        row = conn.execute(
            f"{_IMAGE_SELECT_SQL} WHERE i.conversation_id = ? AND i.ref_label = ?",
            (conversation_id, ref_label),
        ).fetchone()
    return _image_from_row(row) if row else None


def insert_pending_output_image(
    *,
    conversation_id: str,
    turn_id: str,
    message_id: str,
    round_no: int,
    item_id: str,
    prompt: str,
    mode: str,
    owner: str | None = None,
) -> dict[str, Any]:
    _ensure_database()
    now = utc_now()
    with _connect() as conn:
        with _transaction(conn):
            _require_owner(conn, owner, turn_id=turn_id)
            index = int(
                conn.execute(
                    """
                    SELECT COALESCE(MAX(image_index), 0) FROM agent_message_images
                    WHERE conversation_id = ? AND round_no = ? AND role = 'output'
                    """,
                    (conversation_id, round_no),
                ).fetchone()[0]
            ) + 1
            row_id = _new_id()
            conn.execute(
                """
                INSERT INTO agent_message_images (
                    id, conversation_id, turn_id, message_id, round_no, image_index,
                    role, ref_label, item_id, prompt, mode, status, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, 'output', ?, ?, ?, ?, 'pending', ?)
                """,
                (
                    row_id,
                    conversation_id,
                    turn_id,
                    message_id,
                    round_no,
                    index,
                    f"round-{round_no}-image-{index}",
                    item_id,
                    prompt,
                    mode,
                    now,
                ),
            )
            row = conn.execute(f"{_IMAGE_SELECT_SQL} WHERE i.id = ?", (row_id,)).fetchone()
    return _image_from_row(row)


def set_image_job(row_id: str, job_id: str, *, owner: str | None = None) -> None:
    _ensure_database()
    with _connect() as conn:
        with _transaction(conn):
            _require_owner(conn, owner, image_row_id=row_id)
            conn.execute("UPDATE agent_message_images SET job_id = ? WHERE id = ?", (job_id, row_id))


def settle_image(
    row_id: str,
    *,
    status: str,
    image_id: str | None = None,
    error: str | None = None,
    owner: str | None = None,
) -> dict[str, Any] | None:
    """Record an image outcome; a gallery image deleted meanwhile is stored as NULL."""
    if status not in {"pending", "succeeded", "failed", "cancelled"}:
        raise ValueError(f"Unsupported image status: {status}")
    _ensure_database()
    with _connect() as conn:
        with _transaction(conn):
            _require_owner(conn, owner, image_row_id=row_id)
            if image_id:
                exists = conn.execute(
                    "SELECT 1 FROM gallery_entries WHERE id = ?", (image_id,)
                ).fetchone()
                if exists is None:
                    image_id = None
            conn.execute(
                """
                UPDATE agent_message_images
                SET status = ?, image_id = COALESCE(?, image_id), error = ?
                WHERE id = ?
                """,
                (status, image_id, error, row_id),
            )
            row = conn.execute(f"{_IMAGE_SELECT_SQL} WHERE i.id = ?", (row_id,)).fetchone()
    return _image_from_row(row) if row else None


def list_reconcilable_images_for_turn(turn_id: str) -> list[dict[str, Any]]:
    """Pending rows plus legacy rows failed only because the wait window ended."""
    _ensure_database()
    with _connect() as conn:
        rows = conn.execute(
            f"""{_IMAGE_SELECT_SQL}
            WHERE i.turn_id = ?
              AND (i.status = 'pending'
                   OR (i.status = 'failed' AND i.job_id IS NOT NULL AND i.error = ?))""",
            (turn_id, LEGACY_IMAGE_WAIT_TIMEOUT_ERROR),
        ).fetchall()
    return [_image_from_row(row) for row in rows]


def list_pending_images_for_turn(turn_id: str) -> list[dict[str, Any]]:
    _ensure_database()
    with _connect() as conn:
        rows = conn.execute(
            f"{_IMAGE_SELECT_SQL} WHERE i.turn_id = ? AND i.status = 'pending'",
            (turn_id,),
        ).fetchall()
    return [_image_from_row(row) for row in rows]
