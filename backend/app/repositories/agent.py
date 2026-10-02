"""Agent conversations, turns, messages, image references and turn events."""

import json
import re
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Sequence

from ..core import settings as config
from ..core.utils import utc_now
from .db import _connect, _ensure_database, _transaction

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


# ── Conversations ──────────────────────────────────────────────


def create_conversation(title: str = "") -> dict[str, Any]:
    _ensure_database()
    now = utc_now()
    conversation_id = _new_id()
    clean_title = " ".join(str(title or "").split())[:200]
    with _connect() as conn:
        with _transaction(conn):
            count = conn.execute("SELECT COUNT(*) FROM agent_conversations").fetchone()[0]
            if int(count) >= config.AGENT_MAX_CONVERSATIONS:
                raise AgentConversationLimitError(
                    f"At most {config.AGENT_MAX_CONVERSATIONS} Agent conversations are kept. Delete one first."
                )
            conn.execute(
                """
                INSERT INTO agent_conversations (id, title, title_is_auto, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (conversation_id, clean_title, 0 if clean_title else 1, now, now),
            )
            row = conn.execute(f"{_CONVERSATION_SELECT_SQL} WHERE c.id = ?", (conversation_id,)).fetchone()
    return _conversation_from_row(row)


def list_conversations(limit: int | None = None) -> list[dict[str, Any]]:
    _ensure_database()
    effective = config.AGENT_MAX_CONVERSATIONS if limit is None else limit
    with _connect() as conn:
        rows = conn.execute(
            f"{_CONVERSATION_SELECT_SQL} ORDER BY c.updated_at DESC, c.id DESC LIMIT ?",
            (max(1, int(effective)),),
        ).fetchall()
    return [_conversation_from_row(row) for row in rows]


def get_conversation(conversation_id: str) -> dict[str, Any] | None:
    _ensure_database()
    with _connect() as conn:
        row = conn.execute(f"{_CONVERSATION_SELECT_SQL} WHERE c.id = ?", (conversation_id,)).fetchone()
    return _conversation_from_row(row) if row else None


def rename_conversation(conversation_id: str, title: str) -> dict[str, Any] | None:
    _ensure_database()
    clean_title = " ".join(str(title or "").split())[:200]
    with _connect() as conn:
        with _transaction(conn):
            cursor = conn.execute(
                """
                UPDATE agent_conversations
                SET title = ?, title_is_auto = 0, updated_at = ?
                WHERE id = ?
                """,
                (clean_title, utc_now(), conversation_id),
            )
            if cursor.rowcount == 0:
                return None
            row = conn.execute(f"{_CONVERSATION_SELECT_SQL} WHERE c.id = ?", (conversation_id,)).fetchone()
    return _conversation_from_row(row)


def delete_conversation(conversation_id: str) -> bool:
    _ensure_database()
    with _connect() as conn:
        with _transaction(conn):
            cursor = conn.execute("DELETE FROM agent_conversations WHERE id = ?", (conversation_id,))
            return cursor.rowcount > 0


# ── Turns ──────────────────────────────────────────────────────


def _path_ids(conn: sqlite3.Connection, conversation_id: str, head: str | None) -> list[str]:
    rows = conn.execute("SELECT id, parent_turn_id FROM agent_turns WHERE conversation_id = ?", (conversation_id,)).fetchall()
    parents = {row[0]: row[1] for row in rows}
    path: list[str] = []
    while head is not None:
        if head not in parents or head in path:
            raise AgentTurnConflictError("Invalid Agent branch")
        path.append(head)
        head = parents[head]
    return list(reversed(path))


def _branch_text(conn: sqlite3.Connection, conversation_id: str, path: list[str], text: str) -> str:
    rows = conn.execute("SELECT turn_id, ref_label, image_index, role FROM agent_message_images WHERE conversation_id = ?", (conversation_id,)).fetchall()
    allowed = {row["ref_label"] for row in rows if row["turn_id"] in path}
    aliases = {(path.index(row["turn_id"]) + 1, row["image_index"]): row["ref_label"] for row in rows if row["turn_id"] in path and row["role"] == "output"}

    def replace(match: re.Match[str]) -> str:
        label = aliases.get((int(match[1]), int(match[2])))
        if label is None:
            raise AgentAttachmentError("The mentioned image is not on this branch; attach it explicitly from Gallery")
        return f"@{label}"

    normalized = re.sub(r"@第?(\d+)轮图(\d+)", replace, text)
    for label in re.findall(r"@(round-\d+-(?:image|input)-\d+)", normalized):
        if label not in allowed:
            raise AgentAttachmentError("The mentioned image is not on this branch; attach it explicitly from Gallery")
    return normalized


def select_branch(conversation_id: str, selected_turn_id: str | None, expected_revision: int) -> bool:
    _ensure_database()
    with _connect() as conn:
        with _transaction(conn):
            row = conn.execute("SELECT branch_revision FROM agent_conversations WHERE id = ?", (conversation_id,)).fetchone()
            if row is None:
                return False
            if int(row[0]) != expected_revision:
                raise AgentTurnConflictError("The selected branch changed in another tab. Reload before switching.")
            _path_ids(conn, conversation_id, selected_turn_id)
            conn.execute("UPDATE agent_conversations SET selected_turn_id = ?, branch_revision = branch_revision + 1, updated_at = ? WHERE id = ?", (selected_turn_id, utc_now(), conversation_id))
    return True


def path_turn_ids(conversation_id: str, turn_id: str | None) -> list[str]:
    _ensure_database()
    with _connect() as conn:
        return _path_ids(conn, conversation_id, turn_id)


def branch_snapshot(conversation_id: str, *, before_seq: int | None = None, limit: int = 200) -> dict[str, Any] | None:
    _ensure_database()
    with _connect() as conn:
        with conn:
            # A deferred read transaction keeps the path/page consistent
            # without taking the write reservation used for branch mutations.
            conn.execute("BEGIN")
            conversation = conn.execute(f"{_CONVERSATION_SELECT_SQL} WHERE c.id = ?", (conversation_id,)).fetchone()
            if conversation is None:
                return None
            path = _path_ids(conn, conversation_id, conversation["selected_turn_id"])
            turns = conn.execute("SELECT t.*, m.text FROM agent_turns t JOIN agent_messages m ON m.id = t.user_message_id WHERE t.conversation_id = ? ORDER BY t.round_no", (conversation_id,)).fetchall()
            depths: dict[str, int] = {}
            branches = []
            for turn in turns:
                depths[turn["id"]] = depths.get(turn["parent_turn_id"], 0) + 1
                branches.append({"id": turn["id"], "parent_turn_id": turn["parent_turn_id"], "round_no": turn["round_no"], "path_round_no": depths[turn["id"]], "preview": turn["text"][:120], "status": turn["status"]})
            messages = []
            images = []
            if path:
                placeholders = ",".join("?" for _ in path)
                where = f"conversation_id = ? AND turn_id IN ({placeholders})"
                params: list[Any] = [conversation_id, *path]
                if before_seq is not None:
                    where += " AND seq < ?"
                    params.append(before_seq)
                raw = conn.execute(f"SELECT * FROM agent_messages WHERE {where} ORDER BY seq DESC LIMIT ?", [*params, max(1, limit)]).fetchall()
                messages = [{**_message_from_row(row), "path_round_no": depths[row["turn_id"]]} for row in reversed(raw)]
                message_ids = [row["id"] for row in raw]
                if message_ids:
                    marks = ",".join("?" for _ in message_ids)
                    images = [{**_image_from_row(row), "path_round_no": depths[row["turn_id"]]} for row in conn.execute(f"{_IMAGE_SELECT_SQL} WHERE i.message_id IN ({marks}) ORDER BY i.round_no, i.image_index", message_ids)]
            return {"conversation": _conversation_from_row(conversation), "messages": messages, "images": images, "branches": branches}


def create_turn(
    conversation_id: str,
    *,
    client_turn_id: str,
    text: str,
    attachment_image_ids: Sequence[str],
    model: str,
    image_params: dict[str, Any],
    lease_expires_at: str,
    action: str = "continue",
    source_turn_id: str | None = None,
    branch_revision: int | None = None,
) -> tuple[dict[str, Any], bool] | None:
    """Insert a turn with its user and empty assistant messages atomically.

    Returns ``(turn, created)``; ``created`` is False when ``client_turn_id`` was
    already accepted for this conversation (an idempotent replay). Returns None
    when the conversation does not exist.
    """
    _ensure_database()
    now = utc_now()
    with _connect() as conn:
        with _transaction(conn):
            conversation = conn.execute(
                "SELECT * FROM agent_conversations WHERE id = ?",
                (conversation_id,),
            ).fetchone()
            if conversation is None:
                return None
            existing = conn.execute(
                "SELECT * FROM agent_turns WHERE conversation_id = ? AND client_turn_id = ?",
                (conversation_id, client_turn_id),
            ).fetchone()
            if existing is not None:
                return _turn_from_row(existing), False
            if branch_revision is not None and branch_revision != conversation["branch_revision"]:
                raise AgentTurnConflictError("The selected branch changed in another tab. Reload before continuing.")
            parent_id = conversation["selected_turn_id"]
            if action != "continue":
                source = conn.execute("SELECT * FROM agent_turns WHERE id = ? AND conversation_id = ?", (source_turn_id, conversation_id)).fetchone()
                selected_path = _path_ids(conn, conversation_id, parent_id)
                if source is None or source["id"] not in selected_path:
                    raise AgentTurnConflictError("The source turn is not on the selected branch")
                parent_id = source["parent_turn_id"]
                if action == "regenerate":
                    text = conn.execute("SELECT text FROM agent_messages WHERE id = ?", (source["user_message_id"],)).fetchone()[0]
                    attachment_image_ids = [row[0] for row in conn.execute("SELECT image_id FROM agent_message_images WHERE turn_id = ? AND role = 'input'", (source["id"],))]
                    if any(value is None for value in attachment_image_ids):
                        raise AgentAttachmentError("An original attachment was deleted; edit the message to choose new attachments")
                    image_params = json.loads(source["image_params_json"] or "{}")
            elif source_turn_id is not None:
                raise AgentTurnConflictError("source_turn_id requires edit or regenerate")
            path_ids = _path_ids(conn, conversation_id, parent_id)
            text = _branch_text(conn, conversation_id, path_ids, text)
            active = conn.execute(
                "SELECT 1 FROM agent_turns WHERE conversation_id = ? AND status IN ('queued', 'running') LIMIT 1",
                (conversation_id,),
            ).fetchone()
            if active is not None:
                raise AgentTurnConflictError("This conversation already has a turn in progress")
            if int(conversation["turn_count"]) >= config.AGENT_MAX_TURNS_PER_CONVERSATION:
                raise AgentConversationLimitError(
                    f"A conversation holds at most {config.AGENT_MAX_TURNS_PER_CONVERSATION} turns. Start a new one."
                )

            attachment_ids = list(dict.fromkeys(str(value) for value in attachment_image_ids))
            if attachment_ids:
                placeholders = ",".join("?" for _ in attachment_ids)
                found = {
                    row["id"]
                    for row in conn.execute(
                        f"SELECT id FROM gallery_entries WHERE id IN ({placeholders})",
                        attachment_ids,
                    )
                }
                missing = [value for value in attachment_ids if value not in found]
                if missing:
                    raise AgentAttachmentError("An attached gallery image no longer exists")

            round_no = int(
                conn.execute(
                    "SELECT COALESCE(MAX(round_no), 0) FROM agent_turns WHERE conversation_id = ?",
                    (conversation_id,),
                ).fetchone()[0]
            ) + 1
            seq = int(
                conn.execute(
                    "SELECT COALESCE(MAX(seq), 0) FROM agent_messages WHERE conversation_id = ?",
                    (conversation_id,),
                ).fetchone()[0]
            )
            turn_id = _new_id()
            user_message_id = _new_id()
            assistant_message_id = _new_id()
            conn.execute(
                """
                INSERT INTO agent_turns (
                    id, conversation_id, round_no, client_turn_id, status, model,
                    image_params_json, lease_expires_at, user_message_id,
                    assistant_message_id, created_at, parent_turn_id
                )
                VALUES (?, ?, ?, ?, 'queued', ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    turn_id,
                    conversation_id,
                    round_no,
                    client_turn_id,
                    model,
                    json.dumps(image_params, ensure_ascii=False, sort_keys=True),
                    lease_expires_at,
                    user_message_id,
                    assistant_message_id,
                    now,
                    parent_id,
                ),
            )
            conn.execute(
                """
                INSERT INTO agent_messages (
                    id, conversation_id, turn_id, seq, round_no, role, text,
                    blocks_json, status, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, 'user', ?, '[]', 'complete', ?, ?)
                """,
                (user_message_id, conversation_id, turn_id, seq + 1, round_no, text, now, now),
            )
            conn.execute(
                """
                INSERT INTO agent_messages (
                    id, conversation_id, turn_id, seq, round_no, role, text,
                    blocks_json, status, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, 'assistant', '', '[]', 'streaming', ?, ?)
                """,
                (assistant_message_id, conversation_id, turn_id, seq + 2, round_no, now, now),
            )
            for index, image_id in enumerate(attachment_ids, start=1):
                conn.execute(
                    """
                    INSERT INTO agent_message_images (
                        id, conversation_id, turn_id, message_id, round_no, image_index,
                        role, ref_label, image_id, prompt, mode, status, created_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, 'input', ?, ?, '', 'generate', 'succeeded', ?)
                    """,
                    (
                        _new_id(),
                        conversation_id,
                        turn_id,
                        user_message_id,
                        round_no,
                        index,
                        f"round-{round_no}-input-{index}",
                        image_id,
                        now,
                    ),
                )
            title = conversation["title"]
            if int(conversation["title_is_auto"]) and not title:
                title = _title_from_text(text)
            conn.execute(
                """
                UPDATE agent_conversations
                SET title = ?, message_count = message_count + 2, turn_count = turn_count + 1,
                    updated_at = ?, selected_turn_id = ?, branch_revision = branch_revision + 1
                WHERE id = ?
                """,
                (title, now, turn_id, conversation_id),
            )
            row = conn.execute("SELECT * FROM agent_turns WHERE id = ?", (turn_id,)).fetchone()
    return _turn_from_row(row), True


