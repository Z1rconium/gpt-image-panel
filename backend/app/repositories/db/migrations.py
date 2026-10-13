"""Forward-only schema migrations for the SQLite store.
Migrations are additive: new columns are nullable and existing rows are
never rebuilt."""

import logging
import sqlite3

from ...core import settings as config
from ...core.api_paths import default_model_for_api_path
from ...core.utils import utc_now
from . import state as db_state
from .connection import (
    _get_setting_value,
    _set_setting_value,
    _table_columns,
)
from .constants import (
    GALLERY_FTS_VERSION,
    GALLERY_FTS_VERSION_KEY,
)
from .rows import (
    _rebuild_gallery_filter_options_on_conn,
)

logger = logging.getLogger(__name__)


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

def _ensure_gallery_sort_seq_filter_indexes(conn: sqlite3.Connection):
    """Create the per-filter keyset indexes shared by migrations 5 and 9."""
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

def _migration_gallery_sort_filter_indexes(conn: sqlite3.Connection):
    _ensure_gallery_sort_seq_column(conn)
    _ensure_gallery_sort_seq_filter_indexes(conn)

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
    _ensure_gallery_sort_seq_filter_indexes(conn)
    columns = _table_columns(conn, "gallery_entries")
    if "favorite" in columns:
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_gallery_entries_favorite_created_at
                ON gallery_entries(favorite, created_at DESC, sort_seq DESC)
            """
        )
    if "model" in columns:
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_gallery_entries_model_created_at
                ON gallery_entries(model, created_at DESC, sort_seq DESC)
            """
        )
    if "api_preset_name" in columns:
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_gallery_entries_preset_created_at
                ON gallery_entries(api_preset_name, created_at DESC, sort_seq DESC)
            """
        )
    if "size" in columns:
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_gallery_entries_size_created_at
                ON gallery_entries(size, created_at DESC, sort_seq DESC)
            """
        )
    if "created_at" in columns:
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_gallery_entries_created_at
                ON gallery_entries(created_at DESC, sort_seq DESC)
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

def _migration_image_trace(conn: sqlite3.Connection):
    columns = _table_columns(conn, "gallery_entries")
    for name, type_name in (
        ("sent_prompt", "TEXT"),
        ("revised_prompt", "TEXT"),
        ("reported_size", "TEXT"),
        ("reported_quality", "TEXT"),
        ("upstream_duration_ms", "INTEGER"),
        ("chroma_status", "TEXT"),
    ):
        if name not in columns:
            conn.execute(f"ALTER TABLE gallery_entries ADD COLUMN {name} {type_name}")

