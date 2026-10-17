"""Gallery maintenance: byte backfill, file reconciliation, orphan GC, caches.

Kept separate from ``mutations.py`` (which owns row CRUD) so file/byte
reconciliation and cache invalidation stay reviewable in one place.
"""

import sqlite3
import time
from pathlib import Path

from ...core import settings as config
from ...core.media import (
    IMAGE_FILE_EXTENSIONS,
    THUMBNAIL_EXTENSION,
    safe_image_path,
    safe_thumbnail_path,
)
from ...core.observability import metrics
from ..db import (
    GALLERY_ORPHAN_FILE_TTL_SECONDS,
    GALLERY_ORPHAN_GC_BATCH_SIZE,
    GALLERY_SYNC_BATCH_SIZE,
    _add_gallery_filter_option_deltas,
    _apply_gallery_filter_option_deltas_on_conn,
    _clear_verified_thumbnails,
    _connect,
    _ensure_database,
    _invalidate_gallery_query_caches_on_conn,
    _invalidate_gallery_total_bytes_cache,
    _remove_verified_thumbnail,
    _transaction,
    logger,
)
from ..db import state as db_state
from ..image_files import (
    delete_image_from_disk as _delete_image_unlocked,
    scan_image_files as _scan_image_files,
)
from ..thumbnails import (
    delete_thumbnail as _delete_thumbnail_unlocked,
    thumbnail_filename_for_image as _thumbnail_filename_for_image,
)
from .queries import _get_all_filenames_on_conn


def _stat_image_bytes(filename: str) -> int | None:
    path = safe_image_path(filename)
    if not path:
        return None
    try:
        return path.stat().st_size
    except OSError:
        return None


def _backfill_gallery_bytes_from_known_rows_on_conn(conn: sqlite3.Connection) -> int:
    before_changes = conn.total_changes
    with _transaction(conn):
        conn.execute(
            """
            UPDATE gallery_entries
            SET bytes = (
                SELECT MAX(known.bytes)
                FROM gallery_entries AS known
                WHERE known.filename = gallery_entries.filename
                  AND known.bytes IS NOT NULL
            )
            WHERE bytes IS NULL
              AND filename IS NOT NULL
              AND EXISTS (
                  SELECT 1
                  FROM gallery_entries AS known
                  WHERE known.filename = gallery_entries.filename
                    AND known.bytes IS NOT NULL
              )
            """
        )
    return conn.total_changes - before_changes


def _backfill_gallery_bytes_from_filenames() -> int:
    total_updated = 0
    batch_size = 200
    last_filename = ""
    while True:
        with _connect() as conn:
            rows = conn.execute(
                """
                SELECT filename
                FROM gallery_entries
                WHERE filename IS NOT NULL
                  AND TRIM(filename) != ''
                  AND bytes IS NULL
                  AND filename > ?
                GROUP BY filename
                ORDER BY filename ASC
                LIMIT ?
                """,
                (last_filename, batch_size),
            ).fetchall()
        if not rows:
            break

        backfills: list[tuple[int, str]] = []
        for row in rows:
            stored_filename = str(row["filename"] or "")
            filename = stored_filename.strip()
            if not filename:
                continue
            last_filename = stored_filename
            size = _stat_image_bytes(filename)
            if size is None:
                continue
            backfills.append((size, stored_filename))

        if backfills:
            with _connect() as conn:
                before_changes = conn.total_changes
                with _transaction(conn):
                    conn.executemany(
                        """
                        UPDATE gallery_entries
                        SET bytes = ?
                        WHERE filename = ? AND bytes IS NULL
                        """,
                        backfills,
                    )
                total_updated += conn.total_changes - before_changes

        if len(rows) < batch_size:
            break

    return total_updated


def backfill_missing_gallery_bytes() -> int:
    """Backfill missing gallery byte sizes outside the gallery request path."""
    _ensure_database()
    with _connect() as conn:
        updated = _backfill_gallery_bytes_from_known_rows_on_conn(conn)
    updated += _backfill_gallery_bytes_from_filenames()
    if updated:
        _invalidate_gallery_total_bytes_cache()
    return updated


def sync_gallery_with_image_files() -> int:
    _ensure_database()
    with db_state._storage_lock:
        image_filenames = _scan_image_files()

        # Read-only scan: collect stale rows without holding the write lock.
        # Each SELECT is its own implicit read transaction, so WAL readers never
        # block concurrent writers while a large library is scanned.
        stale_rows: list[sqlite3.Row] = []
        with _connect() as conn:
            last_id = ""
            while True:
                rows = conn.execute(
                    """
                    SELECT id, filename, model, api_preset_name, size
                    FROM gallery_entries
                    WHERE id > ?
                    ORDER BY id
                    LIMIT ?
                    """,
                    (last_id, GALLERY_SYNC_BATCH_SIZE),
                ).fetchall()
                if not rows:
                    break

                last_id = str(rows[-1]["id"])
                stale_rows.extend(
                    row
                    for row in rows
                    if row["filename"] and row["filename"] not in image_filenames
                )

        if not stale_rows:
            return 0

        # Delete in bounded write transactions so the write lock is held per
        # batch instead of for the whole scan. Each batch applies its own
        # filter-option deltas atomically; the gallery version bump (which
        # invalidates page/count caches) happens once in the final batch.
        removed_count = 0
        batch_size = max(1, int(GALLERY_SYNC_BATCH_SIZE))
        for start in range(0, len(stale_rows), batch_size):
            batch = stale_rows[start : start + batch_size]
            is_last_batch = start + batch_size >= len(stale_rows)
            filter_option_deltas: dict[tuple[str, str], int] = {}
            with _connect() as conn:
                with _transaction(conn):
                    conn.executemany(
                        "DELETE FROM gallery_entries WHERE id = ?",
                        [(row["id"],) for row in batch],
                    )
                    for row in batch:
                        _add_gallery_filter_option_deltas(
                            filter_option_deltas,
                            row,
                            -1,
                        )
                    _apply_gallery_filter_option_deltas_on_conn(
                        conn,
                        filter_option_deltas,
                    )
                    if is_last_batch:
                        _invalidate_gallery_query_caches_on_conn(conn)
            removed_count += len(batch)

        _clear_verified_thumbnails()
        return removed_count