def get_turn(turn_id: str) -> dict[str, Any] | None:
    _ensure_database()
    with _connect() as conn:
        row = conn.execute("SELECT * FROM agent_turns WHERE id = ?", (turn_id,)).fetchone()
    return _turn_from_row(row) if row else None


def get_turn_by_client_id(conversation_id: str, client_turn_id: str) -> dict[str, Any] | None:
    _ensure_database()
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM agent_turns WHERE conversation_id = ? AND client_turn_id = ?",
            (conversation_id, client_turn_id),
        ).fetchone()
    return _turn_from_row(row) if row else None


def claim_turn(turn_id: str, *, owner: str, lease_expires_at: str) -> bool:
    _ensure_database()
    now = utc_now()
    with _connect() as conn:
        with _transaction(conn):
            cursor = conn.execute(
                """
                UPDATE agent_turns
                SET status = 'running', lease_owner = ?, lease_expires_at = ?, started_at = ?
                WHERE id = ? AND status = 'queued'
                """,
                (owner, lease_expires_at, now, turn_id),
            )
            return cursor.rowcount > 0


def renew_turn_lease(turn_id: str, *, owner: str, lease_expires_at: str) -> bool:
    _ensure_database()
    with _connect() as conn:
        with _transaction(conn):
            cursor = conn.execute(
                """
                UPDATE agent_turns SET lease_expires_at = ?
                WHERE id = ? AND status = 'running' AND lease_owner = ? AND lease_expires_at > ?
                """,
                (lease_expires_at, turn_id, owner, utc_now()),
            )
            return cursor.rowcount > 0


