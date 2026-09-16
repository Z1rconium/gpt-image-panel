"""SQLite connection lifecycle, transactions, storage checks, and the SQL
helpers shared across the persistence layer."""

import logging
import os
import sqlite3
import time
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Any, Iterable, Iterator, Sequence

from ...core import settings as config
from ...core.observability import metrics, observe_job_stage
from . import state as db_state
from .state import (
    _add_verified_thumbnail,
    _remove_verified_thumbnail,
    _clear_verified_thumbnails,
    _invalidate_filter_options_cache,
    _bump_filter_options_cache_version,
    _get_filter_options_cache_version,
    _invalidate_gallery_total_bytes_cache,
    _invalidate_gallery_count_cache,
)
from .constants import (
    AI_ASSISTANT_SETTINGS_KEY,
    DATA_DIR_MODE,
    DATA_FILE_MODE,
    DATA_PERMISSION_CHECK_INTERVAL_SECONDS,
    EditSourceQueueFullError,
    GALLERY_COLUMNS,
    GALLERY_COUNT_CACHE_SECONDS,
    GALLERY_FTS_MIN_QUERY_LENGTH,
    GALLERY_FTS_VERSION,
    GALLERY_FTS_VERSION_KEY,
    GALLERY_IMPORT_BATCH_SIZE,
    GALLERY_JOB_COLUMNS,
    GALLERY_ORPHAN_FILE_TTL_SECONDS,
    GALLERY_ORPHAN_GC_BATCH_SIZE,
    GALLERY_PAGE_ANCHOR_INTERVAL_PAGES,
    GALLERY_PAGE_ANCHOR_INVALIDATING_UPDATE_FIELDS,
    GALLERY_PAGE_ANCHOR_MAX_PER_QUERY,
    GALLERY_PAGE_ANCHOR_SMALL_OFFSET_THRESHOLD,
    GALLERY_SYNC_BATCH_SIZE,
    GALLERY_TOTAL_BYTES_CACHE_SECONDS,
    GENERATE_JOB_COLUMNS,
    IMAGE_JOB_UNIT_COLUMNS,
    INTEGER_GALLERY_COLUMNS,
    INTEGER_GENERATE_JOB_COLUMNS,
    ImageJobQueueFullError,
    MAX_PERSISTED_JOB_TEXT_CHARS,
    NODEIMAGE_SETTINGS_KEY,
    PROMPT_OPTIMIZER_SETTINGS_KEY,
    PROMPT_SNIPPET_COLUMNS,
    R2_BACKUP_SETTINGS_KEY,
    REQUIRED_GALLERY_COLUMNS,
    SETTINGS_ACTIVE_PRESET_KEY,
    SQLITE_IN_CLAUSE_CHUNK_SIZE,
    SQLITE_TIMEOUT_SECONDS,
    THUMBNAIL_CPU_SLOT_LEASE_SECONDS,
    THUMBNAIL_JOB_LEASE_SECONDS,
    THUMBNAIL_JOB_MAX_ATTEMPTS,
    UPSTREAM_SOCKS5_PROXY_KEY,
    WEBHOOK_URL_KEY,
    WORKER_METRIC_SNAPSHOT_TTL_SECONDS,
    _GALLERY_BYTES_CACHE_MAX_SIZE,
    _GALLERY_COUNT_CACHE_MAX_SIZE,
    _GALLERY_INTERNAL_COLUMNS,
)

logger = logging.getLogger(__name__)

_current_db_metric: ContextVar[str | None] = ContextVar(
    "current_db_metric",
    default=None,
)


def _unique_sqlite_values(values: Iterable[Any]) -> list[str]:
    unique: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalized = "" if value is None else str(value)
        if not normalized.strip() or normalized in seen:
            continue
        seen.add(normalized)
        unique.append(normalized)
    return unique


def _iter_sqlite_in_chunks(
    values: Iterable[Any],
    *,
    chunk_size: int | None = None,
) -> Iterator[list[str]]:
    unique_values = _unique_sqlite_values(values)
    normalized_chunk_size = max(1, int(chunk_size or SQLITE_IN_CLAUSE_CHUNK_SIZE))
    for start in range(0, len(unique_values), normalized_chunk_size):
        yield unique_values[start : start + normalized_chunk_size]


def _chmod_path(path: Path, mode: int) -> None:
    if os.name == "nt" or not path.exists():
        return
    try:
        os.chmod(path, mode)
    except OSError as e:
        logger.warning("Failed to chmod %s to %#o: %s", path, mode, e)


def _secure_data_storage_permissions(*, force: bool = False) -> None:
    if os.name == "nt":
        return

    now = time.monotonic()
    with db_state._permissions_check_lock:
        if (
            not force
            and now - db_state._last_permissions_check < DATA_PERMISSION_CHECK_INTERVAL_SECONDS
        ):
            return
        db_state._last_permissions_check = now

    data_dir = Path(config.DATA_DIR)
    database_path = Path(config.DATABASE_FILE)
    for directory in {data_dir, database_path.parent}:
        _chmod_path(directory, DATA_DIR_MODE)
    for suffix in ("", "-wal", "-shm"):
        _chmod_path(Path(f"{database_path}{suffix}"), DATA_FILE_MODE)