def _is_gallery_filename_referenced_on_conn(
    conn: sqlite3.Connection,
    filename: str,
) -> bool:
    row = conn.execute(
        "SELECT 1 FROM gallery_entries WHERE filename = ? LIMIT 1",
        (filename,),
    ).fetchone()
    return row is not None


def is_gallery_filename_referenced(filename: str) -> bool:
    _ensure_database()
    normalized = str(filename or "").strip()
    if not normalized or not safe_image_path(normalized):
        return False
    with _connect() as conn:
        return _is_gallery_filename_referenced_on_conn(conn, normalized)


def _delete_gallery_file_if_unreferenced(filename: str) -> bool:
    with db_state._storage_lock:
        with _connect() as conn:
            if _is_gallery_filename_referenced_on_conn(conn, filename):
                return False

        deleted = False
        try:
            deleted = _delete_image_unlocked(filename)
        except OSError as e:
            metrics.increment("gallery.orphan_cleanup_pending")
            logger.warning("Failed to delete gallery image file %s: %s", filename, e)

        try:
            _delete_thumbnail_unlocked(filename)
            thumbnail_filename = _thumbnail_filename_for_image(filename)
            if thumbnail_filename:
                _remove_verified_thumbnail(thumbnail_filename)
        except OSError as e:
            metrics.increment("gallery.orphan_cleanup_pending")
            logger.warning("Failed to delete gallery thumbnail for %s: %s", filename, e)

        return deleted


def _file_older_than(path: Path, cutoff_epoch_seconds: float) -> bool:
    try:
        return path.stat().st_mtime < cutoff_epoch_seconds
    except OSError:
        return False


def cleanup_orphan_gallery_files(
    *,
    ttl_seconds: int = GALLERY_ORPHAN_FILE_TTL_SECONDS,
    batch_size: int = GALLERY_ORPHAN_GC_BATCH_SIZE,
) -> dict[str, int]:
    """Delete unreferenced image and thumbnail files after a short TTL."""
    _ensure_database()
    cutoff = time.time() - max(0, int(ttl_seconds))
    limit = max(1, int(batch_size))
    removed_images = 0
    removed_thumbnails = 0
    failed = 0
    scanned = 0

    with db_state._storage_lock:
        with _connect() as conn:
            referenced_filenames = set(_get_all_filenames_on_conn(conn))
        referenced_thumbnails = {
            thumbnail
            for filename in referenced_filenames
            if (thumbnail := _thumbnail_filename_for_image(filename))
        }

        images_dir = Path(config.IMAGES_DIR)
        if images_dir.exists():
            for path in images_dir.iterdir():
                if scanned >= limit:
                    break
                if not path.is_file() or path.suffix.lower() not in IMAGE_FILE_EXTENSIONS:
                    continue
                scanned += 1
                filename = path.name
                if filename in referenced_filenames or not _file_older_than(path, cutoff):
                    continue
                try:
                    if _delete_image_unlocked(filename):
                        removed_images += 1
                except OSError as e:
                    failed += 1
                    metrics.increment("gallery.orphan_cleanup_pending")
                    logger.warning("Failed to GC orphan gallery image %s: %s", filename, e)

        thumbnails_dir = Path(config.THUMBNAILS_DIR)
        same_dir_as_images = False
        try:
            same_dir_as_images = thumbnails_dir.resolve() == images_dir.resolve()
        except OSError:
            same_dir_as_images = False

        if thumbnails_dir.exists() and scanned < limit:
            protected_thumbnail_names = set(referenced_thumbnails)
            if same_dir_as_images:
                protected_thumbnail_names.update(referenced_filenames)
            for path in thumbnails_dir.iterdir():
                if scanned >= limit:
                    break
                if not path.is_file() or path.suffix.lower() != THUMBNAIL_EXTENSION:
                    continue
                scanned += 1
                thumbnail_filename = path.name
                if (
                    thumbnail_filename in protected_thumbnail_names
                    or not safe_thumbnail_path(thumbnail_filename)
                    or not _file_older_than(path, cutoff)
                ):
                    continue
                try:
                    path.unlink()
                    _remove_verified_thumbnail(thumbnail_filename)
                    removed_thumbnails += 1
                except OSError as e:
                    failed += 1
                    metrics.increment("gallery.orphan_cleanup_pending")
                    logger.warning("Failed to GC orphan gallery thumbnail %s: %s", thumbnail_filename, e)

    if removed_images or removed_thumbnails or failed:
        logger.info(
            "Gallery file GC scanned=%d removed_images=%d removed_thumbnails=%d failed=%d",
            scanned,
            removed_images,
            removed_thumbnails,
            failed,
        )
    return {
        "scanned": scanned,
        "removed_images": removed_images,
        "removed_thumbnails": removed_thumbnails,
        "failed": failed,
    }


def invalidate_thumbnail_cache(thumbnail_filename: str) -> None:
    """Drop a filename from the in-memory verified-thumbnail cache.

    Called by the routing layer when it detects the on-disk thumbnail is missing.
    """
    _remove_verified_thumbnail(thumbnail_filename)


__all__ = [name for name in globals() if not name.startswith("__")]
