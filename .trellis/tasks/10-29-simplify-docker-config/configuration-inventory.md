# Configuration inventory

Per-setting implementation decisions, based on the approved design. Retired constants retain their existing backend defaults; derived values follow their bases. Outbound host allowlists are removed. Rows in the database remain intact but cannot override retired values.

| Setting | Decision |
|---|---|
| `DEFAULT_API_URL` | retained |
| `DEFAULT_API_KEY` | retained |
| `DEFAULT_API_PATH` | retained |
| `DEFAULT_RESPONSES_MODEL` | retained |
| `DEFAULT_UPSTREAM_SOCKS5_PROXY` | retained |
| `AIOHTTP_CONNECTION_LIMIT` | retired |
| `AIOHTTP_CONNECTION_LIMIT_PER_HOST` | retired |
| `ALLOW_PLAINTEXT_SECRETS` | retained |
| `SECRET_REGISTRY_JSON` | retained |
| `ALLOW_LEGACY_ENV_REFS` | retained |
| `GITHUB_REPO` | retained |
| `ENABLE_VERSION_CHECK` | retained |
| `VERSION_CHECK_TIMEOUT_SECONDS` | retired |
| `VERSION_CHECK_BRANCH` | retired |
| `VERSION_CHECK_CACHE_SECONDS` | retired |
| `ENABLE_METRICS` | retained |
| `SLOW_GALLERY_QUERY_MS` | retired |
| `ENABLE_NGINX_ACCEL_REDIRECT` | retained |
| `PUBLIC_IMAGE_BASE_URL` | retained |
| `PUBLIC_THUMBNAIL_BASE_URL` | retained |
| `CDN_SIGNING_SECRET` | retained |
| `CDN_URL_TTL_SECONDS` | retained |
| `ACCESS_KEY` | retained |
| `ALLOW_UNAUTHENTICATED` | retained |
| `ACCESS_KEY_SESSION_MINUTES` | retained |
| `ACCESS_KEY_COOKIE_NAME` | retained |
| `ACCESS_COOKIE_SECURE` | retained |
| `ACCESS_MAX_FAILURES` | retained |
| `ACCESS_LOCKOUT_SECONDS` | retained |
| `ACCESS_GLOBAL_MAX_ATTEMPTS_PER_MINUTE` | retained |
| `TURNSTILE_ENABLED` | retained |
| `TURNSTILE_SITE_KEY` | retained |
| `TURNSTILE_SECRET_KEY` | retained |
| `TURNSTILE_VERIFY_URL` | retained |
| `TURNSTILE_TIMEOUT_SECONDS` | retained |
| `IP_ALLOWLIST` | retained |
| `TRUST_PROXY_HEADERS` | retained |
| `TRUSTED_PROXY_IPS` | retained |
| `PUBLIC_ORIGIN` | retained |
| `ALLOWED_HOSTS` | retained |
| `CSRF_ORIGIN_CHECK_ENABLED` | retained |
| `UPSTREAM_HOST_ALLOWLIST` | retired |
| `UPSTREAM_PROXY_HOST_ALLOWLIST` | retired |
| `UPSTREAM_USER_AGENT` | retained |
| `WEBHOOK_HOST_ALLOWLIST` | retired |
| `WEBHOOK_SIGNING_SECRET` | retained |
| `WEBHOOK_TIMEOUT_SECONDS` | retired |
| `WEBHOOK_MAX_ATTEMPTS` | retired |
| `MAX_FILE_SIZE_MB` | retained |
| `MASK_PASTE_BACK_DEFAULT` | retained |
| `NODEIMAGE_UPLOAD_CONCURRENCY` | retired |
| `MAX_JSON_BODY_MB` | retired |
| `REQUEST_BODY_IDLE_TIMEOUT_SECONDS` | retired |
| `REQUEST_BODY_TOTAL_TIMEOUT_SECONDS` | retired |
| `MAX_UPSTREAM_JSON_MB` | retired |
| `MAX_UPSTREAM_IMAGE_BYTES_PER_TASK_MB` | derived |
| `UPSTREAM_MEMORY_BUDGET_MB` | derived |
| `MAX_IMAGE_PIXELS` | retired |
| `IMAGE_CPU_CONCURRENCY` | retired |
| `VISION_PREVIEW_MEMORY_BUDGET_MB` | retired |
| `FILE_IO_CONCURRENCY` | retired |
| `MAX_ACTIVE_GENERATE_JOBS` | retained |
| `DB_EXECUTOR_WORKERS` | derived |
| `SQLITE_BUSY_TIMEOUT_MS` | retired |
| `SQLITE_BUSY_RETRY_ATTEMPTS` | retired |
| `SQLITE_BUSY_RETRY_BASE_MS` | retired |
| `SQLITE_CRITICAL_BUSY_TIMEOUT_MS` | retired |
| `SQLITE_CRITICAL_BUSY_RETRY_ATTEMPTS` | retired |
| `SQLITE_SLOW_TXN_WARN_MS` | retired |
| `SQLITE_CACHE_SIZE_MB` | retired |
| `SQLITE_MMAP_SIZE_MB` | retired |
| `SQLITE_TEMP_STORE` | retired |
| `SQLITE_WAL_AUTOCHECKPOINT_PAGES` | retired |
| `SQLITE_OPTIMIZE_INTERVAL_SECONDS` | retired |
| `IMAGE_JOB_PROGRESS_PERSIST_INTERVAL_SECONDS` | retired |
| `IMAGE_JOB_AGGREGATE_MIN_INTERVAL_SECONDS` | retired |
| `RUNTIME_METRICS_REFRESH_SECONDS` | retired |
| `EVENT_LOOP_LAG_SAMPLE_SECONDS` | retired |
| `MAX_PENDING_EDIT_SOURCE_MB` | derived |
| `IMPORT_ARCHIVE_MAX_MB` | derived |
| `IMPORT_MAX_FILES` | retired |
| `IMPORT_MAX_UNCOMPRESSED_MB` | retired |
| `IMPORT_MAX_METADATA_BYTES` | retired |
| `IMPORT_MAX_ENTRIES` | retired |
| `IMPORT_MAX_OUTPUT_MB` | retired |
| `IMPORT_MAX_COMPRESSION_RATIO` | retired |
| `IMPORT_TEMP_RESERVATION_MAX_MB` | derived |
| `IMPORT_UPLOAD_RESERVATION_TTL_SECONDS` | retired |
| `IMPORT_UPLOADS_PER_IP_PER_MINUTE` | retired |
| `GRANIAN_WORKERS` | retained |
| `MAX_QUEUED_GENERATE_JOBS` | retained |
| `IMAGE_JOB_UNIT_LEASE_SECONDS` | retired |
| `IMAGE_JOB_UNIT_LEASE_RENEW_SECONDS` | derived |
| `IMAGE_JOB_UNIT_MAX_ATTEMPTS` | retired |
| `IMAGE_JOB_UNIT_POLL_INTERVAL_SECONDS` | retired |
| `SSE_IDLE_BACKOFF_MAX_SECONDS` | retired |
| `IMAGES_DIR` | retained |
| `THUMBNAILS_DIR` | retained |
| `MASKS_DIR` | retained |
| `THUMBNAIL_MAX_SIDE` | retired |
| `THUMBNAIL_CPU_CONCURRENCY` | retired |
| `DATA_DIR` | retained |
| `DATABASE_FILE` | retained |
| `LOG_DIR` | retained |
| `LOG_LEVEL` | retained |
| `LOG_RETENTION_HOURS` | retained |
| `MAX_SSE_SUBSCRIBERS_GLOBAL` | retired |
| `MAX_SSE_SUBSCRIBERS_PER_IP` | retired |
| `SSE_CONNECTION_TTL_SECONDS` | retired |
| `STREAMING_MAX_FRAME_MB` | retired |
| `STREAMING_MAX_TOTAL_MB` | retired |
| `PREVIEW_CACHE_MAX_ENTRY_MB` | retired |
| `PREVIEW_CACHE_MAX_ENTRIES` | retired |
| `PROMPT_OPTIMIZER_ENABLED` | retained |
| `PROMPT_OPTIMIZER_API_URL` | retained |
| `PROMPT_OPTIMIZER_API_KEY` | retained |
| `PROMPT_OPTIMIZER_MODEL` | retained |
| `PROMPT_OPTIMIZER_TIMEOUT_SECONDS` | retained |
| `PROMPT_OPTIMIZER_MAX_OUTPUT_CHARS` | retired |
| `PROMPT_OPTIMIZER_MAX_RESPONSE_MB` | retired |
| `PROMPT_OPTIMIZER_HOST_ALLOWLIST` | retired |
| `AI_ASSISTANT_ENABLED` | retained |
| `AI_ASSISTANT_VISION_MODEL` | retained |
| `AI_ASSISTANT_MAX_RESPONSE_MB` | retired |
| `AI_ASSISTANT_MAX_CONCURRENCY` | derived |
| `AI_ASSISTANT_BATCH_MAX_IMAGES` | retired |
| `AI_ASSISTANT_IMAGE_MAX_SIDE` | retired |
| `AI_ASSISTANT_IMAGE_MAX_BYTES` | retired |
| `AGENT_MAX_CONVERSATIONS` | retired |
| `AGENT_MAX_TURNS_PER_CONVERSATION` | retired |
| `AGENT_MAX_USER_TEXT_CHARS` | retired |
| `AGENT_MAX_IMAGE_PROMPT_CHARS` | retired |
| `AGENT_MAX_ATTACHMENTS_PER_MESSAGE` | retired |
| `AGENT_MAX_IMAGES_PER_BATCH` | retired |
| `AGENT_MAX_IMAGES_PER_TURN` | retired |
| `AGENT_MAX_TOOL_ROUNDS_CEILING` | retired |
| `AGENT_MAX_HISTORY_IMAGES` | retired |
| `AGENT_IMAGE_JOB_TIMEOUT_SECONDS` | retired |
| `AGENT_TURN_LEASE_SECONDS` | retired |
| `AGENT_EVENT_RETENTION_SECONDS` | retired |
| `AGENT_MAX_ACTIVE_TURNS` | retired |
| `R2_BACKUP_ENABLED` | retained |
| `R2_ENDPOINT_URL` | retained |
| `R2_ENDPOINT_HOST_ALLOWLIST` | retired |
| `R2_BUCKET_NAME` | retained |
| `R2_REGION` | retained |
| `R2_KEY_PREFIX` | retained |
| `R2_ACCESS_KEY_ID` | retained |
| `R2_SECRET_ACCESS_KEY` | retained |
| `R2_SYNC_INTERVAL_HOURS` | retained |
| `R2_SYNC_CONCURRENCY` | retired |
| `NODEIMAGE_API_KEY` | retained |
| `IMAGE_COST_RATES_JSON` | retained |

| `APP_VERSION` | retained |
| `GPT_IMAGE_PANEL_IMAGE` | retained (Compose image selector) |
| `PYTHON_BASE_IMAGE` | retained (Docker build argument) |
| `NODE_BASE_IMAGE` | retained (Docker build argument) |
| `GRANIAN_RUNTIME_THREADS` | supported by Granian; omit from default example |
| `GRANIAN_LOOP` | supported by Granian; omit from default example |
| `GRANIAN_BACKPRESSURE` | supported by Granian; omit from default example |
| `GRANIAN_BACKLOG` | supported by Granian; omit from default example |
| `GRANIAN_STATIC_PATH_ROUTE` | supported by Granian; omit from default example |
| `GRANIAN_STATIC_PATH_MOUNT` | supported by Granian; omit from default example |
| `GRANIAN_STATIC_PATH_EXPIRES` | supported by Granian; omit from default example |

`APP_VERSION` remains a deployment input; `GPT_IMAGE_PANEL_IMAGE` remains a Compose image selector. Granian-native options other than `GRANIAN_WORKERS` remain supported by Granian but are omitted from the default example.
