"""Persistence facade: the connection surface plus what callers import.

The code lives in the sibling modules. New code should import from them
directly, for example ``from .db.rows import _gallery_entry_from_row``.
"""

import logging

from . import connection, constants, rows, schema, settings_store, state

logger = logging.getLogger(__name__)

import sqlite3

from .connection import (
    _close_thread_connection,
    _connect,
    _get_gallery_version_on_conn,
    _get_setting_value,
    _get_thread_connection,
    _invalidate_gallery_query_caches_on_conn,
    _iter_sqlite_in_chunks,
    _open_connection,
    _secure_data_storage_permissions,
    _set_setting_value,
    _table_columns,
    _transaction,
    _unique_sqlite_values,
    busy_timeout_scope,
    close_database_connections,
    current_db_metric,
    metric_name_scope,
    persistent_connection_scope,
)
from .constants import (
    AI_ASSISTANT_SETTINGS_KEY,
    EditSourceQueueFullError,
    GALLERY_COLUMNS,
    GALLERY_COUNT_CACHE_SECONDS,
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
    ImageJobQueueFullError,
    NODEIMAGE_SETTINGS_KEY,
    PROMPT_OPTIMIZER_SETTINGS_KEY,
    PROMPT_SNIPPET_COLUMNS,
    R2_BACKUP_SETTINGS_KEY,
    REQUIRED_GALLERY_COLUMNS,
    THUMBNAIL_CPU_SLOT_LEASE_SECONDS,
    THUMBNAIL_JOB_LEASE_SECONDS,
    THUMBNAIL_JOB_MAX_ATTEMPTS,
    WORKER_METRIC_SNAPSHOT_TTL_SECONDS,
    _GALLERY_BYTES_CACHE_MAX_SIZE,
    _GALLERY_COUNT_CACHE_MAX_SIZE,
)
from .rows import (
    GalleryPage,
    _GalleryFilterOptionsCacheEntry,
    _GalleryPaginationState,
    _GalleryQueryComponents,
    _PreparedGalleryFile,
    _add_gallery_filter_option_deltas,
    _apply_gallery_filter_option_deltas_on_conn,
    _attach_gallery_thumbnail_url,
    _build_gallery_filter_where,
    _coerce_nonnegative_int,
    _gallery_entry_from_row,
    _gallery_job_from_row,
    _gallery_job_values,
    _gallery_query_key_from_components,
    _gallery_row_values,
    _generate_job_from_row,
    _generate_prompt_snippet_id,
    _image_job_unit_from_row,
    _image_job_unit_values,
    _increment_gallery_filter_options_on_conn,
    _json_loads_dict,
    _like_prompt_snippet_query,
    _normalize_gallery_entry,
    _normalize_gallery_favorite,
    _normalize_gallery_job,
    _normalize_generate_job,
    _normalize_prompt_snippet_favorite,
    _prompt_snippet_from_row,
    _rebuild_gallery_filter_options_on_conn,
    _sanitize_persisted_job_text,
    _upsert_generate_job_on_conn,
    image_url_for_filename,
)
from .schema import (
    SCHEMA_MIGRATIONS,
    _ensure_database,
    _migration_access_failures,
    _migration_background_column,
    _migration_gallery_page_anchors,
    _migration_generate_job_counts,
    _migration_generate_job_streaming_columns,
    _migration_generate_job_usage_cost_columns,
    _migration_image_job_unit_lease_fencing,
    optimize_database_if_due,
    verify_storage_writable,
)
from .settings_store import (
    _coerce_iso_datetime,
    _default_ai_assistant_settings,
    _default_prompt_optimizer_settings,
    _default_settings,
    _load_nodeimage_settings_from_conn,
    _load_r2_backup_settings_from_conn,
    _load_settings_from_conn,
    _normalize_ai_assistant_settings,
    _normalize_nodeimage_settings,
    _normalize_prompt_optimizer_settings,
    _normalize_r2_backup_settings,
    _normalize_settings,
    _replace_settings_on_conn,
    list_overall_config_values,
    save_overall_config_overrides,
    sync_overall_config_env_values,
)
from .state import (
    _add_verified_thumbnail,
    _clear_verified_thumbnails,
    _get_filter_options_cache_version,
    _invalidate_filter_options_cache,
    _invalidate_gallery_total_bytes_cache,
    _remove_verified_thumbnail,
)