def _get_gallery_version_on_conn(conn: sqlite3.Connection) -> int:
    row = conn.execute(
        "SELECT value FROM gallery_meta WHERE key = 'gallery_version'"
    ).fetchone()
    if row:
        return int(row["value"] or 0)
    started_transaction = not conn.in_transaction
    if started_transaction:
        conn.execute("BEGIN IMMEDIATE")
    conn.execute(
        """
        INSERT OR IGNORE INTO gallery_meta (key, value)
        VALUES ('gallery_version', 0)
        """
    )
    if started_transaction:
        conn.commit()
    return 0


def _bump_gallery_version_on_conn(conn: sqlite3.Connection) -> int:
    conn.execute(
        """
        INSERT INTO gallery_meta (key, value)
        VALUES ('gallery_version', 1)
        ON CONFLICT(key) DO UPDATE SET value = value + 1
        """
    )
    row = conn.execute(
        "SELECT value FROM gallery_meta WHERE key = 'gallery_version'"
    ).fetchone()
    gallery_version = int(row["value"] or 0) if row else 0
    conn.execute(
        "DELETE FROM gallery_page_anchors WHERE gallery_version != ?",
        (gallery_version,),
    )
    return gallery_version


def _invalidate_gallery_query_caches():
    _invalidate_gallery_count_cache()
    _invalidate_gallery_total_bytes_cache()


def _invalidate_gallery_query_caches_on_conn(conn: sqlite3.Connection):
    _bump_gallery_version_on_conn(conn)
    _invalidate_gallery_query_caches()


def _ensure_directories():
    storage_paths = (
        Path(config.IMAGES_DIR).resolve(),
        Path(config.THUMBNAILS_DIR).resolve(),
        Path(config.DATA_DIR).resolve(),
        Path(config.DATABASE_FILE).resolve().parent,
    )
    if db_state._initialized_storage_paths == storage_paths:
        return
    for path in storage_paths:
        path.mkdir(parents=True, exist_ok=True)
    _secure_data_storage_permissions(force=True)
    db_state._initialized_storage_paths = storage_paths


def _check_directory_writable(path: Path):
    """Probe the directory with a file name unique to this caller.

    Every worker process verifies storage at startup, so a shared probe name
    would have them delete each other's file mid-check and fail a directory
    that is perfectly writable.
    """
    probe = path / f".write-test-{os.getpid()}-{uuid.uuid4().hex}"
    try:
        with open(probe, "wb") as f:
            f.write(b"ok")
    except OSError as e:
        uid = os.getuid()
        gid = os.getgid()
        absolute_path = path.resolve()
        raise PermissionError(
            f"Directory is not writable: {absolute_path} "
            f"(process uid={uid}, gid={gid}). Original error: {e}"
        ) from e
    finally:
        try:
            probe.unlink()
        except OSError:
            # Someone else removing our probe is not a writability problem.
            pass


def _open_connection(
    *,
    timeout: float = SQLITE_TIMEOUT_SECONDS,
    busy_timeout_ms: int = 30000,
) -> sqlite3.Connection:
    _ensure_directories()
    conn = sqlite3.connect(config.DATABASE_FILE, timeout=timeout)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute(f"PRAGMA busy_timeout = {int(busy_timeout_ms)}")
    conn.execute("PRAGMA synchronous = NORMAL")
    # Read-path tuning: a larger page cache and mmap window cut syscalls for the
    # keyset scans the gallery does, and keeping temp b-trees in memory avoids
    # disk spills for the filter-option rebuilds.
    conn.execute(f"PRAGMA cache_size = -{int(config.SQLITE_CACHE_SIZE_MB) * 1024}")
    conn.execute(f"PRAGMA temp_store = {config.SQLITE_TEMP_STORE}")
    conn.execute(f"PRAGMA mmap_size = {int(config.SQLITE_MMAP_SIZE_MB) * 1024 * 1024}")
    conn.execute(
        f"PRAGMA wal_autocheckpoint = {int(config.SQLITE_WAL_AUTOCHECKPOINT_PAGES)}"
    )
    return conn


@contextmanager
def _connect() -> Iterator[sqlite3.Connection]:
    conn = _get_thread_connection()
    depth = int(getattr(db_state._thread_local, "connection_depth", 0))
    db_state._thread_local.connection_depth = depth + 1
    try:
        yield conn
    except Exception:
        if depth == 0 and conn.in_transaction:
            conn.rollback()
        raise
    finally:
        db_state._thread_local.connection_depth = depth
        if depth == 0 and not bool(
            getattr(db_state._thread_local, "keep_connection_open", False)
        ):
            _close_thread_connection()


