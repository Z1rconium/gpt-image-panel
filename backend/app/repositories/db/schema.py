"""Storage initialisation, schema creation, and forward-only migrations.
Migrations are additive: new columns are nullable and existing rows are
never rebuilt."""

import logging
import time
from pathlib import Path

from ...core import settings as config
from . import state as db_state
from .connection import (
    _check_directory_writable,
    _connect,
    _ensure_directories,
    _invalidate_gallery_query_caches,
    _secure_data_storage_permissions,
)
from .migrations import (
    _ensure_gallery_fts,
    _run_schema_migrations,
)
from .state import (
    _clear_verified_thumbnails,
)

logger = logging.getLogger(__name__)

# Per-domain schema DDL, applied in one ``executescript`` call in the order
# below. Each chunk lives next to its owning domain so schema changes stay
# reviewable; ``_ensure_database`` keeps only the init policy (idempotency
# guard, PRAGMAs, migrations, FTS, optimize, permissions).
_SCHEMA_DDL = (
    # settings + migrations bookkeeping
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
    """,
    # api presets
    """
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
    """,
    # gallery entries + filter/meta/anchors
    """
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
    """,
    # thumbnail jobs
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
    );

    CREATE INDEX IF NOT EXISTS idx_thumbnail_jobs_claim
        ON thumbnail_jobs(status, lease_expires_at, created_at);
    """,
    # r2 sync state
    """
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
    """,
    # generation jobs
    """
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
    """,
    # image job units
    """
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
    """,
    # gallery jobs
    """
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
    """,
    # worker heartbeats + metric snapshots
    """
    CREATE TABLE IF NOT EXISTS worker_heartbeats (
        worker_id TEXT PRIMARY KEY,
        last_seen_at TEXT NOT NULL,
        active_units INTEGER NOT NULL DEFAULT 0
    );

    CREATE TABLE IF NOT EXISTS worker_metric_snapshots (
        worker_id TEXT PRIMARY KEY,
        snapshot_json TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );

    CREATE INDEX IF NOT EXISTS idx_worker_metric_snapshots_updated_at
        ON worker_metric_snapshots(updated_at DESC);
    """,
    # sse slots
    """
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
    """,
    # background leases
    """
    CREATE TABLE IF NOT EXISTS background_leases (
        name TEXT PRIMARY KEY,
        owner TEXT NOT NULL,
        lease_expires_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        completed_at TEXT
    );

    CREATE INDEX IF NOT EXISTS idx_background_leases_expires
        ON background_leases(lease_expires_at);
    """,
    # access failures
    """
    CREATE TABLE IF NOT EXISTS access_failures (
        client_ip TEXT PRIMARY KEY,
        failure_count INTEGER NOT NULL DEFAULT 0,
        first_failed_at REAL NOT NULL,
        last_failed_at REAL NOT NULL
    );

    CREATE INDEX IF NOT EXISTS idx_access_failures_last_failed_at
        ON access_failures(last_failed_at);
    """,
    # edit/import reservations
    """
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
    """,
    # prompt snippets
    """
    CREATE TABLE IF NOT EXISTS prompt_snippets (
        id TEXT PRIMARY KEY,
        title TEXT NOT NULL,
        prompt TEXT NOT NULL,
        favorite INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    """,
    # overall config values
    """
    CREATE TABLE IF NOT EXISTS overall_config_values (
        name TEXT PRIMARY KEY,
        env_value TEXT NOT NULL DEFAULT '',
        override_value TEXT,
        is_env_set INTEGER NOT NULL DEFAULT 0,
        updated_at TEXT NOT NULL,
        override_updated_at TEXT
    );
    """,
    # gallery ai metadata
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
    );
    """,
)


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
            conn.executescript("\n".join(_SCHEMA_DDL))
            _run_schema_migrations(conn)
            _ensure_gallery_fts(conn)
            conn.commit()
            conn.execute("PRAGMA optimize")

        db_state._initialized_database_file = database_file
        _secure_data_storage_permissions(force=True)
