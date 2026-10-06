"""Shared errors, constants and row converters for the Agent repositories."""

import json
import sqlite3
import uuid
from typing import Any

from ...core.utils import utc_now


ACTIVE_TURN_STATUSES = ("queued", "running")


TERMINAL_TURN_STATUSES = ("completed", "failed", "cancelled", "interrupted")


_MESSAGE_STATUS_FOR_TURN = {
    "completed": "complete",
    "failed": "failed",
    "cancelled": "cancelled",
    "interrupted": "interrupted",
}


DEFAULT_TITLE_MAX_CHARS = 40


class AgentConversationLimitError(ValueError):
    """The conversation or turn cap has been reached."""


class AgentTurnConflictError(ValueError):
    """The conversation already has a queued or running turn."""


class AgentAttachmentError(ValueError):
    """An attached gallery image no longer exists."""


class AgentLeaseLostError(RuntimeError):
    """A stale runner attempted to write after losing its lease."""


def _require_owner(
    conn: sqlite3.Connection,
    owner: str | None,
    *,
    turn_id: str | None = None,
    message_id: str | None = None,
    image_row_id: str | None = None,
) -> None:
    if owner is None:
        return
    if message_id is not None:
        message = conn.execute("SELECT turn_id FROM agent_messages WHERE id = ?", (message_id,)).fetchone()
        turn_id = message[0] if message else None
    if image_row_id is not None:
        image = conn.execute("SELECT turn_id FROM agent_message_images WHERE id = ?", (image_row_id,)).fetchone()
        turn_id = image[0] if image else None
    row = conn.execute("SELECT status, lease_owner, lease_expires_at FROM agent_turns WHERE id = ?", (turn_id,)).fetchone()
    if (
        row is None or row["status"] != "running" or row["lease_owner"] != owner
        or not row["lease_expires_at"] or row["lease_expires_at"] <= utc_now()
    ):
        raise AgentLeaseLostError("Agent turn lease was lost")


def _new_id() -> str:
    return uuid.uuid4().hex


def _title_from_text(text: str) -> str:
    collapsed = " ".join(str(text or "").split())
    return collapsed[:DEFAULT_TITLE_MAX_CHARS]


def _conversation_from_row(row: sqlite3.Row) -> dict[str, Any]:
    keys = row.keys()
    return {
        "id": row["id"],
        "title": row["title"],
        "message_count": int(row["message_count"] or 0),
        "turn_count": int(row["turn_count"] or 0),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "active_turn_id": row["active_turn_id"] if "active_turn_id" in keys else None,
        "selected_turn_id": row["selected_turn_id"],
        "branch_revision": int(row["branch_revision"]),
    }


_CONVERSATION_SELECT_SQL = """
    SELECT
        c.id, c.title, c.message_count, c.turn_count, c.created_at, c.updated_at,
        c.selected_turn_id, c.branch_revision,
        t.id AS active_turn_id
    FROM agent_conversations AS c
    LEFT JOIN agent_turns AS t
        ON t.conversation_id = c.id AND t.status IN ('queued', 'running')
"""


def _turn_from_row(row: sqlite3.Row) -> dict[str, Any]:
    try:
        image_params = json.loads(row["image_params_json"] or "{}")
    except (TypeError, ValueError):
        image_params = {}
    try:
        snapshot = json.loads(row["execution_snapshot_json"] or "{}")
    except (TypeError, ValueError):
        snapshot = {}
    return {
        "id": row["id"],
        "conversation_id": row["conversation_id"],
        "round_no": int(row["round_no"]),
        "client_turn_id": row["client_turn_id"],
        "status": row["status"],
        "cancel_requested": bool(row["cancel_requested"]),
        "lease_owner": row["lease_owner"],
        "lease_expires_at": row["lease_expires_at"],
        "rounds_used": int(row["rounds_used"] or 0),
        "model": row["model"] or "",
        "image_params": image_params if isinstance(image_params, dict) else {},
        "error_message": row["error_message"],
        "user_message_id": row["user_message_id"],
        "assistant_message_id": row["assistant_message_id"],
        "created_at": row["created_at"],
        "started_at": row["started_at"],
        "finished_at": row["finished_at"],
        "parent_turn_id": row["parent_turn_id"],
        "web_search_enabled": bool(row["web_search_enabled"]),
        "execution_snapshot": snapshot if isinstance(snapshot, dict) else {},
    }


def _message_from_row(row: sqlite3.Row) -> dict[str, Any]:
    try:
        blocks = json.loads(row["blocks_json"] or "[]")
    except (TypeError, ValueError):
        blocks = []
    return {
        "id": row["id"],
        "conversation_id": row["conversation_id"],
        "turn_id": row["turn_id"],
        "seq": int(row["seq"]),
        "round_no": int(row["round_no"]),
        "role": row["role"],
        "text": row["text"],
        "blocks": blocks if isinstance(blocks, list) else [],
        "status": row["status"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _image_from_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "conversation_id": row["conversation_id"],
        "turn_id": row["turn_id"],
        "message_id": row["message_id"],
        "round_no": int(row["round_no"]),
        "image_index": int(row["image_index"]),
        "role": row["role"],
        "ref_label": row["ref_label"],
        "image_id": row["image_id"],
        "job_id": row["job_id"],
        "item_id": row["item_id"],
        "prompt": row["prompt"] or "",
        "mode": row["mode"],
        "status": row["status"],
        "error": row["error"],
        "filename": row["filename"] if "filename" in row.keys() else None,
        "created_at": row["created_at"],
    }


_IMAGE_SELECT_SQL = """
    SELECT i.*, e.filename AS filename
    FROM agent_message_images AS i
    LEFT JOIN gallery_entries AS e ON e.id = i.image_id
"""
