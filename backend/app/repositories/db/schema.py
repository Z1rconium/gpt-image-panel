"""Storage initialisation, schema creation, and forward-only migrations.
Migrations are additive: new columns are nullable and existing rows are
never rebuilt."""

import logging
import sqlite3
import time
from pathlib import Path

from ...core import settings as config
from ...core.api_paths import default_model_for_api_path, normalize_api_preset
from ...core.utils import utc_now
from . import state as db_state
from .connection import (
    _check_directory_writable,
    _connect,
    _ensure_directories,
    _get_setting_value,
    _invalidate_gallery_query_caches,
    _secure_data_storage_permissions,
    _set_setting_value,
    _table_columns,
)
from .rows import (
    _rebuild_gallery_filter_options_on_conn,
)

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


def verify_storage_writable():
    _ensure_directories()
    _check_directory_writable(Path(config.IMAGES_DIR))
    _check_directory_writable(Path(config.THUMBNAILS_DIR))
    _check_directory_writable(Path(config.MASKS_DIR))
    _check_directory_writable(Path(config.DATA_DIR))
    _ensure_database()


def optimize_database_if_due() -> bool:
    """Run ``PRAGMA optimize`` on this thread's connection at most every
    ``SQLITE_OPTIMIZE_INTERVAL_SECONDS``.

    SQLite recommends running it periodically rather than only at startup, so
    the planner keeps statistics for the query shapes the app actually runs.
    Returns True when the pragma was executed.
    """
    interval = float(config.SQLITE_OPTIMIZE_INTERVAL_SECONDS)
    now = time.monotonic()
    with db_state._optimize_lock:
        if now - db_state._last_optimize_at < interval:
            return False
        db_state._last_optimize_at = now
    _ensure_database()
    with _connect() as conn:
        conn.execute("PRAGMA optimize")
    return True


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE name = ? LIMIT 1",
        (table,),
    ).fetchone()
    return row is not None


def _reset_gallery_fts_on_conn(conn: sqlite3.Connection):
    conn.executescript(
        """
        DROP TRIGGER IF EXISTS gallery_entries_fts_ai;
        DROP TRIGGER IF EXISTS gallery_entries_fts_ad;
        DROP TRIGGER IF EXISTS gallery_entries_fts_au;
        DROP TABLE IF EXISTS gallery_entries_fts;
        """
    )


def _ensure_gallery_fts(conn: sqlite3.Connection):

    fts_exists = _table_exists(conn, "gallery_entries_fts")
    needs_rebuild = (
        not fts_exists
        or _get_setting_value(conn, GALLERY_FTS_VERSION_KEY) != GALLERY_FTS_VERSION
    )

    try:
        if needs_rebuild:
            _reset_gallery_fts_on_conn(conn)

        conn.execute(
            """
            CREATE VIRTUAL TABLE IF NOT EXISTS gallery_entries_fts
            USING fts5(
                prompt,
                content='gallery_entries',
                content_rowid='rowid',
                tokenize='trigram'
            )
            """
        )
        conn.executescript(
            """
            CREATE TRIGGER IF NOT EXISTS gallery_entries_fts_ai
            AFTER INSERT ON gallery_entries BEGIN
                INSERT INTO gallery_entries_fts(rowid, prompt)
                VALUES (new.rowid, new.prompt);
            END;

            CREATE TRIGGER IF NOT EXISTS gallery_entries_fts_ad
            AFTER DELETE ON gallery_entries BEGIN
                INSERT INTO gallery_entries_fts(gallery_entries_fts, rowid, prompt)
                VALUES ('delete', old.rowid, old.prompt);
            END;

            CREATE TRIGGER IF NOT EXISTS gallery_entries_fts_au
            AFTER UPDATE OF prompt ON gallery_entries BEGIN
                INSERT INTO gallery_entries_fts(gallery_entries_fts, rowid, prompt)
                VALUES ('delete', old.rowid, old.prompt);
                INSERT INTO gallery_entries_fts(rowid, prompt)
                VALUES (new.rowid, new.prompt);
            END;
            """
        )
        if needs_rebuild:
            conn.execute("INSERT INTO gallery_entries_fts(gallery_entries_fts) VALUES ('rebuild')")
            _set_setting_value(conn, GALLERY_FTS_VERSION_KEY, GALLERY_FTS_VERSION)
        db_state._gallery_fts_available = True
    except sqlite3.OperationalError as e:
        db_state._gallery_fts_available = False
        logger.warning("SQLite FTS5 prompt search unavailable; falling back to LIKE: %s", e)


