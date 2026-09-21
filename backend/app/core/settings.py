"""Runtime configuration.

Values come from three places, in this order:

1. Environment variables, read once at import into module constants.
2. Values derived from other settings (``DERIVED_CONFIG_NAMES``), which follow
   their base value unless the name is configured explicitly.
3. Runtime overrides stored in SQLite and applied by
   ``core.overall_config.apply_rows_to_config``, which is the only caller
   allowed to change a setting after import (see ``apply_overrides``).

Readers keep using module attributes (``config.MAX_FILE_SIZE_MB``); what
changed is that writing them now happens in exactly one place that also
recomputes the derived values.
"""

import os
import re
from pathlib import Path
from collections.abc import Callable

PROJECT_ROOT = Path(__file__).resolve().parents[3]
VERSION_FILE = PROJECT_ROOT / "VERSION"


def read_app_version() -> str:
    env_version = os.getenv("APP_VERSION", "").strip()
    if env_version:
        return env_version

    try:
        file_version = VERSION_FILE.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return "v0.0.0"

    return file_version or "v0.0.0"


def env_flag(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def env_non_negative_int(name: str, default: int = 0) -> int:
    try:
        parsed = int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default
    return parsed if parsed >= 0 else default


def _env_or_derived(
    name: str,
    derive: Callable[[], object],
    cast: Callable[[str], object],
) -> object:
    """Read ``name`` from the environment, else derive it from other settings."""
    raw = os.getenv(name)
    return cast(raw) if raw is not None else derive()


# Formulas for settings derived from other settings. Each lambda is looked up
# by name in module globals when called, so it is safe to reference a setting
# defined later in this file as long as it exists by the time the formula
# actually runs (both at import, via ``_env_or_derived``, and later, via
# ``recompute_derived``). This is the single source of truth for each
# formula — do not re-derive the value inline elsewhere.
DERIVED_CONFIG_NAMES: dict[str, Callable[[], object]] = {
    "MAX_UPSTREAM_IMAGE_BYTES_PER_TASK_MB": lambda: max(1, MAX_FILE_SIZE_MB),
    "UPSTREAM_MEMORY_BUDGET_MB": lambda: max(MAX_UPSTREAM_IMAGE_BYTES_PER_TASK_MB, 256),
    "MAX_PENDING_EDIT_SOURCE_MB": lambda: max(0, MAX_FILE_SIZE_MB * 4),
    "IMPORT_ARCHIVE_MAX_MB": lambda: MAX_FILE_SIZE_MB * 20,
    "IMPORT_TEMP_RESERVATION_MAX_MB": lambda: max(1, IMPORT_ARCHIVE_MAX_MB * 2),
    "DB_EXECUTOR_WORKERS": lambda: max(4, MAX_ACTIVE_GENERATE_JOBS // 2 + 2),
    "AI_ASSISTANT_MAX_CONCURRENCY": lambda: max(1, MAX_ACTIVE_GENERATE_JOBS),
    "IMAGE_JOB_UNIT_LEASE_RENEW_SECONDS": lambda: max(
        5.0, IMAGE_JOB_UNIT_LEASE_SECONDS / 3
    ),
}


def _clamp_lease_renew(value: float) -> float:
    """Keep the image-unit lease renewal cadence below half the lease."""
    lease = float(IMAGE_JOB_UNIT_LEASE_SECONDS)
    renew = float(value)
    if renew < 5.0:
        renew = min(5.0, lease / 4)
    if lease > 0 and renew >= lease / 2:
        renew = lease / 4
    return renew


def _validate_github_repo(value: str) -> str:
    val = value.strip()
    if not val:
        return ""
    if not re.match(r"^[a-zA-Z0-9_.-]+/[a-zA-Z0-9_.-]+$", val):
        raise ValueError(f"Invalid GITHUB_REPO format: '{val}'. Expected 'owner/repo'.")
    return val


DEFAULT_API_URL = os.getenv("DEFAULT_API_URL", "")
DEFAULT_API_KEY = os.getenv("DEFAULT_API_KEY", "")
DEFAULT_API_PATH = os.getenv("DEFAULT_API_PATH", "/v1/images/generations")
DEFAULT_RESPONSES_MODEL = os.getenv("DEFAULT_RESPONSES_MODEL", "gpt-5.4")
DEFAULT_UPSTREAM_SOCKS5_PROXY = os.getenv("DEFAULT_UPSTREAM_SOCKS5_PROXY", "").strip()
AIOHTTP_CONNECTION_LIMIT = max(1, int(os.getenv("AIOHTTP_CONNECTION_LIMIT", "100")))
AIOHTTP_CONNECTION_LIMIT_PER_HOST = max(
    0,
    int(os.getenv("AIOHTTP_CONNECTION_LIMIT_PER_HOST", "20")),
)
ALLOW_PLAINTEXT_SECRETS = env_flag("ALLOW_PLAINTEXT_SECRETS")
SECRET_REGISTRY_JSON = os.getenv("SECRET_REGISTRY_JSON", "").strip()
GITHUB_REPO = _validate_github_repo(os.getenv("GITHUB_REPO", "Z1rconium/gpt-image-linux"))
ENABLE_VERSION_CHECK = env_flag("ENABLE_VERSION_CHECK", "true")
VERSION_CHECK_TIMEOUT_SECONDS = float(os.getenv("VERSION_CHECK_TIMEOUT_SECONDS", "3"))
VERSION_CHECK_BRANCH = os.getenv("VERSION_CHECK_BRANCH", "main").strip() or "main"
VERSION_CHECK_CACHE_SECONDS = max(
    60,
    int(os.getenv("VERSION_CHECK_CACHE_SECONDS", "600")),
)
ENABLE_METRICS = env_flag("ENABLE_METRICS")
SLOW_GALLERY_QUERY_MS = max(1.0, float(os.getenv("SLOW_GALLERY_QUERY_MS", "200")))
ENABLE_NGINX_ACCEL_REDIRECT = env_flag("ENABLE_NGINX_ACCEL_REDIRECT")
PUBLIC_IMAGE_BASE_URL = os.getenv("PUBLIC_IMAGE_BASE_URL", "").strip().rstrip("/")
PUBLIC_THUMBNAIL_BASE_URL = os.getenv("PUBLIC_THUMBNAIL_BASE_URL", "").strip().rstrip("/")
CDN_SIGNING_SECRET = os.getenv("CDN_SIGNING_SECRET", "").strip()
CDN_URL_TTL_SECONDS = max(30, min(3600, int(os.getenv("CDN_URL_TTL_SECONDS", "300"))))
ACCESS_KEY = os.getenv("ACCESS_KEY", "").strip()
ALLOW_UNAUTHENTICATED = env_flag("ALLOW_UNAUTHENTICATED")
ACCESS_KEY_SESSION_MINUTES = 180
ACCESS_KEY_COOKIE_NAME = os.getenv("ACCESS_KEY_COOKIE_NAME", "__Host-gpt_image_access")
ACCESS_COOKIE_SECURE = env_flag("ACCESS_COOKIE_SECURE", "true")
ACCESS_MAX_FAILURES = int(os.getenv("ACCESS_MAX_FAILURES", "5"))
ACCESS_LOCKOUT_SECONDS = int(os.getenv("ACCESS_LOCKOUT_SECONDS", "300"))
TURNSTILE_ENABLED = env_flag("TURNSTILE_ENABLED")
TURNSTILE_SITE_KEY = os.getenv("TURNSTILE_SITE_KEY", "").strip()
TURNSTILE_SECRET_KEY = os.getenv("TURNSTILE_SECRET_KEY", "").strip()
TURNSTILE_VERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"
TURNSTILE_TIMEOUT_SECONDS = max(1.0, float(os.getenv("TURNSTILE_TIMEOUT_SECONDS", "5")))
IP_ALLOWLIST = os.getenv("IP_ALLOWLIST", "")
TRUST_PROXY_HEADERS = env_flag("TRUST_PROXY_HEADERS")
TRUSTED_PROXY_IPS = os.getenv("TRUSTED_PROXY_IPS", "").strip()
PUBLIC_ORIGIN = os.getenv("PUBLIC_ORIGIN", "").strip()
ALLOWED_HOSTS = os.getenv("ALLOWED_HOSTS", "").strip()
CSRF_ORIGIN_CHECK_ENABLED = env_flag("CSRF_ORIGIN_CHECK_ENABLED", "true")
UPSTREAM_HOST_ALLOWLIST = os.getenv("UPSTREAM_HOST_ALLOWLIST", "").strip()
UPSTREAM_PROXY_HOST_ALLOWLIST = os.getenv("UPSTREAM_PROXY_HOST_ALLOWLIST", "").strip()
WEBHOOK_HOST_ALLOWLIST = os.getenv("WEBHOOK_HOST_ALLOWLIST", "").strip()
WEBHOOK_SIGNING_SECRET = os.getenv("WEBHOOK_SIGNING_SECRET", "").strip()
WEBHOOK_TIMEOUT_SECONDS = float(os.getenv("WEBHOOK_TIMEOUT_SECONDS", "5"))
WEBHOOK_MAX_ATTEMPTS = int(os.getenv("WEBHOOK_MAX_ATTEMPTS", "3"))
MAX_FILE_SIZE_MB = int(os.getenv("MAX_FILE_SIZE_MB", "50"))
NODEIMAGE_UPLOAD_CONCURRENCY = max(1, int(os.getenv("NODEIMAGE_UPLOAD_CONCURRENCY", "4")))
MAX_JSON_BODY_MB = max(1, int(os.getenv("MAX_JSON_BODY_MB", "1")))
MAX_UPSTREAM_JSON_MB = max(1, int(os.getenv("MAX_UPSTREAM_JSON_MB", "128")))
MAX_UPSTREAM_IMAGE_BYTES_PER_TASK_MB = _env_or_derived(
    "MAX_UPSTREAM_IMAGE_BYTES_PER_TASK_MB",
    DERIVED_CONFIG_NAMES["MAX_UPSTREAM_IMAGE_BYTES_PER_TASK_MB"],
    lambda raw: max(1, int(raw)),
)
UPSTREAM_MEMORY_BUDGET_MB = _env_or_derived(
    "UPSTREAM_MEMORY_BUDGET_MB",
    DERIVED_CONFIG_NAMES["UPSTREAM_MEMORY_BUDGET_MB"],
    lambda raw: max(MAX_UPSTREAM_IMAGE_BYTES_PER_TASK_MB, int(raw)),
)
MAX_IMAGE_PIXELS = max(1, int(os.getenv("MAX_IMAGE_PIXELS", "100000000")))
IMAGE_CPU_CONCURRENCY = max(1, int(os.getenv("IMAGE_CPU_CONCURRENCY", "2")))
FILE_IO_CONCURRENCY = max(1, int(os.getenv("FILE_IO_CONCURRENCY", "4")))
MAX_ACTIVE_GENERATE_JOBS = max(1, int(os.getenv("MAX_ACTIVE_GENERATE_JOBS", "2")))
# The DB executor runs short polling reads and the occasional critical write
# (claim/renew/complete/fail/finalize/cancel). A critical write can occupy one
# worker for up to the critical busy budget, so size the pool from generation
# concurrency rather than a fixed constant.
DB_EXECUTOR_WORKERS = _env_or_derived(
    "DB_EXECUTOR_WORKERS",
    DERIVED_CONFIG_NAMES["DB_EXECUTOR_WORKERS"],
    lambda raw: max(1, int(raw)),
)
SQLITE_BUSY_TIMEOUT_MS = max(10, int(os.getenv("SQLITE_BUSY_TIMEOUT_MS", "250")))
SQLITE_BUSY_RETRY_ATTEMPTS = max(0, int(os.getenv("SQLITE_BUSY_RETRY_ATTEMPTS", "5")))
SQLITE_BUSY_RETRY_BASE_MS = max(1, int(os.getenv("SQLITE_BUSY_RETRY_BASE_MS", "20")))
# Critical write paths use a much larger busy budget than polling reads: losing
# a terminal write strands a unit in `running` (D1/D2), so a 2s lock wait with
# jittered retries is preferable to giving up after the ~2s polling budget.
SQLITE_CRITICAL_BUSY_TIMEOUT_MS = max(
    10, int(os.getenv("SQLITE_CRITICAL_BUSY_TIMEOUT_MS", "2000"))
)
SQLITE_CRITICAL_BUSY_RETRY_ATTEMPTS = max(
    0, int(os.getenv("SQLITE_CRITICAL_BUSY_RETRY_ATTEMPTS", "6"))
)
# Write transactions held longer than this are logged with their call label.
SQLITE_SLOW_TXN_WARN_MS = max(1, int(os.getenv("SQLITE_SLOW_TXN_WARN_MS", "500")))
# Page cache per connection. SQLite's default (~2MB) is small for a gallery
# database that keyset-scans tens of thousands of rows per page.
SQLITE_CACHE_SIZE_MB = max(1, int(os.getenv("SQLITE_CACHE_SIZE_MB", "16")))
# Memory-mapped I/O window; 0 disables mmap.
SQLITE_MMAP_SIZE_MB = max(0, int(os.getenv("SQLITE_MMAP_SIZE_MB", "256")))
# Where transient sorts/aggregates (filter-option rebuilds) are kept.
_SQLITE_TEMP_STORE_VALUES = {"DEFAULT", "FILE", "MEMORY", "0", "1", "2"}
SQLITE_TEMP_STORE = os.getenv("SQLITE_TEMP_STORE", "MEMORY").strip().upper()
if SQLITE_TEMP_STORE not in _SQLITE_TEMP_STORE_VALUES:
    SQLITE_TEMP_STORE = "MEMORY"
# WAL auto-checkpoint threshold in pages (SQLite's default is 1000); 0 disables.
SQLITE_WAL_AUTOCHECKPOINT_PAGES = max(
    0, int(os.getenv("SQLITE_WAL_AUTOCHECKPOINT_PAGES", "1000"))
)
# Interval for the background `PRAGMA optimize` refresh of planner statistics.
SQLITE_OPTIMIZE_INTERVAL_SECONDS = max(
    60, int(os.getenv("SQLITE_OPTIMIZE_INTERVAL_SECONDS", "300"))
)
IMAGE_JOB_PROGRESS_PERSIST_INTERVAL_SECONDS = max(
    0.1,
    float(os.getenv("IMAGE_JOB_PROGRESS_PERSIST_INTERVAL_SECONDS", "1")),
)
# A running unit persists progress every ~1s, but the parent row's persisted
# counts only need to track it at the parent persist cadence. The authoritative
# finalize always happens once the unit reaches a terminal state, so this only
# gates the interim aggregate read.
IMAGE_JOB_AGGREGATE_MIN_INTERVAL_SECONDS = max(
    0.1,
    float(os.getenv("IMAGE_JOB_AGGREGATE_MIN_INTERVAL_SECONDS", "5")),
)
RUNTIME_METRICS_REFRESH_SECONDS = max(
    5.0,
    # Also drives the stale in-memory generate-job reconcile (job_events.py);
    # keep this <= 2 * GENERATE_JOB_PERSIST_INTERVAL_SECONDS (10s) so that
    # window is checked at least once per staleness period.
    float(os.getenv("RUNTIME_METRICS_REFRESH_SECONDS", "10")),
)
EVENT_LOOP_LAG_SAMPLE_SECONDS = max(
    0.1,
    float(os.getenv("EVENT_LOOP_LAG_SAMPLE_SECONDS", "0.5")),
)
MAX_PENDING_EDIT_SOURCE_MB = _env_or_derived(
    "MAX_PENDING_EDIT_SOURCE_MB",
    DERIVED_CONFIG_NAMES["MAX_PENDING_EDIT_SOURCE_MB"],
    lambda raw: max(0, int(raw)),
)
IMPORT_ARCHIVE_MAX_MB = _env_or_derived(
    "IMPORT_ARCHIVE_MAX_MB",
    DERIVED_CONFIG_NAMES["IMPORT_ARCHIVE_MAX_MB"],
    int,
)
IMPORT_MAX_FILES = int(os.getenv("IMPORT_MAX_FILES", "500"))
IMPORT_MAX_UNCOMPRESSED_MB = int(os.getenv("IMPORT_MAX_UNCOMPRESSED_MB", "1024"))
IMPORT_MAX_METADATA_BYTES = int(os.getenv("IMPORT_MAX_METADATA_BYTES", str(2 * 1024 * 1024)))
IMPORT_MAX_ENTRIES = max(1, int(os.getenv("IMPORT_MAX_ENTRIES", "500")))
IMPORT_MAX_OUTPUT_MB = max(1, int(os.getenv("IMPORT_MAX_OUTPUT_MB", "1024")))
IMPORT_MAX_COMPRESSION_RATIO = float(os.getenv("IMPORT_MAX_COMPRESSION_RATIO", "20"))
IMPORT_TEMP_RESERVATION_MAX_MB = _env_or_derived(
    "IMPORT_TEMP_RESERVATION_MAX_MB",
    DERIVED_CONFIG_NAMES["IMPORT_TEMP_RESERVATION_MAX_MB"],
    lambda raw: max(1, int(raw)),
)
IMPORT_UPLOAD_RESERVATION_TTL_SECONDS = max(
    60,
    int(os.getenv("IMPORT_UPLOAD_RESERVATION_TTL_SECONDS", "1800")),
)
IMPORT_UPLOADS_PER_IP_PER_MINUTE = max(
    1,
    int(os.getenv("IMPORT_UPLOADS_PER_IP_PER_MINUTE", "3")),
)
# Granian worker processes. Each process runs its own image-unit dispatcher;
# the per-worker claim share below spreads a job's `n` units across them.
GRANIAN_WORKERS = max(1, int(os.getenv("GRANIAN_WORKERS", "1")))
MAX_QUEUED_GENERATE_JOBS = max(0, int(os.getenv("MAX_QUEUED_GENERATE_JOBS", "20")))
IMAGE_JOB_UNIT_LEASE_SECONDS = max(30, int(os.getenv("IMAGE_JOB_UNIT_LEASE_SECONDS", "120")))
# Lease renewal cadence. Defaults to lease/3 and is clamped below lease/2 so the
# renewal loop can always extend the lease before it can expire.
IMAGE_JOB_UNIT_LEASE_RENEW_SECONDS = _clamp_lease_renew(
    _env_or_derived(
        "IMAGE_JOB_UNIT_LEASE_RENEW_SECONDS",
        DERIVED_CONFIG_NAMES["IMAGE_JOB_UNIT_LEASE_RENEW_SECONDS"],
        float,
    )
)
# How many times an image unit may be claimed (each claim increments attempts)
# before an exhausted lease is interrupted instead of retried forever.
IMAGE_JOB_UNIT_MAX_ATTEMPTS = max(1, int(os.getenv("IMAGE_JOB_UNIT_MAX_ATTEMPTS", "2")))
IMAGE_JOB_UNIT_POLL_INTERVAL_SECONDS = max(
    0.1,
    float(os.getenv("IMAGE_JOB_UNIT_POLL_INTERVAL_SECONDS", "0.35")),
)
# SSE pollers query SQLite only as a cross-process fallback for job updates:
# same-process updates are pushed by the in-process job event bus. They start
# at the base poll interval and double the delay while nothing changes, capped
# here, so an idle-but-connected UI does not hold a DB worker at a fixed rate.
SSE_IDLE_BACKOFF_MAX_SECONDS = max(
    0.35,
    float(os.getenv("SSE_IDLE_BACKOFF_MAX_SECONDS", "2")),
)
IMAGES_DIR = os.getenv("IMAGES_DIR", "./images")
THUMBNAILS_DIR = os.getenv("THUMBNAILS_DIR", os.path.join(IMAGES_DIR, "thumbs"))
MASKS_DIR = os.getenv("MASKS_DIR", os.path.join(IMAGES_DIR, "masks"))
THUMBNAIL_MAX_SIDE = max(1, int(os.getenv("THUMBNAIL_MAX_SIDE", "512")))
THUMBNAIL_CPU_CONCURRENCY = max(1, int(os.getenv("THUMBNAIL_CPU_CONCURRENCY", "1")))
DATA_DIR = os.getenv("DATA_DIR", "./data")
DATABASE_FILE = os.getenv("DATABASE_FILE", os.path.join(DATA_DIR, "app.sqlite3"))
LOG_DIR = os.getenv("LOG_DIR", os.path.join(DATA_DIR, "logs"))
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").strip().upper() or "INFO"
LOG_RETENTION_HOURS = max(1, int(os.getenv("LOG_RETENTION_HOURS", "24")))

# ── SSE connection limits ────────────────────────────────────────
MAX_SSE_SUBSCRIBERS_GLOBAL = max(1, int(os.getenv("MAX_SSE_SUBSCRIBERS_GLOBAL", "200")))
MAX_SSE_SUBSCRIBERS_PER_IP = max(1, int(os.getenv("MAX_SSE_SUBSCRIBERS_PER_IP", "10")))
SSE_CONNECTION_TTL_SECONDS = max(60, int(os.getenv("SSE_CONNECTION_TTL_SECONDS", "3600")))

# ── Streaming partial-image preview ──────────────────────────────
# Bounds for the upstream `text/event-stream` response when stream=true.
STREAMING_MAX_FRAME_MB = max(1, int(os.getenv("STREAMING_MAX_FRAME_MB", "8")))
STREAMING_MAX_TOTAL_MB = max(
    STREAMING_MAX_FRAME_MB,
    int(os.getenv("STREAMING_MAX_TOTAL_MB", "64")),
)
# In-memory preview cache: one slot per (job_id, unit_index), holding only the
# most recent partial image. Bounds are defensive backstops, not expected to
# bind in normal operation.
PREVIEW_CACHE_MAX_ENTRY_MB = max(1, int(os.getenv("PREVIEW_CACHE_MAX_ENTRY_MB", "8")))
PREVIEW_CACHE_MAX_ENTRIES = max(1, int(os.getenv("PREVIEW_CACHE_MAX_ENTRIES", "500")))

# ── Prompt optimizer ────────────────────────────────────────────
PROMPT_OPTIMIZER_ENABLED = env_flag("PROMPT_OPTIMIZER_ENABLED")
PROMPT_OPTIMIZER_API_URL = os.getenv("PROMPT_OPTIMIZER_API_URL", "").strip()
PROMPT_OPTIMIZER_API_KEY = os.getenv("PROMPT_OPTIMIZER_API_KEY", "").strip()
PROMPT_OPTIMIZER_MODEL = os.getenv("PROMPT_OPTIMIZER_MODEL", "gpt-4o-mini").strip()
PROMPT_OPTIMIZER_TIMEOUT_SECONDS = max(
    1,
    int(os.getenv("PROMPT_OPTIMIZER_TIMEOUT_SECONDS", "60")),
)
PROMPT_OPTIMIZER_MAX_OUTPUT_CHARS = int(os.getenv("PROMPT_OPTIMIZER_MAX_OUTPUT_CHARS", "32000"))
PROMPT_OPTIMIZER_MAX_RESPONSE_MB = max(
    1,
    int(os.getenv("PROMPT_OPTIMIZER_MAX_RESPONSE_MB", "8")),
)
PROMPT_OPTIMIZER_HOST_ALLOWLIST = os.getenv("PROMPT_OPTIMIZER_HOST_ALLOWLIST", "").strip()

# ── AI Assistant ────────────────────────────────────────────────
AI_ASSISTANT_ENABLED = env_flag("AI_ASSISTANT_ENABLED", "true")
AI_ASSISTANT_VISION_MODEL = os.getenv("AI_ASSISTANT_VISION_MODEL", "gpt-4o-mini").strip()
AI_ASSISTANT_MAX_RESPONSE_MB = max(
    1,
    int(os.getenv("AI_ASSISTANT_MAX_RESPONSE_MB", "8")),
)
AI_ASSISTANT_MAX_CONCURRENCY = _env_or_derived(
    "AI_ASSISTANT_MAX_CONCURRENCY",
    DERIVED_CONFIG_NAMES["AI_ASSISTANT_MAX_CONCURRENCY"],
    lambda raw: max(1, int(raw)),
)
AI_ASSISTANT_BATCH_MAX_IMAGES = max(
    1,
    int(os.getenv("AI_ASSISTANT_BATCH_MAX_IMAGES", "200")),
)
AI_ASSISTANT_IMAGE_MAX_SIDE = max(
    256,
    int(os.getenv("AI_ASSISTANT_IMAGE_MAX_SIDE", "1024")),
)
AI_ASSISTANT_IMAGE_MAX_BYTES = max(
    65536,
    int(os.getenv("AI_ASSISTANT_IMAGE_MAX_BYTES", str(1024 * 1024))),
)

# ── Cloudflare R2 gallery backup ─────────────────────────────────
R2_BACKUP_ENABLED = env_flag("R2_BACKUP_ENABLED")
R2_ENDPOINT_URL = os.getenv("R2_ENDPOINT_URL", "").strip()
R2_ENDPOINT_HOST_ALLOWLIST = os.getenv("R2_ENDPOINT_HOST_ALLOWLIST", "").strip()
R2_BUCKET_NAME = os.getenv("R2_BUCKET_NAME", "").strip()
R2_REGION = os.getenv("R2_REGION", "auto").strip() or "auto"
R2_KEY_PREFIX = os.getenv("R2_KEY_PREFIX", "gallery/").strip()
R2_ACCESS_KEY_ID = os.getenv("R2_ACCESS_KEY_ID", "").strip()
R2_SECRET_ACCESS_KEY = os.getenv("R2_SECRET_ACCESS_KEY", "").strip()
R2_SYNC_INTERVAL_HOURS = env_non_negative_int("R2_SYNC_INTERVAL_HOURS", 0)
R2_SYNC_CONCURRENCY = max(1, int(os.getenv("R2_SYNC_CONCURRENCY", "4")))

# ── NodeImage gallery upload ────────────────────────────────────
NODEIMAGE_API_KEY = os.getenv("NODEIMAGE_API_KEY", "").strip()

# ── Image generation cost estimation ─────────────────────────────
# JSON object mapping model name -> {text_input_per_million, image_input_per_million,
# image_output_per_million} in USD. Overrides/extends the builtin rate table in
# backend/app/core/image_cost.py. See .env.example for the exact shape.
IMAGE_COST_RATES_JSON = os.getenv("IMAGE_COST_RATES_JSON", "").strip()


def per_worker_generate_limit() -> int:
    """Return the image-unit claim share for one Granian worker process.

    The SQL claim still enforces the global ``MAX_ACTIVE_GENERATE_JOBS`` cap;
    this only bounds how many units a single process's claim loop may hold, so
    an ``n>1`` job is spread across workers instead of being monopolized by the
    worker that accepted the request. Values are read at call time so tests and
    runtime config can override them.
    """

    workers = max(1, int(GRANIAN_WORKERS))
    return max(1, -(-int(MAX_ACTIVE_GENERATE_JOBS) // workers))


# Order matters when recomputing: a later entry may read an earlier derived
# value (formulas themselves live in ``DERIVED_CONFIG_NAMES`` above).
def recompute_derived(explicit: frozenset[str] = frozenset()) -> None:
    """Recompute the settings derived from others.

    A base value that changes at runtime (``MAX_FILE_SIZE_MB``, say) has to move
    the limits derived from it, or the process runs with limits that contradict
    each other. Names in ``explicit`` were configured on their own and keep their
    value.
    """
    for name, derive in DERIVED_CONFIG_NAMES.items():
        if name in explicit:
            continue
        globals()[name] = derive()
    globals()["IMAGE_JOB_UNIT_LEASE_RENEW_SECONDS"] = _clamp_lease_renew(
        IMAGE_JOB_UNIT_LEASE_RENEW_SECONDS
    )


def apply_overrides(
    values: dict[str, object],
    *,
    explicit: frozenset[str] = frozenset(),
) -> None:
    """Change settings after import. This is the only supported write path.

    Callers pass the values they want applied plus the names that were
    configured on their own; everything derived from the bases then follows.
    Unknown names are rejected so a typo cannot invent a setting.
    """
    if not values:
        return
    unknown = sorted(name for name in values if name not in globals())
    if unknown:
        raise KeyError(f"Unknown settings: {', '.join(unknown)}")
    globals().update(values)
    recompute_derived(explicit)