def _get_thread_connection() -> sqlite3.Connection:
    database_file = str(config.DATABASE_FILE)
    busy_timeout_ms = int(getattr(db_state._thread_local, "busy_timeout_ms", 30000))
    conn = getattr(db_state._thread_local, "conn", None)
    conn_database_file = getattr(db_state._thread_local, "database_file", None)
    conn_busy_timeout_ms = getattr(db_state._thread_local, "connection_busy_timeout_ms", None)
    if (
        conn is not None
        and conn_database_file == database_file
        and conn_busy_timeout_ms == busy_timeout_ms
    ):
        return conn

    _close_thread_connection()
    conn = _open_connection(
        timeout=max(0.01, busy_timeout_ms / 1000),
        busy_timeout_ms=busy_timeout_ms,
    )
    db_state._thread_local.conn = conn
    db_state._thread_local.database_file = database_file
    db_state._thread_local.connection_busy_timeout_ms = busy_timeout_ms
    db_state._thread_local.connection_depth = 0
    return conn


def _close_thread_connection():
    conn = getattr(db_state._thread_local, "conn", None)
    if conn is None:
        return
    try:
        if conn.in_transaction:
            conn.rollback()
        conn.close()
    finally:
        db_state._thread_local.conn = None
        db_state._thread_local.database_file = None
        db_state._thread_local.connection_busy_timeout_ms = None
        db_state._thread_local.connection_depth = 0


@contextmanager
def busy_timeout_scope(busy_timeout_ms: int) -> Iterator[None]:
    previous_busy_timeout = getattr(db_state._thread_local, "busy_timeout_ms", None)
    db_state._thread_local.busy_timeout_ms = max(1, int(busy_timeout_ms))
    try:
        yield
    finally:
        if previous_busy_timeout is None:
            try:
                delattr(db_state._thread_local, "busy_timeout_ms")
            except AttributeError:
                pass
        else:
            db_state._thread_local.busy_timeout_ms = previous_busy_timeout


@contextmanager
def persistent_connection_scope(busy_timeout_ms: int) -> Iterator[None]:
    previous_keep_open = bool(getattr(db_state._thread_local, "keep_connection_open", False))
    previous_busy_timeout = getattr(db_state._thread_local, "busy_timeout_ms", None)
    db_state._thread_local.keep_connection_open = True
    db_state._thread_local.busy_timeout_ms = max(1, int(busy_timeout_ms))
    try:
        yield
    finally:
        db_state._thread_local.keep_connection_open = previous_keep_open
        if previous_busy_timeout is None:
            try:
                delattr(db_state._thread_local, "busy_timeout_ms")
            except AttributeError:
                pass
        else:
            db_state._thread_local.busy_timeout_ms = previous_busy_timeout


def close_database_connections():
    """Close this thread's active SQLite connection and clear storage caches."""
    _close_thread_connection()
    _clear_verified_thumbnails()
    _invalidate_filter_options_cache()
    _invalidate_gallery_query_caches()


@contextmanager
def metric_name_scope(name: str | None) -> Iterator[None]:
    """Tag repository work on this thread for slow-transaction logging."""
    token = _current_db_metric.set(name)
    try:
        yield
    finally:
        _current_db_metric.reset(token)


def current_db_metric() -> str | None:
    return _current_db_metric.get()


@contextmanager
def _transaction(conn: sqlite3.Connection) -> Iterator[None]:
    begin_started_at = time.perf_counter()
    try:
        conn.execute("BEGIN IMMEDIATE")
    except sqlite3.OperationalError as e:
        metrics.observe_ms(
            "sqlite.write_lock_wait_ms",
            (time.perf_counter() - begin_started_at) * 1000,
        )
        message = str(e).lower()
        if "locked" in message or "busy" in message:
            metrics.increment("sqlite.busy")
        raise
    metrics.observe_ms(
        "sqlite.write_lock_wait_ms",
        (time.perf_counter() - begin_started_at) * 1000,
    )
    label = _current_db_metric.get()

    def _observe_hold() -> float:
        hold_ms = (time.perf_counter() - begin_started_at) * 1000
        metrics.observe_ms("sqlite.write_txn_hold_ms", hold_ms)
        if hold_ms >= config.SQLITE_SLOW_TXN_WARN_MS:
            logger.warning(
                "Slow SQLite write transaction: held %.1fms (label=%s)",
                hold_ms,
                label or "-",
            )
        return hold_ms

    try:
        yield
    except Exception as e:
        if isinstance(e, sqlite3.OperationalError):
            message = str(e).lower()
            if "locked" in message or "busy" in message:
                metrics.increment("sqlite.busy")
        conn.rollback()
        _observe_hold()
        raise
    else:
        conn.commit()
        metrics.increment("sqlite.write_txn")
        _observe_hold()


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return {row["name"] for row in rows}


def _get_setting_value(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute(
        "SELECT value FROM settings_kv WHERE key = ?",
        (key,),
    ).fetchone()
    return row["value"] if row else None


def _set_setting_value(conn: sqlite3.Connection, key: str, value: str):
    conn.execute(
        """
        INSERT INTO settings_kv (key, value)
        VALUES (?, ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """,
        (key, value),
    )