def _resolved_database_file() -> Path:
    """Resolve the configured database path once per raw config value.

    ``_ensure_database`` runs at the top of nearly every repository call, so
    repeating ``Path.resolve()`` (realpath syscalls) on each one is pure
    overhead. Keying the cache on the raw setting keeps a swapped
    ``config.DATABASE_FILE`` (as tests do) from being served a stale path.
    """
    raw = str(config.DATABASE_FILE)
    if db_state._resolved_database_file_path is None or db_state._resolved_database_file_raw != raw:
        db_state._resolved_database_file_path = Path(raw).resolve()
        db_state._resolved_database_file_raw = raw
    return db_state._resolved_database_file_path


def _ensure_database():
    database_file = _resolved_database_file()
    if db_state._initialized_database_file == database_file and database_file.exists():
        return

    with db_state._db_init_lock:
        database_file = _resolved_database_file()
        if db_state._initialized_database_file == database_file and database_file.exists():
            return
        if db_state._initialized_database_file != database_file:
            db_state._gallery_fts_available = None
            _invalidate_gallery_query_caches()
            _clear_verified_thumbnails()

        with _connect() as conn:
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute("PRAGMA synchronous = NORMAL")
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS settings_kv (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version INTEGER PRIMARY KEY,
                    name TEXT NOT NULL,
                    applied_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS api_presets (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    api_url TEXT NOT NULL,
                    api_key TEXT NOT NULL,
                    api_path TEXT NOT NULL,
                    default_model TEXT NOT NULL,
                    default_response_format TEXT NOT NULL DEFAULT 'url',
                    supports_mask INTEGER NOT NULL DEFAULT 1,
                    position INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS gallery_entries (
                    id TEXT PRIMARY KEY,
                    prompt TEXT NOT NULL,
                    size TEXT NOT NULL,
                    filename TEXT NOT NULL,
                    thumbnail_filename TEXT,
                    created_at TEXT NOT NULL,
                    completed_at TEXT,
                    image_width INTEGER,
                    image_height INTEGER,
                    model TEXT,
                    quality TEXT,
                    output_format TEXT,
                    output_compression INTEGER,
                    background TEXT,
                    response_format TEXT,
                    n INTEGER,
                    completed_count INTEGER,
                    success_count INTEGER,
                    failure_count INTEGER,
                    api_path TEXT,
                    api_preset_name TEXT,
                    duration TEXT,
                    favorite INTEGER NOT NULL DEFAULT 0,
                    bytes INTEGER,
                    sha256 TEXT,
                    sort_seq INTEGER,
                    mask_coverage REAL
                );

                CREATE INDEX IF NOT EXISTS idx_gallery_entries_filename
                    ON gallery_entries(filename);

                CREATE TABLE IF NOT EXISTS gallery_filter_options (
                    kind TEXT NOT NULL,
                    value TEXT NOT NULL,
                    ref_count INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(kind, value)
                );

                CREATE TABLE IF NOT EXISTS gallery_meta (
                    key TEXT PRIMARY KEY,
                    value INTEGER NOT NULL
                );

                CREATE TABLE IF NOT EXISTS gallery_page_anchors (
                    query_key TEXT NOT NULL,
                    page_size INTEGER NOT NULL,
                    page INTEGER NOT NULL,
                    sort_seq INTEGER NOT NULL,
                    image_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    gallery_version INTEGER NOT NULL,
                    PRIMARY KEY(query_key, page_size, page)
                );

                CREATE INDEX IF NOT EXISTS idx_gallery_page_anchors_lookup
                    ON gallery_page_anchors(query_key, page_size, gallery_version, page);

                CREATE TABLE IF NOT EXISTS thumbnail_jobs (
                    filename TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    lease_owner TEXT,
                    lease_expires_at TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    error TEXT
                );

                CREATE INDEX IF NOT EXISTS idx_thumbnail_jobs_claim
                    ON thumbnail_jobs(status, lease_expires_at, created_at);

                CREATE TABLE IF NOT EXISTS r2_sync_state (
                    filename TEXT PRIMARY KEY,
                    sha256 TEXT,
                    bytes INTEGER NOT NULL DEFAULT 0,
                    key TEXT NOT NULL,
                    etag TEXT,
                    last_remote_seen_at TEXT,
                    synced_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_r2_sync_state_key
                    ON r2_sync_state(key);

                CREATE TABLE IF NOT EXISTS generate_jobs (
                    job_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    stage TEXT,
                    message TEXT,
                    operation TEXT,
                    prompt TEXT,
                    size TEXT,
                    created_at TEXT NOT NULL,
                    started_at TEXT,
                    completed_at TEXT,
                    updated_at TEXT NOT NULL,
                    model TEXT,
                    quality TEXT,
                    output_format TEXT,
                    output_compression INTEGER,
                    background TEXT,
                    response_format TEXT,
                    n INTEGER,
                    api_path TEXT,
                    api_preset_name TEXT,
                    duration TEXT,
                    stage_timings_json TEXT,
                    image_id TEXT,
                    image_url TEXT,
                    images_json TEXT,
                    image_width INTEGER,
                    image_height INTEGER,
                    error TEXT,
                    webhook_url TEXT
                );

                CREATE INDEX IF NOT EXISTS idx_generate_jobs_status_updated_at
                    ON generate_jobs(status, updated_at DESC);
                CREATE INDEX IF NOT EXISTS idx_generate_jobs_updated_at
                    ON generate_jobs(updated_at DESC);
                CREATE INDEX IF NOT EXISTS idx_generate_jobs_seek
                    ON generate_jobs(updated_at DESC, job_id DESC);

                CREATE TABLE IF NOT EXISTS image_job_units (
                    unit_id TEXT PRIMARY KEY,
                    parent_job_id TEXT NOT NULL,
                    operation TEXT NOT NULL,
                    unit_index INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    claimed_by TEXT,
                    claim_expires_at TEXT,
                    stage TEXT,
                    message TEXT,
                    error TEXT,
                    result_json TEXT,
                    stage_timings_json TEXT,
                    duration TEXT,
                    created_at TEXT NOT NULL,
                    started_at TEXT,
                    completed_at TEXT,
                    updated_at TEXT NOT NULL,
                    request_json TEXT NOT NULL,
                    edit_sources_json TEXT,
                    api_preset_id TEXT,
                    api_preset_name TEXT,
                    api_path TEXT,
                    claim_token TEXT,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    UNIQUE(parent_job_id, unit_index)
                );

                DROP INDEX IF EXISTS idx_image_job_units_claim;
                CREATE INDEX IF NOT EXISTS idx_image_job_units_claim_queued
                    ON image_job_units(created_at, unit_index)
                    WHERE status = 'queued';
                CREATE INDEX IF NOT EXISTS idx_image_job_units_claim_running_expired
                    ON image_job_units(claim_expires_at, created_at, unit_index)
                    WHERE status = 'running' AND claim_expires_at IS NOT NULL;
                DROP INDEX IF EXISTS idx_image_job_units_running_count;
                CREATE INDEX IF NOT EXISTS idx_image_job_units_running_count
                    ON image_job_units(claim_expires_at)
                    WHERE status = 'running';
                CREATE INDEX IF NOT EXISTS idx_image_job_units_parent
                    ON image_job_units(parent_job_id, unit_index);
                CREATE INDEX IF NOT EXISTS idx_image_job_units_worker
                    ON image_job_units(claimed_by, status);

                CREATE TABLE IF NOT EXISTS gallery_jobs (
                    job_id TEXT PRIMARY KEY,
                    kind TEXT NOT NULL,
                    status TEXT NOT NULL,
                    stage TEXT,
                    message TEXT,
                    progress INTEGER NOT NULL DEFAULT 0,
                    filename TEXT,
                    download_url TEXT,
                    path TEXT,
                    requested_count INTEGER NOT NULL DEFAULT 0,
                    processed_count INTEGER NOT NULL DEFAULT 0,
                    exported_count INTEGER NOT NULL DEFAULT 0,
                    missing_count INTEGER NOT NULL DEFAULT 0,
                    total_count INTEGER NOT NULL DEFAULT 0,
                    compared_count INTEGER NOT NULL DEFAULT 0,
                    uploaded_count INTEGER NOT NULL DEFAULT 0,
                    pending_upload_count INTEGER NOT NULL DEFAULT 0,
                    skipped_existing_count INTEGER NOT NULL DEFAULT 0,
                    missing_local_count INTEGER NOT NULL DEFAULT 0,
                    failed_count INTEGER NOT NULL DEFAULT 0,
                    bytes_total INTEGER NOT NULL DEFAULT 0,
                    bytes_written INTEGER NOT NULL DEFAULT 0,
                    bytes_uploaded INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    started_at TEXT,
                    completed_at TEXT,
                    updated_at TEXT NOT NULL,
                    error TEXT,
                    lease_owner TEXT,
                    lease_expires_at TEXT,
                    payload_json TEXT
                );

                CREATE INDEX IF NOT EXISTS idx_gallery_jobs_claim
                    ON gallery_jobs(kind, status, lease_expires_at, created_at);
                CREATE INDEX IF NOT EXISTS idx_gallery_jobs_active_count
                    ON gallery_jobs(kind, status, lease_expires_at);
                CREATE INDEX IF NOT EXISTS idx_gallery_jobs_terminal_gc
                    ON gallery_jobs(kind, status, updated_at);
                CREATE INDEX IF NOT EXISTS idx_gallery_jobs_kind_updated
                    ON gallery_jobs(kind, updated_at DESC);

                CREATE TABLE IF NOT EXISTS worker_heartbeats (
                    worker_id TEXT PRIMARY KEY,
                    last_seen_at TEXT NOT NULL,
                    active_units INTEGER NOT NULL DEFAULT 0
                );

                CREATE TABLE IF NOT EXISTS sse_slots (
                    connection_id TEXT PRIMARY KEY,
                    client_ip TEXT NOT NULL,
                    acquired_at TEXT NOT NULL,
                    lease_expires_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_sse_slots_lease
                    ON sse_slots(lease_expires_at);
                CREATE INDEX IF NOT EXISTS idx_sse_slots_client
                    ON sse_slots(client_ip, lease_expires_at);

                CREATE TABLE IF NOT EXISTS background_leases (
                    name TEXT PRIMARY KEY,
                    owner TEXT NOT NULL,
                    lease_expires_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    completed_at TEXT
                );

                CREATE INDEX IF NOT EXISTS idx_background_leases_expires
                    ON background_leases(lease_expires_at);

                CREATE TABLE IF NOT EXISTS access_failures (
                    client_ip TEXT PRIMARY KEY,
                    failure_count INTEGER NOT NULL DEFAULT 0,
                    first_failed_at REAL NOT NULL,
                    last_failed_at REAL NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_access_failures_last_failed_at
                    ON access_failures(last_failed_at);

                CREATE TABLE IF NOT EXISTS worker_metric_snapshots (
                    worker_id TEXT PRIMARY KEY,
                    snapshot_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_worker_metric_snapshots_updated_at
                    ON worker_metric_snapshots(updated_at DESC);

                CREATE TABLE IF NOT EXISTS edit_source_reservations (
                    job_id TEXT PRIMARY KEY,
                    byte_count INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS import_upload_reservations (
                    reservation_id TEXT PRIMARY KEY,
                    client_ip TEXT NOT NULL,
                    byte_count INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    lease_expires_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_import_upload_reservations_expires
                    ON import_upload_reservations(lease_expires_at);
                CREATE INDEX IF NOT EXISTS idx_import_upload_reservations_client
                    ON import_upload_reservations(client_ip, created_at);

                CREATE TABLE IF NOT EXISTS prompt_snippets (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    prompt TEXT NOT NULL,
                    favorite INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS overall_config_values (
                    name TEXT PRIMARY KEY,
                    env_value TEXT NOT NULL DEFAULT '',
                    override_value TEXT,
                    is_env_set INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL,
                    override_updated_at TEXT
                );

                CREATE TABLE IF NOT EXISTS gallery_ai_metadata (
                    image_id TEXT PRIMARY KEY,
                    description TEXT NOT NULL DEFAULT '',
                    prompt TEXT NOT NULL DEFAULT '',
                    analysis_json TEXT NOT NULL DEFAULT '{}',
                    model TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(image_id) REFERENCES gallery_entries(id) ON DELETE CASCADE
                );

                """
            )
            _run_schema_migrations(conn)
            _ensure_gallery_fts(conn)
            conn.commit()
            conn.execute("PRAGMA optimize")

        db_state._initialized_database_file = database_file
        _secure_data_storage_permissions(force=True)


def _ensure_gallery_sort_seq_column(conn: sqlite3.Connection):
    columns = _table_columns(conn, "gallery_entries")
    if "sort_seq" not in columns:
        conn.execute("ALTER TABLE gallery_entries ADD COLUMN sort_seq INTEGER")
        conn.execute("DROP INDEX IF EXISTS idx_gallery_entries_created_at")
        conn.execute("DROP INDEX IF EXISTS idx_gallery_entries_model_created_at")
        conn.execute("DROP INDEX IF EXISTS idx_gallery_entries_preset_created_at")
        conn.execute("DROP INDEX IF EXISTS idx_gallery_entries_size_created_at")
        conn.execute("DROP INDEX IF EXISTS idx_gallery_entries_favorite_created_at")
    conn.execute(
        """
        UPDATE gallery_entries
        SET sort_seq = rowid
        WHERE sort_seq IS NULL
        """
    )


def _ensure_gallery_keyset_indexes(conn: sqlite3.Connection):
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_gallery_entries_sort_seq
            ON gallery_entries(sort_seq DESC)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_gallery_entries_sort_seq_id
            ON gallery_entries(sort_seq DESC, id DESC)
        """
    )


def _migration_baseline_legacy_schema(conn: sqlite3.Connection):
    # v1 marks databases that already passed the historical inline schema setup.
    return


def _migration_gallery_filter_options(conn: sqlite3.Connection):
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS gallery_filter_options (
            kind TEXT NOT NULL,
            value TEXT NOT NULL,
            ref_count INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL,
            PRIMARY KEY(kind, value)
        )
        """
    )
    _rebuild_gallery_filter_options_on_conn(conn)


def _migration_thumbnail_jobs(conn: sqlite3.Connection):
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS thumbnail_jobs (
            filename TEXT PRIMARY KEY,
            status TEXT NOT NULL,
            attempts INTEGER NOT NULL DEFAULT 0,
            lease_owner TEXT,
            lease_expires_at TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            error TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_thumbnail_jobs_claim
            ON thumbnail_jobs(status, lease_expires_at, created_at)
        """
    )


def _migration_gallery_keyset_index(conn: sqlite3.Connection):
    _ensure_gallery_sort_seq_column(conn)
    _ensure_gallery_keyset_indexes(conn)


def _migration_gallery_sort_filter_indexes(conn: sqlite3.Connection):
    _ensure_gallery_sort_seq_column(conn)
    columns = _table_columns(conn, "gallery_entries")
    if "favorite" in columns:
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_gallery_entries_favorite_sort_seq_id
                ON gallery_entries(favorite, sort_seq DESC, id DESC)
            """
        )
    if "model" in columns:
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_gallery_entries_model_sort_seq_id
                ON gallery_entries(model, sort_seq DESC, id DESC)
            """
        )
    if "api_preset_name" in columns:
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_gallery_entries_preset_sort_seq_id
                ON gallery_entries(api_preset_name, sort_seq DESC, id DESC)
            """
        )
    if "size" in columns:
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_gallery_entries_size_sort_seq_id
                ON gallery_entries(size, sort_seq DESC, id DESC)
            """
        )
    if "created_at" in columns:
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_gallery_entries_created_at_sort_seq_id
                ON gallery_entries(created_at DESC, sort_seq DESC, id DESC)
            """
        )


def _migration_gallery_page_anchors(conn: sqlite3.Connection):
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS gallery_meta (
            key TEXT PRIMARY KEY,
            value INTEGER NOT NULL
        )
        """
    )
    conn.execute(
        """
        INSERT OR IGNORE INTO gallery_meta (key, value)
        VALUES ('gallery_version', 0)
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS gallery_page_anchors (
            query_key TEXT NOT NULL,
            page_size INTEGER NOT NULL,
            page INTEGER NOT NULL,
            sort_seq INTEGER NOT NULL,
            image_id TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            gallery_version INTEGER NOT NULL,
            PRIMARY KEY(query_key, page_size, page)
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_gallery_page_anchors_lookup
            ON gallery_page_anchors(query_key, page_size, gallery_version, page)
        """
    )


def _migration_access_failures(conn: sqlite3.Connection):
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS access_failures (
            client_ip TEXT PRIMARY KEY,
            failure_count INTEGER NOT NULL DEFAULT 0,
            first_failed_at REAL NOT NULL,
            last_failed_at REAL NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_access_failures_last_failed_at
            ON access_failures(last_failed_at)
        """
    )


def _run_schema_migrations(conn: sqlite3.Connection):
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            applied_at TEXT NOT NULL
        )
        """
    )
    rows = conn.execute("SELECT version FROM schema_migrations").fetchall()
    applied_versions = {int(row["version"]) for row in rows}
    now = utc_now()

    if not applied_versions:
        conn.execute(
            """
            INSERT OR IGNORE INTO schema_migrations (version, name, applied_at)
            VALUES (?, ?, ?)
            """,
            (1, "baseline_legacy_schema", now),
        )
        applied_versions.add(1)

    for version, name, migration in SCHEMA_MIGRATIONS:
        if version in applied_versions:
            continue
        migration(conn)
        conn.execute(
            """
            INSERT INTO schema_migrations (version, name, applied_at)
            VALUES (?, ?, ?)
            """,
            (version, name, utc_now()),
        )


def _migration_gallery_entry_schema(conn: sqlite3.Connection):
    columns = _table_columns(conn, "gallery_entries")
    if "favorite" not in columns:
        conn.execute(
            "ALTER TABLE gallery_entries ADD COLUMN favorite INTEGER NOT NULL DEFAULT 0"
        )
    if "bytes" not in columns:
        conn.execute("ALTER TABLE gallery_entries ADD COLUMN bytes INTEGER")
    if "thumbnail_filename" not in columns:
        conn.execute("ALTER TABLE gallery_entries ADD COLUMN thumbnail_filename TEXT")
    if "completed_at" not in columns:
        conn.execute("ALTER TABLE gallery_entries ADD COLUMN completed_at TEXT")
    if "sha256" not in columns:
        conn.execute("ALTER TABLE gallery_entries ADD COLUMN sha256 TEXT")
    _ensure_gallery_sort_seq_column(conn)
    _ensure_gallery_keyset_indexes(conn)
    columns = _table_columns(conn, "gallery_entries")
    if "favorite" in columns:
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_gallery_entries_favorite_created_at
                ON gallery_entries(favorite, created_at DESC, sort_seq DESC)
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_gallery_entries_favorite_sort_seq_id
                ON gallery_entries(favorite, sort_seq DESC, id DESC)
            """
        )
    if "model" in columns:
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_gallery_entries_model_created_at
                ON gallery_entries(model, created_at DESC, sort_seq DESC)
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_gallery_entries_model_sort_seq_id
                ON gallery_entries(model, sort_seq DESC, id DESC)
            """
        )
    if "api_preset_name" in columns:
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_gallery_entries_preset_created_at
                ON gallery_entries(api_preset_name, created_at DESC, sort_seq DESC)
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_gallery_entries_preset_sort_seq_id
                ON gallery_entries(api_preset_name, sort_seq DESC, id DESC)
            """
        )
    if "size" in columns:
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_gallery_entries_size_created_at
                ON gallery_entries(size, created_at DESC, sort_seq DESC)
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_gallery_entries_size_sort_seq_id
                ON gallery_entries(size, sort_seq DESC, id DESC)
            """
        )
    if "created_at" in columns:
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_gallery_entries_created_at
                ON gallery_entries(created_at DESC, sort_seq DESC)
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_gallery_entries_created_at_sort_seq_id
                ON gallery_entries(created_at DESC, sort_seq DESC, id DESC)
            """
        )
    if {"filename", "bytes"}.issubset(columns):
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_gallery_entries_missing_bytes_filename
                ON gallery_entries(filename) WHERE bytes IS NULL
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_gallery_entries_filename_bytes
                ON gallery_entries(filename, bytes) WHERE bytes IS NOT NULL
            """
        )


def _migration_api_presets_schema(conn: sqlite3.Connection):
    columns = _table_columns(conn, "api_presets")
    if "default_model" not in columns:
        conn.execute("ALTER TABLE api_presets ADD COLUMN default_model TEXT")
    if "default_response_format" not in columns:
        conn.execute(
            "ALTER TABLE api_presets ADD COLUMN default_response_format TEXT NOT NULL DEFAULT 'url'"
        )
    conn.execute(
        """
        UPDATE api_presets
        SET default_model = CASE
            WHEN api_path = ? AND ? != '' THEN ?
            ELSE ?
        END
        WHERE default_model IS NULL OR trim(default_model) = ''
        """,
        (
            "/v1/responses",
            str(config.DEFAULT_RESPONSES_MODEL or "").strip(),
            str(config.DEFAULT_RESPONSES_MODEL or "").strip(),
            default_model_for_api_path("/v1/images/generations"),
        ),
    )
    conn.execute(
        """
        UPDATE api_presets
        SET default_response_format = ?
        WHERE default_response_format IS NULL
            OR trim(default_response_format) NOT IN ('', 'url', 'b64_json')
        """,
        ("url",),
    )


def _migration_generate_jobs_schema(conn: sqlite3.Connection):
    columns = _table_columns(conn, "generate_jobs")
    if "stage_timings_json" not in columns:
        conn.execute("ALTER TABLE generate_jobs ADD COLUMN stage_timings_json TEXT")
    if "images_json" not in columns:
        conn.execute("ALTER TABLE generate_jobs ADD COLUMN images_json TEXT")
    if "webhook_url" not in columns:
        conn.execute("ALTER TABLE generate_jobs ADD COLUMN webhook_url TEXT")


def _migration_generate_job_counts(conn: sqlite3.Connection):
    columns = _table_columns(conn, "generate_jobs")
    for column in ("completed_count", "success_count", "failure_count"):
        if column not in columns:
            conn.execute(f"ALTER TABLE generate_jobs ADD COLUMN {column} INTEGER")


def _migration_gallery_jobs_schema(conn: sqlite3.Connection):
    columns = _table_columns(conn, "gallery_jobs")
    if "pending_upload_count" not in columns:
        conn.execute(
            "ALTER TABLE gallery_jobs ADD COLUMN pending_upload_count INTEGER NOT NULL DEFAULT 0"
        )


def _migration_r2_sync_state_schema(conn: sqlite3.Connection):
    columns = _table_columns(conn, "r2_sync_state")
    if "etag" not in columns:
        conn.execute("ALTER TABLE r2_sync_state ADD COLUMN etag TEXT")
    if "last_remote_seen_at" not in columns:
        conn.execute("ALTER TABLE r2_sync_state ADD COLUMN last_remote_seen_at TEXT")


def _migration_prompt_snippets_schema(conn: sqlite3.Connection):
    columns = _table_columns(conn, "prompt_snippets")
    if "favorite" not in columns:
        conn.execute(
            "ALTER TABLE prompt_snippets ADD COLUMN favorite INTEGER NOT NULL DEFAULT 0"
        )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_prompt_snippets_favorite_updated_at
            ON prompt_snippets(favorite DESC, updated_at DESC)
        """
    )


def _migration_gallery_ai_metadata_schema(conn: sqlite3.Connection):
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS gallery_ai_metadata (
            image_id TEXT PRIMARY KEY,
            description TEXT NOT NULL DEFAULT '',
            prompt TEXT NOT NULL DEFAULT '',
            analysis_json TEXT NOT NULL DEFAULT '{}',
            model TEXT NOT NULL DEFAULT '',
            updated_at TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY(image_id) REFERENCES gallery_entries(id) ON DELETE CASCADE
        )
        """
    )


