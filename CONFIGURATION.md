# Configuration reference

Copy `.env.example` to `.env` for the common settings. Compose forwards only the names in the table when they are defined in the shell or the project `.env`; an absent name is omitted so the application or image supplies its default. A deliberately empty value is passed as empty and may be invalid for numeric settings. Shell values take precedence over the project `.env`. Restart containers after environment changes.

Web Settings and Overall Config can persist overrides for supported settings; persisted values may take precedence over environment values as described in the README. Secret values should be supplied privately. Custom environment variables referenced by `SECRET_REGISTRY_JSON` must also be explicitly added to the Compose service’s `environment` list.

| Environment variable | Purpose | Default when unset |
|---|---|---|
| `DEFAULT_API_URL` | Default API base URL. | empty |
| `DEFAULT_API_KEY` | Default API key. | empty |
| `DEFAULT_API_PATH` | Default upstream route. | `/v1/images/generations` |
| `DEFAULT_RESPONSES_MODEL` | Fallback Responses API model. | `gpt-5.4` |
| `DEFAULT_UPSTREAM_SOCKS5_PROXY` | Global SOCKS5 proxy. | empty |
| `APP_VERSION` | Version override shown by /api/version. | `VERSION file (fallback v0.0.0)` |
| `GITHUB_REPO` | GitHub owner/repo used for update checks. | `Z1rconium/gpt-image-linux` |
| `ENABLE_VERSION_CHECK` | Enable latest version checks. | `true` |
| `ENABLE_METRICS` | Enable /api/metrics. | `false` |
| `ENABLE_NGINX_ACCEL_REDIRECT` | Return X-Accel-Redirect for authorized image files when an nginx internal alias is in front. | `false` |
| `PUBLIC_IMAGE_BASE_URL` | Optional public/CDN base URL for gallery image bytes. | empty |
| `PUBLIC_THUMBNAIL_BASE_URL` | Optional public/CDN base URL for generated gallery thumbnail bytes. | empty |
| `GRANIAN_WORKERS` | Granian worker process count; each worker claims at most ceil(MAX_ACTIVE_GENERATE_JOBS / GRANIAN_WORKERS) image units. Set equal to the actual process count. | `1` |
| `ALLOW_PLAINTEXT_SECRETS` | Deprecated; web-managed literal secrets are disabled. | `false` |
| `ALLOW_LEGACY_ENV_REFS` | Deprecated compatibility switch: allow ${ENV_VAR} references that are not declared in SECRET_REGISTRY_JSON. | `false` |
| `PROMPT_OPTIMIZER_ENABLED` | Enable prompt optimizer. | `false` |
| `PROMPT_OPTIMIZER_API_URL` | Optimizer endpoint URL. | empty |
| `PROMPT_OPTIMIZER_API_KEY` | Optimizer API key. | empty |
| `PROMPT_OPTIMIZER_MODEL` | Optimizer model. | `gpt-4o-mini` |
| `PROMPT_OPTIMIZER_TIMEOUT_SECONDS` | Optimizer timeout. | `60` |
| `AI_ASSISTANT_ENABLED` | Enable AI Assistant tools. | `true` |
| `AI_ASSISTANT_VISION_MODEL` | Assistant vision model. | `gpt-4o-mini` |
| `R2_BACKUP_ENABLED` | Enable R2 backup. | `false` |
| `R2_ENDPOINT_URL` | R2 endpoint URL. | empty |
| `R2_BUCKET_NAME` | R2 bucket name. | empty |
| `R2_REGION` | R2 region. | `auto` |
| `R2_KEY_PREFIX` | R2 key prefix. | `gallery/` |
| `R2_SYNC_INTERVAL_HOURS` | Scheduled R2 sync interval in hours; 0 disables automatic sync. | `0` |
| `R2_ACCESS_KEY_ID` | R2 access key ID. | empty |
| `R2_SECRET_ACCESS_KEY` | R2 secret access key. | empty |
| `NODEIMAGE_API_KEY` | NodeImage API key. | empty |
| `ACCESS_KEY` | Access gate key. | empty |
| `ALLOW_UNAUTHENTICATED` | Allow startup without access key; logs a warning because non-health APIs are unauthenticated. | `false` |
| `ACCESS_KEY_COOKIE_NAME` | Access cookie name. | `__Host-gpt_image_access` |
| `ACCESS_COOKIE_SECURE` | Set Secure on access cookie. | `true` |
| `ACCESS_MAX_FAILURES` | Failed access attempts before lockout. | `5` |
| `ACCESS_LOCKOUT_SECONDS` | Access lockout duration. | `300` |
| `ACCESS_GLOBAL_MAX_ATTEMPTS_PER_MINUTE` | Process-wide ceiling on /api/access attempts per rolling minute; 0 disables. | `120` |
| `TURNSTILE_ENABLED` | Enable Cloudflare Turnstile for the access gate. | `false` |
| `TURNSTILE_SITE_KEY` | Turnstile public site key. | empty |
| `TURNSTILE_SECRET_KEY` | Turnstile secret key. | empty |
| `IP_ALLOWLIST` | Client IP/CIDR allowlist. | empty |
| `TRUST_PROXY_HEADERS` | Trust reverse-proxy headers. | `false` |
| `TRUSTED_PROXY_IPS` | Trusted reverse proxy IP/CIDR list. | empty |
| `PUBLIC_ORIGIN` | Canonical browser origin. | empty |
| `ALLOWED_HOSTS` | Host / X-Forwarded-Host allowlist. | empty |
| `CSRF_ORIGIN_CHECK_ENABLED` | Reject unsafe requests without valid Origin, Referer, or same-origin fetch metadata. | `true` |
| `UPSTREAM_USER_AGENT` | User-Agent sent to the upstream service. | `opencode` |
| `WEBHOOK_SIGNING_SECRET` | Webhook signing secret. | empty |
| `MAX_FILE_SIZE_MB` | Max uploaded/downloaded image size. | `50` |
| `MASK_PASTE_BACK_DEFAULT` | Paste masked edit results onto the primary image by default. | `true` |
| `MAX_ACTIVE_GENERATE_JOBS` | Concurrent generation/edit jobs. | `2` |
| `MAX_QUEUED_GENERATE_JOBS` | Additional queued jobs before 429. | `20` |
| `IMAGES_DIR` | Generated image directory. | `./images` |
| `THUMBNAILS_DIR` | Thumbnail directory. | `IMAGES_DIR/thumbs` |
| `MASKS_DIR` | Directory containing stored masks. | `IMAGES_DIR/masks` |
| `DATA_DIR` | Runtime data directory. | `./data` |
| `DATABASE_FILE` | SQLite database path. | `DATA_DIR/app.sqlite3` |
| `SECRET_REGISTRY_JSON` | JSON secret registry mapping IDs to environment references and purpose/origin bindings. | empty |
| `CDN_SIGNING_SECRET` | Signing key for CDN image URLs. | empty |
| `CDN_URL_TTL_SECONDS` | Signed CDN URL lifetime in seconds, clamped to 30–3600. | `300` |
| `IMAGE_COST_RATES_JSON` | JSON model-to-rate mapping for cost estimates. | empty |
| `LOG_DIR` | Application log directory. | `DATA_DIR/logs` |
| `LOG_LEVEL` | Application logging level. | `INFO` |
| `LOG_RETENTION_HOURS` | Log retention in hours. | `24` |
| `TURNSTILE_TIMEOUT_SECONDS` | Turnstile verification timeout in seconds. | `5` |