def request_turn_cancel(turn_id: str) -> dict[str, Any] | None:
    """Flag an active turn for cancellation; returns the turn row either way."""
    _ensure_database()
    with _connect() as conn:
        with _transaction(conn):
            conn.execute(
                """
                UPDATE agent_turns SET cancel_requested = 1
                WHERE id = ? AND status IN ('queued', 'running')
                """,
                (turn_id,),
            )
            row = conn.execute("SELECT * FROM agent_turns WHERE id = ?", (turn_id,)).fetchone()
    return _turn_from_row(row) if row else None


def is_turn_cancel_requested(turn_id: str) -> bool:
    _ensure_database()
    with _connect() as conn:
        row = conn.execute(
            "SELECT cancel_requested, status FROM agent_turns WHERE id = ?",
            (turn_id,),
        ).fetchone()
    return bool(row and row["cancel_requested"])


def set_turn_rounds_used(turn_id: str, rounds_used: int, *, owner: str | None = None) -> None:
    _ensure_database()
    with _connect() as conn:
        with _transaction(conn):
            _require_owner(conn, owner, turn_id=turn_id)
            conn.execute(
                "UPDATE agent_turns SET rounds_used = ? WHERE id = ?",
                (int(rounds_used), turn_id),
            )


def finish_turn(
    turn_id: str,
    status: str,
    *,
    error_message: str | None = None,
    rounds_used: int | None = None,
    expired_before: str | None = None,
    owner: str | None = None,
) -> dict[str, Any] | None:
    """Move a non-terminal turn to a terminal status and settle its assistant message."""
    if status not in TERMINAL_TURN_STATUSES:
        raise ValueError(f"Unsupported terminal turn status: {status}")
    _ensure_database()
    now = utc_now()
    with _connect() as conn:
        with _transaction(conn):
            current = conn.execute("SELECT * FROM agent_turns WHERE id = ?", (turn_id,)).fetchone()
            if current is None:
                return None
            if current["status"] in TERMINAL_TURN_STATUSES:
                return None if expired_before is not None else _turn_from_row(current)
            if expired_before is not None and (
                current["lease_expires_at"] is None or current["lease_expires_at"] >= expired_before
            ):
                return None
            if owner is not None and current["lease_owner"] != owner:
                return _turn_from_row(current)
            conn.execute(
                """
                UPDATE agent_turns
                SET status = ?, error_message = ?, finished_at = ?, lease_owner = NULL,
                    rounds_used = COALESCE(?, rounds_used)
                WHERE id = ?
                """,
                (status, error_message, now, rounds_used, turn_id),
            )
            if current["assistant_message_id"]:
                conn.execute(
                    "UPDATE agent_messages SET status = ?, updated_at = ? WHERE id = ?",
                    (_MESSAGE_STATUS_FOR_TURN[status], now, current["assistant_message_id"]),
                )
            row = conn.execute("SELECT * FROM agent_turns WHERE id = ?", (turn_id,)).fetchone()
    return _turn_from_row(row)