def _migration_background_column(conn: sqlite3.Connection):
    columns_gallery = _table_columns(conn, "gallery_entries")
    if "background" not in columns_gallery:
        conn.execute("ALTER TABLE gallery_entries ADD COLUMN background TEXT")

    columns_jobs = _table_columns(conn, "generate_jobs")
    if "background" not in columns_jobs:
        conn.execute("ALTER TABLE generate_jobs ADD COLUMN background TEXT")


def _migration_generate_job_usage_cost_columns(conn: sqlite3.Connection):
    jobs_columns = _table_columns(conn, "generate_jobs")
    if "usage_json" not in jobs_columns:
        conn.execute("ALTER TABLE generate_jobs ADD COLUMN usage_json TEXT")
    if "cost_json" not in jobs_columns:
        conn.execute("ALTER TABLE generate_jobs ADD COLUMN cost_json TEXT")

    unit_columns = _table_columns(conn, "image_job_units")
    if "usage_json" not in unit_columns:
        conn.execute("ALTER TABLE image_job_units ADD COLUMN usage_json TEXT")
    if "cost_json" not in unit_columns:
        conn.execute("ALTER TABLE image_job_units ADD COLUMN cost_json TEXT")


def _migration_generate_job_streaming_columns(conn: sqlite3.Connection):
    jobs_columns = _table_columns(conn, "generate_jobs")
    if "streaming" not in jobs_columns:
        conn.execute("ALTER TABLE generate_jobs ADD COLUMN streaming INTEGER")
    if "partial_images" not in jobs_columns:
        conn.execute("ALTER TABLE generate_jobs ADD COLUMN partial_images INTEGER")


