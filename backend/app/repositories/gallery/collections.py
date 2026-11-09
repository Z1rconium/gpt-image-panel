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
from .queries import get_gallery_entries_by_ids


class GalleryCollectionNameConflictError(ValueError):
    """Raised when a collection name is already used (case-insensitive)."""


class GalleryCollectionAliasConflictError(ValueError):
    """Raised when an asset alias is already used (case-insensitive)."""


_UNSET = object()

_COLLECTION_SELECT_SQL = """
    SELECT
        c.id,
        c.name,
        c.asset_alias,
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
        ) AS cover_image_id,
        (
            SELECT e.filename
            FROM gallery_collection_items AS items
            JOIN gallery_entries AS e ON e.id = items.image_id
            WHERE items.collection_id = c.id
            ORDER BY e.sort_seq DESC, e.id DESC
            LIMIT 1
        ) AS cover_filename
    FROM gallery_collections AS c
"""


def _collection_from_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "name": row["name"],
        "asset_alias": row["asset_alias"],
        "position": int(row["position"] or 0),
        "is_default": bool(row["is_default"]),
        "image_count": int(row["image_count"] or 0),
        "cover_image_id": row["cover_image_id"],
        "cover_filename": row["cover_filename"],
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


def _alias_taken_on_conn(conn: sqlite3.Connection, alias: str, *, exclude_id: str | None = None) -> bool:
    row = conn.execute(
        "SELECT id FROM gallery_collections WHERE asset_alias = ? COLLATE NOCASE AND id != ?",
        (alias, exclude_id or ""),
    ).fetchone()
    return row is not None