def sweep_stale_turns() -> list[dict[str, Any]]:
    """Interrupt turns whose lease expired (worker died or never started them).

    The candidate lookup is a plain read: idle sweeps must not take the write
    lock, and they run from every conversation read and open event stream.
    """
    _ensure_database()
    now = utc_now()
    with _connect() as conn:
        stale = conn.execute(
            """
            SELECT id FROM agent_turns
            WHERE status IN ('queued', 'running')
              AND lease_expires_at IS NOT NULL AND lease_expires_at < ?
            LIMIT 100
            """,
            (now,),
        ).fetchall()
    interrupted = []
    for row in stale:
        turn = finish_turn(
            row["id"],
            "interrupted",
            error_message="The turn was interrupted before it finished.",
            expired_before=now,
        )
        if turn is not None:
            interrupted.append(turn)
    return interrupted


def list_turns_needing_reconcile(conversation_id: str) -> list[dict[str, Any]]:
    """Terminal turns that still hold pending image rows (e.g. after an interrupt)."""
    _ensure_database()
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT DISTINCT t.* FROM agent_turns AS t
            JOIN agent_message_images AS i ON i.turn_id = t.id
            WHERE t.conversation_id = ?
              AND t.status IN ('completed', 'failed', 'cancelled', 'interrupted')
              AND i.status = 'pending'
            """,
            (conversation_id,),
        ).fetchall()
    return [_turn_from_row(row) for row in rows]


# ── Messages ───────────────────────────────────────────────────


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


# ── Message images ─────────────────────────────────────────────


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


def list_pending_images_for_turn(turn_id: str) -> list[dict[str, Any]]:
    _ensure_database()
    with _connect() as conn:
        rows = conn.execute(
            f"{_IMAGE_SELECT_SQL} WHERE i.turn_id = ? AND i.status = 'pending'",
            (turn_id,),
        ).fetchall()
    return [_image_from_row(row) for row in rows]


# ── Turn events ────────────────────────────────────────────────


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