def _migration_generate_job_mask_column(conn: sqlite3.Connection):
    jobs_columns = _table_columns(conn, "generate_jobs")
    if "mask_applied" not in jobs_columns:
        conn.execute("ALTER TABLE generate_jobs ADD COLUMN mask_applied INTEGER")


def _migration_api_preset_supports_mask(conn: sqlite3.Connection):
    preset_columns = _table_columns(conn, "api_presets")
    if "supports_mask" not in preset_columns:
        conn.execute(
            "ALTER TABLE api_presets ADD COLUMN supports_mask INTEGER NOT NULL DEFAULT 1"
        )


def _migration_gallery_mask_coverage(conn: sqlite3.Connection):
    gallery_columns = _table_columns(conn, "gallery_entries")
    if "mask_coverage" not in gallery_columns:
        conn.execute("ALTER TABLE gallery_entries ADD COLUMN mask_coverage REAL")


def _migration_image_job_unit_lease_fencing(conn: sqlite3.Connection):
    unit_columns = _table_columns(conn, "image_job_units")
    if "claim_token" not in unit_columns:
        conn.execute("ALTER TABLE image_job_units ADD COLUMN claim_token TEXT")
    if "attempts" not in unit_columns:
        conn.execute(
            "ALTER TABLE image_job_units ADD COLUMN attempts INTEGER NOT NULL DEFAULT 0"
        )
    conn.execute("DROP INDEX IF EXISTS idx_image_job_units_running_count")
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_image_job_units_running_count
            ON image_job_units(claim_expires_at)
            WHERE status = 'running'
        """
    )


def _migration_performance_indexes(conn: sqlite3.Connection):
    # Queue counts and the enqueue capacity guard filter on both active statuses
    # at once, which neither single-status partial index can satisfy. Matching
    # the predicate exactly keeps this index as small as the live queue.
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_image_job_units_active_status
            ON image_job_units(status)
            WHERE status IN ('queued', 'running')
        """
    )
    # Auxiliary-state GC deletes stale heartbeats by last_seen_at on every
    # maintenance cycle.
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_worker_heartbeats_last_seen
            ON worker_heartbeats(last_seen_at)
        """
    )
    # Gallery job file cleanup filters on kind with a non-null path.
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_gallery_jobs_kind_path
            ON gallery_jobs(kind, path)
            WHERE path IS NOT NULL
        """
    )
    # Lockout listing and overflow eviction order by (last_failed_at, client_ip).
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_access_failures_last_failed_client
            ON access_failures(last_failed_at, client_ip)
        """
    )


def _migration_gallery_mask_coverage_index(conn: sqlite3.Connection):
    # mask_only filters gallery_entries down to masked edits before the usual
    # (sort_seq DESC, id DESC) keyset page; without this the filter falls back
    # to a full scan + sort once the gallery has thousands of rows.
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_gallery_entries_masked_sort_seq_id
            ON gallery_entries(sort_seq DESC, id DESC)
            WHERE mask_coverage IS NOT NULL
        """
    )