For custom cost estimates, rates are USD per million tokens, for example:

```json
{"my-model": {"text_input_per_million": 5, "image_input_per_million": 10, "image_output_per_million": 40}}
```

## Image and build inputs

- `GPT_IMAGE_PANEL_IMAGE`: Compose image selector; defaults to `ghcr.io/z1rconium/gpt-image-linux:v1.8.1`.
- Docker build arguments `PYTHON_BASE_IMAGE` and `NODE_BASE_IMAGE` override pinned Python 3.12 slim and Node 24 Alpine images respectively; see `Dockerfile` for exact digests. `APP_VERSION` and `VCS_REF` build arguments set image labels. Docker Bake also accepts `IMAGE` and `TAG`; see `docker-bake.hcl`.

## Granian native options

The image sets `GRANIAN_INTERFACE=asgi`, `GRANIAN_HOST=0.0.0.0`, `GRANIAN_PORT=9090`, `GRANIAN_LOOP=uvloop`, `GRANIAN_RUNTIME_THREADS=2`, `GRANIAN_RUNTIME_MODE=auto`, `GRANIAN_BACKPRESSURE=100`, `GRANIAN_BACKLOG=2048`, `GRANIAN_STATIC_PATH_ROUTE=/_app/immutable`, `GRANIAN_STATIC_PATH_MOUNT=/app/frontend/build/_app/immutable`, and `GRANIAN_STATIC_PATH_EXPIRES=31536000`. Granian supports its own environment options; to override any not listed in the table, explicitly add its name to the Compose service’s `environment` list. Keep `GRANIAN_WORKERS` consistent with the actual worker count.

Outbound host allowlists and internal tuning environment overrides were removed. Public-network URL validation and credential purpose/origin binding still apply. Retired database rows are preserved but ignored.
