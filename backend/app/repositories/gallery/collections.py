"""Named gallery collections (albums) and their image memberships."""

import sqlite3
import uuid
from typing import Any, Sequence

from ...core.utils import utc_now
from ..db import (
    _build_gallery_filter_where,
    _connect,
    _ensure_database,
    _invalidate_gallery_query_caches_on_conn,
    _iter_sqlite_in_chunks,
    _transaction,
    _unique_sqlite_values,
)


class GalleryCollectionNameConflictError(ValueError):
    """Raised when a collection name is already used (case-insensitive)."""


_COLLECTION_SELECT_SQL = """
    SELECT
        c.id,
        c.name,
        c.position,
        c.is_default,
        c.created_at,
        c.updated_at,
        (
            SELECT COUNT(*)
            FROM gallery_collection_items AS items
            WHERE items.collection_id = c.id
        ) AS image_count,
        (
            SELECT e.id
            FROM gallery_collection_items AS items
            JOIN gallery_entries AS e ON e.id = items.image_id
            WHERE items.collection_id = c.id
            ORDER BY e.sort_seq DESC, e.id DESC
            LIMIT 1
        ) AS cover_image_id
    FROM gallery_collections AS c
"""


def _collection_from_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "name": row["name"],
        "position": int(row["position"] or 0),
        "is_default": bool(row["is_default"]),
        "image_count": int(row["image_count"] or 0),
        "cover_image_id": row["cover_image_id"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _get_collection_on_conn(conn: sqlite3.Connection, collection_id: str) -> dict[str, Any] | None:
    row = conn.execute(f"{_COLLECTION_SELECT_SQL} WHERE c.id = ?", (collection_id,)).fetchone()
    return _collection_from_row(row) if row else None


def _name_taken_on_conn(conn: sqlite3.Connection, name: str, *, exclude_id: str | None = None) -> bool:
    row = conn.execute(
        "SELECT id FROM gallery_collections WHERE name = ? COLLATE NOCASE AND id != ?",
        (name, exclude_id or ""),
    ).fetchone()
    return row is not None


def list_gallery_collections() -> list[dict[str, Any]]:
    _ensure_database()
    with _connect() as conn:
        rows = conn.execute(f"{_COLLECTION_SELECT_SQL} ORDER BY c.position ASC, c.created_at ASC, c.id ASC").fetchall()
    return [_collection_from_row(row) for row in rows]


def get_gallery_collection(collection_id: str) -> dict[str, Any] | None:
    _ensure_database()
    with _connect() as conn:
        return _get_collection_on_conn(conn, collection_id)


def create_gallery_collection(name: str) -> dict[str, Any]:
    _ensure_database()
    now = utc_now()
    collection_id = uuid.uuid4().hex
    with _connect() as conn:
        with _transaction(conn):
            if _name_taken_on_conn(conn, name):
                raise GalleryCollectionNameConflictError("A collection with this name already exists")
            row = conn.execute("SELECT COALESCE(MAX(position), -1) FROM gallery_collections").fetchone()
            position = int(row[0]) + 1 if row else 0
            conn.execute(
                """
                INSERT INTO gallery_collections (id, name, position, is_default, created_at, updated_at)
                VALUES (?, ?, ?, 0, ?, ?)
                """,
                (collection_id, name, position, now, now),
            )
            return _get_collection_on_conn(conn, collection_id)  # type: ignore[return-value]


def update_gallery_collection(
    collection_id: str,
    *,
    name: str | None = None,
    is_default: bool | None = None,
) -> dict[str, Any] | None:
    _ensure_database()
    with _connect() as conn:
        with _transaction(conn):
            if _get_collection_on_conn(conn, collection_id) is None:
                return None
            now = utc_now()
            if name is not None:
                if _name_taken_on_conn(conn, name, exclude_id=collection_id):
                    raise GalleryCollectionNameConflictError("A collection with this name already exists")
                conn.execute(
                    "UPDATE gallery_collections SET name = ?, updated_at = ? WHERE id = ?",
                    (name, now, collection_id),
                )
            if is_default is True:
                conn.execute(
                    "UPDATE gallery_collections SET is_default = 0, updated_at = ? WHERE is_default = 1 AND id != ?",
                    (now, collection_id),
                )
                conn.execute(
                    "UPDATE gallery_collections SET is_default = 1, updated_at = ? WHERE id = ?",
                    (now, collection_id),
                )
            elif is_default is False:
                conn.execute(
                    "UPDATE gallery_collections SET is_default = 0, updated_at = ? WHERE id = ?",
                    (now, collection_id),
                )
            return _get_collection_on_conn(conn, collection_id)


def delete_gallery_collection(collection_id: str) -> bool:
    _ensure_database()
    with _connect() as conn:
        with _transaction(conn):
            cursor = conn.execute("DELETE FROM gallery_collections WHERE id = ?", (collection_id,))
            deleted = cursor.rowcount > 0
            if deleted:
                _invalidate_gallery_query_caches_on_conn(conn)
            return deleted


def reorder_gallery_collections(ids: Sequence[str]) -> list[dict[str, Any]] | None:
    """Rewrite positions; returns None unless ids is exactly the current set."""
    _ensure_database()
    with _connect() as conn:
        with _transaction(conn):
            existing = {
                str(row["id"])
                for row in conn.execute("SELECT id FROM gallery_collections").fetchall()
            }
            if len(ids) != len(existing) or set(ids) != existing:
                return None
            now = utc_now()
            for position, collection_id in enumerate(ids):
                conn.execute(
                    "UPDATE gallery_collections SET position = ?, updated_at = ? WHERE id = ?",
                    (position, now, collection_id),
                )
    return list_gallery_collections()


def add_gallery_collection_items(collection_id: str, image_ids: Sequence[str]) -> int:
    _ensure_database()
    unique_ids = _unique_sqlite_values(image_ids)
    if not unique_ids:
        return 0
    now = utc_now()
    changed = 0
    with _connect() as conn:
        with _transaction(conn):
            for chunk in _iter_sqlite_in_chunks(unique_ids):
                placeholders = ", ".join("?" for _ in chunk)
                cursor = conn.execute(
                    f"""
                    INSERT OR IGNORE INTO gallery_collection_items (collection_id, image_id, added_at)
                    SELECT ?, id, ? FROM gallery_entries WHERE id IN ({placeholders})
                    """,
                    (collection_id, now, *chunk),
                )
                changed += max(0, cursor.rowcount)
            if changed:
                _invalidate_gallery_query_caches_on_conn(conn)
    return changed


def add_gallery_collection_items_by_filters(collection_id: str, filters: dict[str, Any] | None) -> int:
    _ensure_database()
    where_sql, params = _build_gallery_filter_where(filters)
    with _connect() as conn:
        with _transaction(conn):
            cursor = conn.execute(
                f"""
                INSERT OR IGNORE INTO gallery_collection_items (collection_id, image_id, added_at)
                SELECT ?, id, ? FROM gallery_entries{where_sql}
                """,
                (collection_id, utc_now(), *params),
            )
            changed = max(0, cursor.rowcount)
            if changed:
                _invalidate_gallery_query_caches_on_conn(conn)
    return changed


def remove_gallery_collection_items(collection_id: str, image_ids: Sequence[str]) -> int:
    _ensure_database()
    unique_ids = _unique_sqlite_values(image_ids)
    if not unique_ids:
        return 0
    changed = 0
    with _connect() as conn:
        with _transaction(conn):
            for chunk in _iter_sqlite_in_chunks(unique_ids):
                placeholders = ", ".join("?" for _ in chunk)
                cursor = conn.execute(
                    f"DELETE FROM gallery_collection_items WHERE collection_id = ? AND image_id IN ({placeholders})",
                    (collection_id, *chunk),
                )
                changed += max(0, cursor.rowcount)
            if changed:
                _invalidate_gallery_query_caches_on_conn(conn)
    return changed


def remove_gallery_collection_items_by_filters(collection_id: str, filters: dict[str, Any] | None) -> int:
    _ensure_database()
    where_sql, params = _build_gallery_filter_where(filters)
    with _connect() as conn:
        with _transaction(conn):
            cursor = conn.execute(
                f"""
                DELETE FROM gallery_collection_items
                WHERE collection_id = ?
                  AND image_id IN (SELECT id FROM gallery_entries{where_sql})
                """,
                (collection_id, *params),
            )
            changed = max(0, cursor.rowcount)
            if changed:
                _invalidate_gallery_query_caches_on_conn(conn)
    return changed


def get_gallery_image_collection_ids(image_id: str) -> list[str]:
    _ensure_database()
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT items.collection_id
            FROM gallery_collection_items AS items
            JOIN gallery_collections AS c ON c.id = items.collection_id
            WHERE items.image_id = ?
            ORDER BY c.position ASC, c.created_at ASC
            """,
            (image_id,),
        ).fetchall()
    return [str(row["collection_id"]) for row in rows]