def _migration_mask_paste_back(conn: sqlite3.Connection):
    gallery_columns = _table_columns(conn, "gallery_entries")
    if "paste_back" not in gallery_columns:
        conn.execute("ALTER TABLE gallery_entries ADD COLUMN paste_back TEXT")
    if "paste_back_scale" not in gallery_columns:
        conn.execute("ALTER TABLE gallery_entries ADD COLUMN paste_back_scale REAL")
    if "paste_back" not in _table_columns(conn, "generate_jobs"):
        conn.execute("ALTER TABLE generate_jobs ADD COLUMN paste_back INTEGER")


SCHEMA_MIGRATIONS = (
    (1, "baseline_legacy_schema", _migration_baseline_legacy_schema),
    (2, "gallery_filter_options", _migration_gallery_filter_options),
    (3, "thumbnail_jobs", _migration_thumbnail_jobs),
    (4, "gallery_keyset_index", _migration_gallery_keyset_index),
    (5, "gallery_sort_filter_indexes", _migration_gallery_sort_filter_indexes),
    (6, "gallery_page_anchors", _migration_gallery_page_anchors),
    (7, "access_failures", _migration_access_failures),
    (8, "api_presets_schema", _migration_api_presets_schema),
    (9, "gallery_entry_schema", _migration_gallery_entry_schema),
    (10, "generate_jobs_schema", _migration_generate_jobs_schema),
    (11, "gallery_jobs_schema", _migration_gallery_jobs_schema),
    (12, "r2_sync_state_schema", _migration_r2_sync_state_schema),
    (13, "prompt_snippets_schema", _migration_prompt_snippets_schema),
    (14, "gallery_ai_metadata_schema", _migration_gallery_ai_metadata_schema),
    (15, "generate_job_counts", _migration_generate_job_counts),
    (16, "background_column", _migration_background_column),
    (17, "generate_job_usage_cost_columns", _migration_generate_job_usage_cost_columns),
    (18, "generate_job_streaming_columns", _migration_generate_job_streaming_columns),
    (19, "image_job_unit_lease_fencing", _migration_image_job_unit_lease_fencing),
    (20, "performance_indexes", _migration_performance_indexes),
    (21, "generate_job_mask_column", _migration_generate_job_mask_column),
    (22, "api_preset_supports_mask", _migration_api_preset_supports_mask),
    (23, "gallery_mask_coverage", _migration_gallery_mask_coverage),
    (24, "gallery_mask_coverage_index", _migration_gallery_mask_coverage_index),
    (25, "mask_paste_back", _migration_mask_paste_back),
)