def has_asset_collections() -> bool:
    """Return True if at least one collection has an asset alias configured."""
    _ensure_database()
    with _connect() as conn:
        row = conn.execute(
            "SELECT 1 FROM gallery_collections WHERE asset_alias IS NOT NULL AND trim(asset_alias) != '' LIMIT 1"
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


def create_gallery_collection(name: str, *, asset_alias: str | None = None) -> dict[str, Any]:
    _ensure_database()
    now = utc_now()
    collection_id = uuid.uuid4().hex
    with _connect() as conn:
        with _transaction(conn):
            if _name_taken_on_conn(conn, name):
                raise GalleryCollectionNameConflictError("A collection with this name already exists")
            if asset_alias is not None and _alias_taken_on_conn(conn, asset_alias):
                raise GalleryCollectionAliasConflictError("A collection with this asset alias already exists")
            row = conn.execute("SELECT COALESCE(MAX(position), -1) FROM gallery_collections").fetchone()
            position = int(row[0]) + 1 if row else 0
            conn.execute(
                """
                INSERT INTO gallery_collections (id, name, asset_alias, position, is_default, created_at, updated_at)
                VALUES (?, ?, ?, ?, 0, ?, ?)
                """,
                (collection_id, name, asset_alias, position, now, now),
            )
            return _get_collection_on_conn(conn, collection_id)  # type: ignore[return-value]


def update_gallery_collection(
    collection_id: str,
    *,
    name: str | None = None,
    is_default: bool | None = None,
    asset_alias: str | None | object = _UNSET,
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
            if asset_alias is not _UNSET:
                if asset_alias is not None:
                    if _alias_taken_on_conn(conn, asset_alias, exclude_id=collection_id):
                        raise GalleryCollectionAliasConflictError("A collection with this asset alias already exists")
                    conn.execute(
                        "UPDATE gallery_collections SET asset_alias = ?, updated_at = ? WHERE id = ?",
                        (asset_alias, now, collection_id),
                    )
                else:
                    conn.execute(
                        "UPDATE gallery_collections SET asset_alias = NULL, updated_at = ? WHERE id = ?",
                        (now, collection_id),
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


UNSUPPORTED_REFERENCE_FORMATS = frozenset({"svg", "gif"})
UNSUPPORTED_REFERENCE_EXTENSIONS = (".svg", ".gif")


def is_supported_reference_format(output_format: str | None, filename: str | None = None) -> bool:
    fmt = (output_format or "").strip().lower()
    if fmt in UNSUPPORTED_REFERENCE_FORMATS:
        return False
    name = (filename or "").strip().lower()
    if any(name.endswith(ext) for ext in UNSUPPORTED_REFERENCE_EXTENSIONS):
        return False
    return True


def list_collection_reference_image_ids_on_conn(
    conn: sqlite3.Connection,
    collection_id: str,
) -> list[str]:
    """Fetch ordered image IDs of supported raster reference images for a collection on an open connection."""
    rows = conn.execute(
        """
        SELECT e.id, e.output_format, e.filename
        FROM gallery_collection_items AS i
        JOIN gallery_entries AS e ON e.id = i.image_id
        WHERE i.collection_id = ?
        ORDER BY i.position IS NULL, i.position ASC, i.added_at ASC, i.image_id ASC
        """,
        (collection_id,),
    ).fetchall()
    return [
        str(row["id"])
        for row in rows
        if is_supported_reference_format(row["output_format"], row["filename"])
    ]


def list_collection_reference_entries(collection_id: str) -> dict[str, Any] | None:
    """Fetch valid raster reference images for a collection in unified order.

    Order: ORDER BY items.position IS NULL, items.position ASC, items.added_at ASC, items.image_id ASC.
    SVG and GIF entries are excluded and reported via skipped_unsupported.
    Returns None if the collection does not exist.
    """
    _ensure_database()
    with _connect() as conn:
        collection = _get_collection_on_conn(conn, collection_id)
        if collection is None:
            return None
        rows = conn.execute(
            """
            SELECT items.image_id
            FROM gallery_collection_items AS items
            WHERE items.collection_id = ?
            ORDER BY items.position IS NULL, items.position ASC, items.added_at ASC, items.image_id ASC
            """,
            (collection_id,),
        ).fetchall()
        ordered_ids = [str(row["image_id"]) for row in rows]

    all_entries = get_gallery_entries_by_ids(ordered_ids)
    valid_entries: list[Any] = []
    skipped_unsupported = 0
    for entry in all_entries:
        if is_supported_reference_format(entry.output_format, entry.filename):
            valid_entries.append(entry)
        else:
            skipped_unsupported += 1

    return {
        "collection": collection,
        "images": valid_entries,
        "total_count": len(ordered_ids),
        "skipped_unsupported": skipped_unsupported,
    }


def reorder_gallery_collection_items(
    collection_id: str,
    ids: Sequence[str],
) -> dict[str, Any] | None:
    """Rewrite item positions for a collection.

    Returns None if collection not found or ids is not exactly the current member set.
    """
    _ensure_database()
    with _connect() as conn:
        with _transaction(conn):
            if _get_collection_on_conn(conn, collection_id) is None:
                return None
            existing = {
                str(row["image_id"])
                for row in conn.execute(
                    "SELECT image_id FROM gallery_collection_items WHERE collection_id = ?",
                    (collection_id,),
                ).fetchall()
            }
            if len(ids) != len(existing) or set(ids) != existing:
                return None
            now = utc_now()
            for position, image_id in enumerate(ids):
                conn.execute(
                    """
                    UPDATE gallery_collection_items
                    SET position = ?
                    WHERE collection_id = ? AND image_id = ?
                    """,
                    (position, collection_id, image_id),
                )
            conn.execute(
                "UPDATE gallery_collections SET updated_at = ? WHERE id = ?",
                (now, collection_id),
            )
            return _get_collection_on_conn(conn, collection_id)



def export_gallery_collections() -> list[dict[str, Any]]:
    """Snapshot collection names and ordered member IDs for archive export."""
    _ensure_database()
    with _connect() as conn:
        collections = conn.execute(
            "SELECT id, name, asset_alias, is_default FROM gallery_collections ORDER BY position, created_at, id"
        ).fetchall()
        members = conn.execute(
            """SELECT collection_id, image_id FROM gallery_collection_items
               ORDER BY collection_id, position IS NULL, position, added_at, image_id"""
        ).fetchall()
    by_collection: dict[str, list[str]] = {}
    for row in members:
        by_collection.setdefault(row['collection_id'], []).append(row['image_id'])
    return [dict(row) | {'is_default': bool(row['is_default']), 'image_ids': by_collection.get(row['id'], [])} for row in collections]


def restore_gallery_collections(collections: list[dict[str, Any]], image_ids: dict[str, str]) -> None:
    """Restore all collection metadata together, without replacing existing collections."""
    _ensure_database()
    now = utc_now()
    with _connect() as conn:
        with _transaction(conn):
            position = int(conn.execute('SELECT COALESCE(MAX(position), -1) + 1 FROM gallery_collections').fetchone()[0])
            existing = conn.execute('SELECT name, asset_alias FROM gallery_collections').fetchall()
            names = {row['name'].casefold() for row in existing}
            aliases = {row['asset_alias'].casefold() for row in existing if row['asset_alias']}
            has_default = bool(conn.execute('SELECT 1 FROM gallery_collections WHERE is_default = 1 LIMIT 1').fetchone())
            for collection in collections:
                original_name = collection['name']
                name = original_name
                suffix_number = 1
                while name.casefold() in names:
                    suffix = f' ({suffix_number})'
                    name = original_name[:60-len(suffix)].rstrip() + suffix
                    suffix_number += 1
                original_alias = collection['asset_alias']
                alias = original_alias
                suffix_number = 1
                while alias and alias.casefold() in aliases:
                    suffix = f'_{suffix_number}'
                    alias = original_alias[:32-len(suffix)] + suffix
                    suffix_number += 1
                names.add(name.casefold())
                if alias:
                    aliases.add(alias.casefold())
                collection_id = uuid.uuid4().hex
                is_default = bool(collection.get('is_default')) and not has_default
                has_default = has_default or is_default
                conn.execute('INSERT INTO gallery_collections (id, name, asset_alias, position, is_default, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)',
                             (collection_id, name, alias, position, int(is_default), now, now))
                position += 1
                seen = set()
                for old_id in collection['image_ids']:
                    image_id = image_ids.get(old_id)
                    if image_id is not None and image_id not in seen:
                        seen.add(image_id)
                        conn.execute('INSERT INTO gallery_collection_items (collection_id, image_id, added_at, position) VALUES (?, ?, ?, ?)',
                                     (collection_id, image_id, now, len(seen) - 1))
            if collections:
                _invalidate_gallery_query_caches_on_conn(conn)
