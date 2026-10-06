"""Agent turns: admission, leases, cancellation, completion and stale-turn sweeps."""

import json
from typing import Any, Sequence

from ...core import settings as config
from ...core.utils import utc_now
from ..db import _connect, _ensure_database, _transaction
from ._common import AgentAttachmentError, AgentConversationLimitError, AgentTurnConflictError, TERMINAL_TURN_STATUSES, _MESSAGE_STATUS_FOR_TURN, _new_id, _require_owner, _title_from_text, _turn_from_row
from .branches import _branch_text, _path_ids


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
    web_search_enabled: bool = False,
    execution_snapshot: dict[str, Any] | None = None,
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
                    assistant_message_id, created_at, parent_turn_id, web_search_enabled,
                    execution_snapshot_json
                )
                VALUES (?, ?, ?, ?, 'queued', ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    int(web_search_enabled),
                    json.dumps(execution_snapshot or {}, ensure_ascii=False, sort_keys=True),
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
                message_row = conn.execute("SELECT blocks_json FROM agent_messages WHERE id = ?", (current["assistant_message_id"],)).fetchone()
                try:
                    blocks = json.loads(message_row[0] or "[]") if message_row else []
                except (TypeError, ValueError):
                    blocks = []
                changed = False
                for block in blocks if isinstance(blocks, list) else []:
                    if isinstance(block, dict) and block.get("type") == "search" and block.get("status") not in {"completed", "failed", "cancelled", "interrupted"}:
                        block["status"] = status if status != "completed" else "interrupted"
                        changed = True
                if changed:
                    conn.execute("UPDATE agent_messages SET blocks_json = ? WHERE id = ?", (json.dumps(blocks, ensure_ascii=False), current["assistant_message_id"]))
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


LEGACY_IMAGE_WAIT_TIMEOUT_ERROR = "Timed out waiting for the image job; it may still finish in the gallery."


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
              AND (i.status = 'pending'
                   OR (i.status = 'failed' AND i.job_id IS NOT NULL AND i.error = ?))
            """,
            (conversation_id, LEGACY_IMAGE_WAIT_TIMEOUT_ERROR),
        ).fetchall()
    return [_turn_from_row(row) for row in rows]
