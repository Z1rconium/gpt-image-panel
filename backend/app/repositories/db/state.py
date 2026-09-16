"""Process-local mutable state for the persistence layer.

Every connection, cache, and flag that outlives a single call lives here and is
addressed as ``db_state.<name>`` by the rest of the package, so the submodules
can never end up holding private copies that drift apart.

Nothing in this module is shared between worker processes: cross-process
coordination goes through SQLite leases and version rows instead.
"""

import threading
from collections import OrderedDict
from pathlib import Path
from typing import Any

from .constants import DATA_PERMISSION_CHECK_INTERVAL_SECONDS


_initialized_database_file: Path | None = None
_resolved_database_file_raw: str = ""
_resolved_database_file_path: Path | None = None
_db_init_lock = threading.RLock()
# These locks only serialize file operations inside one Python process. Cross-process
# gallery writes/deletes are deliberately coordinated by SQLite row leases plus
# UUID-derived filenames, atomic Path.replace(), and orphan-file GC TTL cleanup.
_storage_lock = threading.RLock()
_gallery_file_write_lock = threading.RLock()
_thread_local = threading.local()
_initialized_storage_paths: tuple[Path, Path, Path, Path] | None = None
_last_permissions_check = -DATA_PERMISSION_CHECK_INTERVAL_SECONDS
_permissions_check_lock = threading.RLock()

_filter_options_cache: "_GalleryFilterOptionsCacheEntry | None" = None
_filter_options_cache_lock = threading.RLock()
_filter_options_cache_version: int = 0
_gallery_total_bytes_cache: OrderedDict[
    tuple[str, str, tuple[Any, ...]],
    tuple[float, int],
] = OrderedDict()
_gallery_total_bytes_cache_lock = threading.RLock()
_gallery_count_cache: OrderedDict[
    tuple[str, str, tuple[Any, ...]],
    tuple[float, int],
] = OrderedDict()
_gallery_count_cache_lock = threading.RLock()
_gallery_fts_available: bool | None = None

_verified_thumbnails: set[str] = set()
_verified_thumbnails_lock = threading.RLock()

_last_optimize_at: float = 0.0
_optimize_lock = threading.RLock()


def _add_verified_thumbnail(filename: str):
    with _verified_thumbnails_lock:
        _verified_thumbnails.add(filename)


def _remove_verified_thumbnail(filename: str):
    with _verified_thumbnails_lock:
        _verified_thumbnails.discard(filename)


def _clear_verified_thumbnails():
    with _verified_thumbnails_lock:
        _verified_thumbnails.clear()


def _invalidate_filter_options_cache():
    global _filter_options_cache, _filter_options_cache_version
    with _filter_options_cache_lock:
        _filter_options_cache = None
        _filter_options_cache_version += 1


def _bump_filter_options_cache_version():
    global _filter_options_cache_version
    with _filter_options_cache_lock:
        _filter_options_cache_version += 1


def _get_filter_options_cache_version() -> int:
    with _filter_options_cache_lock:
        return _filter_options_cache_version


def _invalidate_gallery_total_bytes_cache():
    with _gallery_total_bytes_cache_lock:
        _gallery_total_bytes_cache.clear()


def _invalidate_gallery_count_cache():
    with _gallery_count_cache_lock:
        _gallery_count_cache.clear()
