"""Branch paths: path resolution, branch selection and the branch-aware conversation snapshot."""

import re
import sqlite3
from typing import Any

from ...core.utils import utc_now
from ..db import _connect, _ensure_database, _transaction
from ._common import AgentAttachmentError, AgentTurnConflictError, _CONVERSATION_SELECT_SQL, _IMAGE_SELECT_SQL, _conversation_from_row, _image_from_row, _message_from_row


def _path_ids(conn: sqlite3.Connection, conversation_id: str, head: str | None) -> list[str]:
    rows = conn.execute("SELECT id, parent_turn_id FROM agent_turns WHERE conversation_id = ?", (conversation_id,)).fetchall()
    parents = {row[0]: row[1] for row in rows}
    path: list[str] = []
    visited: set[str] = set()
    while head is not None:
        if head not in parents or head in visited:
            raise AgentTurnConflictError("Invalid Agent branch")
        path.append(head)
        visited.add(head)
        head = parents[head]
    return list(reversed(path))


def _branch_text(conn: sqlite3.Connection, conversation_id: str, path: list[str], text: str) -> str:
    rows = conn.execute("SELECT turn_id, ref_label, image_index, role FROM agent_message_images WHERE conversation_id = ?", (conversation_id,)).fetchall()
    path_rounds = {turn_id: index for index, turn_id in enumerate(path, start=1)}
    allowed = {row["ref_label"] for row in rows if row["turn_id"] in path_rounds}
    aliases = {
        (path_rounds[row["turn_id"]], row["image_index"]): row["ref_label"]
        for row in rows if row["turn_id"] in path_rounds and row["role"] == "output"
    }

    def replace(match: re.Match[str]) -> str:
        # A mention is untrusted text. Bound its numeric fields before int()
        # so thousands of digits cannot trip Python's integer conversion cap.
        if len(match[1]) > 10 or len(match[2]) > 10:
            raise AgentAttachmentError("The mentioned image is not on this branch; attach it explicitly from Gallery")
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