def _migration_gallery_collections(conn: sqlite3.Connection):
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS gallery_collections (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL COLLATE NOCASE UNIQUE,
            position INTEGER NOT NULL DEFAULT 0,
            is_default INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_gallery_collections_single_default
            ON gallery_collections(is_default)
            WHERE is_default = 1
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS gallery_collection_items (
            collection_id TEXT NOT NULL,
            image_id TEXT NOT NULL,
            added_at TEXT NOT NULL,
            PRIMARY KEY(collection_id, image_id),
            FOREIGN KEY(collection_id) REFERENCES gallery_collections(id) ON DELETE CASCADE,
            FOREIGN KEY(image_id) REFERENCES gallery_entries(id) ON DELETE CASCADE
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_gallery_collection_items_image
            ON gallery_collection_items(image_id)
        """
    )

def _migration_api_preset_prompt_guard(conn: sqlite3.Connection):
    if "prompt_guard" not in _table_columns(conn, "api_presets"):
        conn.execute(
            "ALTER TABLE api_presets ADD COLUMN prompt_guard INTEGER NOT NULL DEFAULT 0"
        )

def _migration_api_preset_provider(conn: sqlite3.Connection):
    columns = _table_columns(conn, "api_presets")
    if "provider_kind" not in columns:
        conn.execute(
            "ALTER TABLE api_presets ADD COLUMN provider_kind TEXT NOT NULL DEFAULT 'openai'"
        )
    if "provider_config" not in columns:
        conn.execute("ALTER TABLE api_presets ADD COLUMN provider_config TEXT")

def _migration_gallery_diagnostics(conn: sqlite3.Connection):
    if "diagnostics" not in _table_columns(conn, "gallery_entries"):
        conn.execute("ALTER TABLE gallery_entries ADD COLUMN diagnostics TEXT")

def _migration_generate_job_preset_id(conn: sqlite3.Connection):
    if "api_preset_id" not in _table_columns(conn, "generate_jobs"):
        conn.execute("ALTER TABLE generate_jobs ADD COLUMN api_preset_id TEXT")

def _migration_image_job_unit_recovery(conn: sqlite3.Connection):
    """Remote task checkpoints and bounded recovery for image units."""
    columns = _table_columns(conn, "image_job_units")
    if "remote_json" not in columns:
        conn.execute("ALTER TABLE image_job_units ADD COLUMN remote_json TEXT")
    if "checkpointed" not in columns:
        conn.execute(
            "ALTER TABLE image_job_units ADD COLUMN checkpointed INTEGER NOT NULL DEFAULT 0"
        )
    if "recovery_count" not in columns:
        conn.execute(
            "ALTER TABLE image_job_units ADD COLUMN recovery_count INTEGER NOT NULL DEFAULT 0"
        )

def _migration_image_job_unit_diagnostics(conn: sqlite3.Connection):
    if "diagnostics_json" not in _table_columns(conn, "image_job_units"):
        conn.execute("ALTER TABLE image_job_units ADD COLUMN diagnostics_json TEXT")

def _migration_agent_conversations(conn: sqlite3.Connection):
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS agent_conversations (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL DEFAULT '',
            title_is_auto INTEGER NOT NULL DEFAULT 1,
            message_count INTEGER NOT NULL DEFAULT 0,
            turn_count INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_agent_conversations_updated
            ON agent_conversations(updated_at DESC)
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS agent_turns (
            id TEXT PRIMARY KEY,
            conversation_id TEXT NOT NULL,
            round_no INTEGER NOT NULL,
            client_turn_id TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'queued'
                CHECK (status IN ('queued', 'running', 'completed', 'failed', 'cancelled', 'interrupted')),
            cancel_requested INTEGER NOT NULL DEFAULT 0,
            lease_owner TEXT,
            lease_expires_at TEXT,
            rounds_used INTEGER NOT NULL DEFAULT 0,
            model TEXT NOT NULL DEFAULT '',
            image_params_json TEXT NOT NULL DEFAULT '{}',
            error_message TEXT,
            user_message_id TEXT,
            assistant_message_id TEXT,
            created_at TEXT NOT NULL,
            started_at TEXT,
            finished_at TEXT,
            FOREIGN KEY(conversation_id) REFERENCES agent_conversations(id) ON DELETE CASCADE,
            UNIQUE(conversation_id, client_turn_id),
            UNIQUE(conversation_id, round_no)
        )
        """
    )
    conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_agent_turns_single_active
            ON agent_turns(conversation_id)
            WHERE status IN ('queued', 'running')
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_agent_turns_status_lease
            ON agent_turns(status, lease_expires_at)
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS agent_messages (
            id TEXT PRIMARY KEY,
            conversation_id TEXT NOT NULL,
            turn_id TEXT NOT NULL,
            seq INTEGER NOT NULL,
            round_no INTEGER NOT NULL,
            role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
            text TEXT NOT NULL DEFAULT '',
            blocks_json TEXT NOT NULL DEFAULT '[]',
            status TEXT NOT NULL DEFAULT 'complete',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY(conversation_id) REFERENCES agent_conversations(id) ON DELETE CASCADE,
            FOREIGN KEY(turn_id) REFERENCES agent_turns(id) ON DELETE CASCADE,
            UNIQUE(conversation_id, seq)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS agent_message_images (
            id TEXT PRIMARY KEY,
            conversation_id TEXT NOT NULL,
            turn_id TEXT NOT NULL,
            message_id TEXT NOT NULL,
            round_no INTEGER NOT NULL,
            image_index INTEGER NOT NULL,
            role TEXT NOT NULL CHECK (role IN ('input', 'output')),
            ref_label TEXT NOT NULL,
            image_id TEXT,
            job_id TEXT,
            item_id TEXT,
            prompt TEXT NOT NULL DEFAULT '',
            mode TEXT NOT NULL DEFAULT 'generate',
            status TEXT NOT NULL DEFAULT 'pending'
                CHECK (status IN ('pending', 'succeeded', 'failed', 'cancelled')),
            error TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY(conversation_id) REFERENCES agent_conversations(id) ON DELETE CASCADE,
            FOREIGN KEY(turn_id) REFERENCES agent_turns(id) ON DELETE CASCADE,
            FOREIGN KEY(message_id) REFERENCES agent_messages(id) ON DELETE CASCADE,
            FOREIGN KEY(image_id) REFERENCES gallery_entries(id) ON DELETE SET NULL,
            UNIQUE(conversation_id, ref_label)
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_agent_message_images_image ON agent_message_images(image_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_agent_message_images_message ON agent_message_images(message_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_agent_message_images_turn ON agent_message_images(turn_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_agent_message_images_job ON agent_message_images(job_id)"
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS agent_turn_events (
            turn_id TEXT NOT NULL,
            seq INTEGER NOT NULL,
            type TEXT NOT NULL,
            data_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY(turn_id, seq),
            FOREIGN KEY(turn_id) REFERENCES agent_turns(id) ON DELETE CASCADE
        )
        """
    )

def _migration_agent_performance_indexes(conn: sqlite3.Connection):
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_agent_message_images_conversation_order
            ON agent_message_images(conversation_id, round_no, role, image_index)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_agent_message_images_pending
            ON agent_message_images(turn_id) WHERE status = 'pending'
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_agent_turns_finished
            ON agent_turns(finished_at) WHERE finished_at IS NOT NULL
        """
    )

def _migration_image_job_preview_context(conn: sqlite3.Connection):
    columns = _table_columns(conn, "generate_jobs")
    for column in ("unit_statuses_json", "agent_turn_id", "agent_conversation_id"):
        if column not in columns:
            conn.execute(f"ALTER TABLE generate_jobs ADD COLUMN {column} TEXT")

def _migration_agent_branches(conn: sqlite3.Connection):
    turn_columns = {row[1] for row in conn.execute("PRAGMA table_info(agent_turns)")}
    if "parent_turn_id" not in turn_columns:
        conn.execute("ALTER TABLE agent_turns ADD COLUMN parent_turn_id TEXT REFERENCES agent_turns(id)")
        parents: dict[str, str] = {}
        for row in conn.execute("SELECT id, conversation_id FROM agent_turns ORDER BY conversation_id, round_no").fetchall():
            conn.execute("UPDATE agent_turns SET parent_turn_id = ? WHERE id = ?", (parents.get(row[1]), row[0]))
            parents[row[1]] = row[0]
    columns = {row[1] for row in conn.execute("PRAGMA table_info(agent_conversations)")}
    if "selected_turn_id" not in columns:
        conn.execute("ALTER TABLE agent_conversations ADD COLUMN selected_turn_id TEXT REFERENCES agent_turns(id) ON DELETE SET NULL")
        conn.execute("UPDATE agent_conversations SET selected_turn_id = (SELECT id FROM agent_turns WHERE conversation_id = agent_conversations.id ORDER BY round_no DESC LIMIT 1)")
    if "branch_revision" not in columns:
        conn.execute("ALTER TABLE agent_conversations ADD COLUMN branch_revision INTEGER NOT NULL DEFAULT 0")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_agent_turns_parent ON agent_turns(conversation_id, parent_turn_id)")

def _migration_agent_search(conn: sqlite3.Connection):
    columns = {row[1] for row in conn.execute("PRAGMA table_info(agent_turns)")}
    if "web_search_enabled" not in columns:
        conn.execute("ALTER TABLE agent_turns ADD COLUMN web_search_enabled INTEGER NOT NULL DEFAULT 0")

def _migration_agent_execution_snapshot(conn: sqlite3.Connection):
    # '{}' marks a legacy turn accepted before snapshots existed; it runs on live settings.
    columns = {row[1] for row in conn.execute("PRAGMA table_info(agent_turns)")}
    if "execution_snapshot_json" not in columns:
        conn.execute("ALTER TABLE agent_turns ADD COLUMN execution_snapshot_json TEXT NOT NULL DEFAULT '{}'")

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
    (26, "image_trace", _migration_image_trace),
    (27, "gallery_collections", _migration_gallery_collections),
    (28, "api_preset_prompt_guard", _migration_api_preset_prompt_guard),
    (29, "gallery_diagnostics", _migration_gallery_diagnostics),
    (30, "generate_job_preset_id", _migration_generate_job_preset_id),
    (31, "agent_conversations", _migration_agent_conversations),
    (32, "agent_performance_indexes", _migration_agent_performance_indexes),
    (33, "api_preset_provider", _migration_api_preset_provider),
    (34, "image_job_unit_recovery", _migration_image_job_unit_recovery),
    (35, "image_job_unit_diagnostics", _migration_image_job_unit_diagnostics),
    (36, "image_job_preview_context", _migration_image_job_preview_context),
    (37, "agent_branches", _migration_agent_branches),
    (38, "agent_search", _migration_agent_search),
    (39, "agent_execution_snapshot", _migration_agent_execution_snapshot),
)
