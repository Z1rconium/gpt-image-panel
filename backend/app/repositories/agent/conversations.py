"""Agent conversation rows."""

from typing import Any

from ...core import settings as config
from ...core.utils import utc_now
from ..db import _connect, _ensure_database, _transaction
from ._common import AgentConversationLimitError, _CONVERSATION_SELECT_SQL, _conversation_from_row, _new_id


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
