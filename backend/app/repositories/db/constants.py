"""Column tuples, tunables, and queue-capacity errors for the persistence layer."""

class ImageJobQueueFullError(RuntimeError):
    """Raised when the SQLite-backed image unit queue has no remaining capacity."""


class EditSourceQueueFullError(RuntimeError):
    """Raised when pending edit source byte reservations exceed the configured cap."""


GALLERY_COLUMNS = (
    "id",
    "prompt",
    "size",
    "filename",
    "thumbnail_filename",
    "created_at",
    "completed_at",
    "image_width",
    "image_height",
    "model",
    "quality",
    "output_format",
    "output_compression",
    "background",
    "response_format",
    "n",
    "api_path",
    "api_preset_name",
    "duration",
    "favorite",
    "bytes",
    "sha256",
    "sort_seq",
    "mask_coverage",
    "paste_back",
    "paste_back_scale",
)
REQUIRED_GALLERY_COLUMNS = {"id", "prompt", "size", "filename", "created_at"}
_GALLERY_INTERNAL_COLUMNS = {"sort_seq"}
INTEGER_GALLERY_COLUMNS = {
    "image_width",
    "image_height",
    "output_compression",
    "n",
    "favorite",
    "bytes",
    "sort_seq",
}
REAL_GALLERY_COLUMNS = {"mask_coverage", "paste_back_scale"}
GENERATE_JOB_COLUMNS = (
    "job_id",
    "status",
    "stage",
    "message",
    "operation",
    "prompt",
    "size",
    "created_at",
    "started_at",
    "completed_at",
    "updated_at",
    "model",
    "quality",
    "output_format",
    "output_compression",
    "background",
    "response_format",
    "n",
    "completed_count",
    "success_count",
    "failure_count",
    "api_path",
    "api_preset_name",
    "duration",
    "stage_timings_json",
    "image_id",
    "image_url",
    "images_json",
    "image_width",
    "image_height",
    "usage_json",
    "cost_json",
    "streaming",
    "partial_images",
    "mask_applied",
    "paste_back",
    "error",
    "webhook_url",
)
IMAGE_JOB_UNIT_COLUMNS = (
    "unit_id",
    "parent_job_id",
    "operation",
    "unit_index",
    "status",
    "claimed_by",
    "claim_expires_at",
    "stage",
    "message",
    "error",
    "result_json",
    "stage_timings_json",
    "usage_json",
    "cost_json",
    "duration",
    "created_at",
    "started_at",
    "completed_at",
    "updated_at",
    "request_json",
    "edit_sources_json",
    "api_preset_id",
    "api_preset_name",
    "api_path",
    "claim_token",
    "attempts",
)
GALLERY_JOB_COLUMNS = (
    "job_id",
    "kind",
    "status",
    "stage",
    "message",
    "progress",
    "filename",
    "download_url",
    "path",
    "requested_count",
    "processed_count",
    "exported_count",
    "missing_count",
    "total_count",
    "compared_count",
    "uploaded_count",
    "pending_upload_count",
    "skipped_existing_count",
    "missing_local_count",
    "failed_count",
    "bytes_total",
    "bytes_written",
    "bytes_uploaded",
    "created_at",
    "started_at",
    "completed_at",
    "updated_at",
    "error",
    "lease_owner",
    "lease_expires_at",
    "payload_json",
)
PROMPT_SNIPPET_COLUMNS = (
    "id",
    "title",
    "prompt",
    "favorite",
    "created_at",
    "updated_at",
)
INTEGER_GENERATE_JOB_COLUMNS = {
    "output_compression",
    "n",
    "completed_count",
    "success_count",
    "failure_count",
    "image_width",
    "image_height",
    "streaming",
    "partial_images",
    "mask_applied",
    "paste_back",
}
SETTINGS_ACTIVE_PRESET_KEY = "active_preset_id"
UPSTREAM_SOCKS5_PROXY_KEY = "upstream_socks5_proxy"
WEBHOOK_URL_KEY = "webhook_url"
PROMPT_OPTIMIZER_SETTINGS_KEY = "prompt_optimizer_settings"
AI_ASSISTANT_SETTINGS_KEY = "ai_assistant_settings"
R2_BACKUP_SETTINGS_KEY = "r2_backup_settings"
NODEIMAGE_SETTINGS_KEY = "nodeimage_settings"
SQLITE_TIMEOUT_SECONDS = 30.0
DATA_DIR_MODE = 0o700
DATA_FILE_MODE = 0o600
DATA_PERMISSION_CHECK_INTERVAL_SECONDS = 60.0
GALLERY_SYNC_BATCH_SIZE = 500
GALLERY_FTS_VERSION_KEY = "gallery_fts_version"
GALLERY_FTS_VERSION = "trigram-v1"
GALLERY_FTS_MIN_QUERY_LENGTH = 3
SQLITE_IN_CLAUSE_CHUNK_SIZE = 900
GALLERY_COUNT_CACHE_SECONDS = 2.0
GALLERY_TOTAL_BYTES_CACHE_SECONDS = 2.0
GALLERY_PAGE_ANCHOR_INTERVAL_PAGES = 100
GALLERY_PAGE_ANCHOR_SMALL_OFFSET_THRESHOLD = 10_000
GALLERY_PAGE_ANCHOR_MAX_PER_QUERY = 256
GALLERY_PAGE_ANCHOR_INVALIDATING_UPDATE_FIELDS = {
    "prompt",
    "model",
    "api_preset_name",
    "size",
    "favorite",
    "created_at",
    "sort_seq",
}
GALLERY_ORPHAN_FILE_TTL_SECONDS = 300
GALLERY_ORPHAN_GC_BATCH_SIZE = 500
GALLERY_IMPORT_BATCH_SIZE = 50
MAX_PERSISTED_JOB_TEXT_CHARS = 2000
_GALLERY_COUNT_CACHE_MAX_SIZE = 512
_GALLERY_BYTES_CACHE_MAX_SIZE = 512
THUMBNAIL_CPU_SLOT_LEASE_SECONDS = 600
THUMBNAIL_JOB_LEASE_SECONDS = 600
THUMBNAIL_JOB_MAX_ATTEMPTS = 3
WORKER_METRIC_SNAPSHOT_TTL_SECONDS = 300
