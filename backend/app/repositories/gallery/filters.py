"""Gallery filter option maintenance and reads."""

from ...schemas.gallery import GalleryFilterOptions
from ..db import (
    _GalleryFilterOptionsCacheEntry,
    _connect,
    _ensure_database,
    _get_filter_options_cache_version,
)
import sqlite3
from ..db import state as db_state


def _get_gallery_filter_options_on_conn(conn: sqlite3.Connection) -> GalleryFilterOptions:
    cache_version = _get_filter_options_cache_version()
    with db_state._filter_options_cache_lock:
        cached = db_state._filter_options_cache
        if cached is not None and cached.version == cache_version:
            return cached.options

    options: dict[str, list[str]] = {}
    for key, kind in (
        ("models", "model"),
        ("presets", "preset"),
        ("sizes", "size"),
    ):
        rows = conn.execute(
            """
            SELECT value
            FROM gallery_filter_options
            WHERE kind = ? AND ref_count > 0
            ORDER BY LOWER(value) ASC
            """,
            (kind,),
        ).fetchall()
        options[key] = [row["value"] for row in rows if row["value"]]

    result = GalleryFilterOptions(**options)
    with db_state._filter_options_cache_lock:
        db_state._filter_options_cache = _GalleryFilterOptionsCacheEntry(
            version=cache_version,
            options=result,
        )
    return result


def get_gallery_filter_options() -> GalleryFilterOptions:
    _ensure_database()
    with _connect() as conn:
        return _get_gallery_filter_options_on_conn(conn)
