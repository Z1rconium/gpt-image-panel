<div align="center">
  <br />
  <img src="frontend/static/favicon.svg" alt="GPT Image Panel logo" width="128" height="128" />

  <h1>GPT Image Panel</h1>

  <hr />

  <p>
    <strong>Self-hosted GPT-compatible image generation and editing panel.</strong>
  </p>

  <p>
    <a href="#english">English</a> ·
    <a href="./README.zh-CN.md">简体中文</a> ·
    <a href="./README.zh-TW.md">繁體中文</a>
  </p>

  <p>
    <img alt="CI passing" src="https://img.shields.io/badge/CI-passing-2cc653?logo=github&logoColor=white" />
    <img alt="Release v1.7.12" src="https://img.shields.io/badge/release-v1.7.12-0e8dcc" />
    <img alt="Python 3.11+" src="https://img.shields.io/badge/Python-3.11+-3776AB?logo=python&logoColor=white" />
    <img alt="Node.js 24" src="https://img.shields.io/badge/Node.js-24-339933?logo=node.js&logoColor=white" />
    <img alt="FastAPI 0.115+" src="https://img.shields.io/badge/FastAPI-0.115+-009688?logo=fastapi&logoColor=white" />
    <img alt="SvelteKit 2" src="https://img.shields.io/badge/SvelteKit-2-FF3E00?logo=svelte&logoColor=white" />
    <img alt="License CC BY-NC 4.0" src="https://img.shields.io/badge/License-CC_BY--NC_4.0-6f42c1" />
    <img alt="Image GHCR" src="https://img.shields.io/badge/GHCR-gpt--image--linux-1f6f8b?logo=github&logoColor=white" />
  </p>
</div>

<a id="english"></a>

## Overview

GPT Image Panel is a lightweight web UI for image generation, image editing, gallery management, and local persistence. It connects to an external GPT-compatible image API and stores images plus metadata on your own server.

This project is only a self-hosted control panel. It does not provide, proxy, resell, or modify any upstream model/API service. Generation capability, billing, account permissions, content policy, and model behavior all come from the upstream provider you configure.

## Features

- Image generation through `/v1/images/generations`, `/v1/responses`, or OpenAI-compatible `/v1/chat/completions`.
- Image editing through `/v1/images/edits`, using uploaded references or gallery images as sources, with a built-in mask editor (brush/eraser/shape/lasso, zoom, feathering, mask import, auto gap-fill/edge-smoothing). Masks persist and restore on retry; results paste back onto the untouched original behind a color-drift guard.
- API presets with base URL/path/key, default model, response format, health checks, SOCKS5 proxy, webhook, and env-ref secret support.
- Prompt helper tags, reusable prompt snippets, an optional server-side prompt optimizer, and an AI Assistant subsystem for prompt rewrites/checks/variants, parameter recommendations, job diagnosis, edit planning, and gallery image analysis.
- Agent conversation mode: a multi-turn chat where the model plans and creates images through the existing job queue (batches of images, dependent follow-up rounds, edits of earlier images via `@` references), with server-side history, resumable streaming, and every image saved to the gallery. Requires an AI Assistant endpoint whose model supports function calling.
- SQLite-backed job queue with SSE progress, cancellation, retry/reuse, persisted history, stage timing metadata, and shared generation/edit concurrency limits.
- Optional streaming partial-image previews for `/v1/images/generations`, `/v1/images/edits`, and `/v1/responses` with 1–10 images, delivered over SSE as the upstream generates. Job history shows per-job token usage and an estimated USD cost whenever the upstream reports `usage` for a model with a configured rate.
- Local gallery with cursor pagination, search/filtering, favorites, lightbox navigation, selection-token batch actions, ZIP import/export, thumbnails, byte-size metadata, and async export/import jobs.
- Optional Cloudflare R2 gallery backup sync; local SQLite/images remain the source of truth.
- Access-key gate, IP/Host allowlists, trusted proxy-header support, CSRF origin checks, CSP nonce injection, version checks, and optional JSON/Prometheus metrics.

## Architecture

- Backend: FastAPI under `backend/app/`; ASGI entrypoint is `backend.app.main:app`.
- Frontend: SvelteKit static app under `frontend/`; production backend serves `frontend/build/`.
- Runtime storage: generated images under `images/`, thumbnails under `images/thumbs/`, SQLite data under `data/app.sqlite3`, and logs under `data/logs/` by default.
- Multi-worker coordination: queued jobs, background leases, SSE slots, and scheduler ownership use SQLite leases. Image/thumbnail file writes use process-local locks and tolerate cross-process races through UUID filenames, atomic `Path.replace()`, and orphan-file GC TTL cleanup.
- Key modules: public API routing in `backend/app/api/` (contract in `contract_app.py`), DTOs in `schemas/`, persistence in `repositories/`, upstream clients in `integrations/`, config in `core/settings.py` and `core/overall_config.py`.

## Tech Stack

Python 3.11+, FastAPI, Granian, aiohttp (+aiohttp-socks), boto3, SQLite, Pydantic v2, Pillow, python-multipart, zipstream-ng · SvelteKit, TypeScript, Tailwind CSS 4 · Playwright, pytest. Browser baseline: Chrome 111+, Safari 16.4+, Firefox 128+.

## Project Structure

```text
backend/
  app/
    api/            # public API routing (contract_app.py)
    core/           # settings, overall config
    integrations/   # upstream API clients
    repositories/   # persistence, SQLite coordination
    runtime/        # blocking/concurrency helpers
    schemas/        # DTOs
    services/       # orchestration
  tests/
frontend/
  src/lib/          # reusable frontend code
  src/routes/       # SvelteKit routes
  static/           # favicon
  tests/
deploy/nginx.conf
Dockerfile  docker-compose.yml  .env.example
requirements.txt  requirements.lock  package.json
images/  data/      # runtime output (generated)
```

## Quick Start

### Docker Compose

```bash
cp .env.example .env
# edit .env: set ACCESS_KEY and any default upstream API values you want
# this example uses plain HTTP on loopback, so disable Secure cookies
ACCESS_COOKIE_SECURE=false docker-compose up -d --force-recreate
```

Open `http://127.0.0.1:9090`.

This local HTTP example requires `ACCESS_COOKIE_SECURE=false`; keep it `true` when serving the panel over HTTPS. By default, `ACCESS_KEY` is required. For local-only testing, unset `ACCESS_KEY` and set `ALLOW_UNAUTHENTICATED=true`; this makes every non-health API route accessible. To additionally protect the unlock flow with Cloudflare Turnstile, set `TURNSTILE_ENABLED=true` plus your `TURNSTILE_SITE_KEY` / `TURNSTILE_SECRET_KEY` from the Cloudflare dashboard; the verification widget then appears below the Unlock button and a valid token is required for every access-key login.

### Docker

```bash
docker build -t gpt-image-panel .
docker run -d --name gpt-image-panel \
  -p 127.0.0.1:9090:9090 \
  -e ACCESS_KEY=change-me \
  -e ACCESS_COOKIE_SECURE=false \
  -v $(pwd)/images:/app/images \
  -v $(pwd)/data:/app/data \
  gpt-image-panel
```

If Docker Hub is slow or blocked:

```bash
docker build \
  --build-arg PYTHON_BASE_IMAGE=docker.m.daocloud.io/library/python:3.12-slim \
  --build-arg NODE_BASE_IMAGE=docker.m.daocloud.io/library/node:24-alpine \
  -t gpt-image-panel .
```

The image installs Python dependencies from the hashed `requirements.lock` (generated from `requirements.txt` with `uv pip compile`), so every build and both architectures get identical versions.

### Caddy Reverse Proxy

When Caddy and the application run on the same host, use a placeholder hostname and keep port `9090` bound to loopback:

```caddyfile
panel.example.com {
    reverse_proxy 127.0.0.1:9090
}
```

For an HTTPS deployment, set the matching application origin and Host allowlist in `.env`:

```dotenv
PUBLIC_ORIGIN=https://panel.example.com
ALLOWED_HOSTS=panel.example.com
ACCESS_COOKIE_SECURE=true
```

The basic proxy is recommended when there is only one upstream. If active health checks are required, first confirm that `/health` returns `200` with the same `Host` header, then use the plural `health_headers` block:

```bash
curl -i -H 'Host: panel.example.com' http://127.0.0.1:9090/health
```

```caddyfile
panel.example.com {
    reverse_proxy 127.0.0.1:9090 {
        health_uri /health
        health_interval 15s
        health_timeout 3s
        health_status 200

        health_headers {
            Host panel.example.com
        }
    }
}
```

A health-check response outside the configured status range marks the upstream unhealthy. With only one upstream, this can make requests fail until the check succeeds again. Validate and reload Caddy after changes:

```bash
sudo caddy validate --config /etc/caddy/Caddyfile
sudo systemctl reload caddy
```

Caddy automatic HTTPS requires the hostname to resolve to the server and inbound ports `80` and `443` to be reachable. When using a CDN proxy such as Cloudflare, ensure its edge certificate explicitly covers the complete hostname, especially for multi-label subdomains; otherwise browsers may report `ERR_SSL_VERSION_OR_CIPHER_MISMATCH` before traffic reaches Caddy. If Caddy runs in a container, `127.0.0.1` refers to that container, so use the application service name or another reachable container-network address instead.

### Deployment Troubleshooting

1. **Container exits on startup with `SecretRegistryError: credentials require a non-empty startup host allowlist`** — `DEFAULT_API_KEY` is configured in `.env`, but `UPSTREAM_HOST_ALLOWLIST` is missing or empty. Add the raw hostname of `DEFAULT_API_URL` to `UPSTREAM_HOST_ALLOWLIST` in `.env` (e.g. `UPSTREAM_HOST_ALLOWLIST=api.openai.com` or `UPSTREAM_HOST_ALLOWLIST=cf.api.fan`).
2. **Browser reports `400 Bad Request: Host is not allowed`** — the incoming `Host` header does not match `ALLOWED_HOSTS` or `PUBLIC_ORIGIN` in `.env` (often due to domain typos or failing to recreate the container after editing `.env`). Ensure both match the exact domain, and always run `docker compose up -d --force-recreate` after modifying `.env`.
3. **Cannot log in or login loop under direct plain HTTP (no reverse proxy/SSL)** — by default `ACCESS_COOKIE_SECURE=true` requires HTTPS; browsers reject storing or sending `Secure` cookies over plain HTTP. For direct HTTP testing, set `ACCESS_COOKIE_SECURE=false` in `.env`. Switch back to `true` once behind an HTTPS reverse proxy.
4. **Clicking "Save Preset" in Web UI fails or drawer does not close** — saving literal plaintext API keys to SQLite via the UI is prohibited by default; set `ALLOW_PLAINTEXT_SECRETS=true` in `.env` and restart the container if you need to paste raw API keys directly into Web Settings. If the preset API URL was modified, ensure its domain is also included in `UPSTREAM_HOST_ALLOWLIST`.

### Local Development

Create a project-local Python 3.11+ virtual environment first. The `.venv` directory is local developer state and is not provided by the repository.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r backend/requirements-dev.txt
npm --prefix frontend install
npm run backend:dev
```

In another terminal:

```bash
npm run frontend:dev
```

Open `http://localhost:5173`. Vite proxies `/api` and `/health` to FastAPI at `127.0.0.1:9090`.

Production-style local smoke test:

```bash
npm run frontend:build
ALLOW_UNAUTHENTICATED=true .venv/bin/granian --interface asgi backend.app.main:app --host 127.0.0.1 --port 9090
```

## Configuration

Most runtime options live in `.env.example`. API presets, prompt optimizer, R2 backup, and selected app/runtime options can also be managed through Web Settings / Overall Config. Key variables:

| Variable | Purpose |
| --- | --- |
| `ACCESS_KEY` / `ALLOW_UNAUTHENTICATED` / `TURNSTILE_*` | Access gate key (required unless unset with `ALLOW_UNAUTHENTICATED=true`); `TURNSTILE_*` adds optional Cloudflare Turnstile verification. |
| `DEFAULT_API_URL` / `DEFAULT_API_KEY` / `DEFAULT_API_PATH` / `DEFAULT_RESPONSES_MODEL` | Default upstream preset. Prefer env refs such as `${OPENAI_API_KEY}` for keys. |
| `UPSTREAM_HOST_ALLOWLIST` | Required whenever an upstream API key is configured; lists allowed upstream hostnames. |
| `PUBLIC_ORIGIN` / `ALLOWED_HOSTS` | Reverse-proxy Host/CSRF hardening; must match the deployed domain. |
| `MAX_ACTIVE_GENERATE_JOBS` / `MAX_QUEUED_GENERATE_JOBS` | Global running image-unit limit and queue capacity (new jobs return `429` beyond it). |
| `IMAGE_JOB_UNIT_LEASE_SECONDS` / `IMAGE_JOB_UNIT_LEASE_RENEW_SECONDS` / `IMAGE_JOB_UNIT_MAX_ATTEMPTS` | SQLite claim lease for a running image unit (crash-detection latency), its renewal cadence, and max claim attempts. |
| `GRANIAN_WORKERS` / `GRANIAN_*` | Worker process count and production runtime tuning; set equal to the actual process count. |
| `VISION_PREVIEW_MEMORY_BUDGET_MB` | Vision-preview decode admission budget: **256 MiB per process** by default, range **32–16384**. Agent previews exceeding the conservative decoded-memory estimate are skipped; text references, original files, generation and editing remain available. |
| `IMAGE_CPU_CONCURRENCY` / `FILE_IO_CONCURRENCY` / `UPSTREAM_MEMORY_BUDGET_MB` / `MAX_UPSTREAM_IMAGE_BYTES_PER_TASK_MB` | Bounded decode, file-I/O concurrency, and upstream image memory budgets. |
| `DB_EXECUTOR_WORKERS` / `SQLITE_BUSY_*` / `SQLITE_CRITICAL_BUSY_*` / `SQLITE_SLOW_TXN_WARN_MS` | SQLite executor size and busy-retry budgets; critical claim/lease/finalization writes get a larger budget. |
| `IMAGES_DIR` / `MASKS_DIR` / `THUMBNAILS_DIR` / `THUMBNAIL_*` / `DATA_DIR` / `DATABASE_FILE` / `LOG_DIR` / `LOG_LEVEL` / `LOG_RETENTION_HOURS` | Storage paths, thumbnail controls, and logging (rotated files retained 24h by default). |
| `MASK_PASTE_BACK_DEFAULT` | Default for pasting a masked edit's result back onto the untouched primary image; overridable per edit request. |
| `MAX_SSE_SUBSCRIBERS_GLOBAL` / `MAX_SSE_SUBSCRIBERS_PER_IP` / `SSE_CONNECTION_TTL_SECONDS` | SSE slot limits and max connection lifetime. |
| `PROMPT_OPTIMIZER_*` | Optional server-side prompt optimizer settings. |
| `AI_ASSISTANT_*` | AI Assistant is enabled by default (`AI_ASSISTANT_ENABLED=false` to disable); reuses `PROMPT_OPTIMIZER_*` for API URL/key/model/timeout/allowlist. |
| `AGENT_*` | Agent conversation-mode limits; the mode itself is switched on in Settings → AI Assistant and reuses the `PROMPT_OPTIMIZER_*` endpoint. |
| `R2_*` | Optional Cloudflare R2 gallery backup sync; custom endpoint hosts require `R2_ENDPOINT_HOST_ALLOWLIST`. |
| `NODEIMAGE_API_KEY` | Optional NodeImage API key for server-side Gallery uploads. |
| `IMAGE_COST_RATES_JSON` | Override/extend the builtin per-model USD rates used for cost estimates. |
| `APP_VERSION` / `GITHUB_REPO` / `ENABLE_VERSION_CHECK` / `VERSION_CHECK_CACHE_SECONDS` | Version reporting and latest-release checks. |
| `ENABLE_NGINX_ACCEL_REDIRECT` / `PUBLIC_IMAGE_BASE_URL` / `PUBLIC_THUMBNAIL_BASE_URL` | Optional nginx/CDN image byte serving. |
| `ENABLE_METRICS` | Enables JSON/Prometheus metrics endpoints. |
| `SECRET_REGISTRY_JSON` / `ALLOW_PLAINTEXT_SECRETS` / `ALLOW_LEGACY_ENV_REFS` | Secret handling: `${ENV_VAR}` references must be declared in `SECRET_REGISTRY_JSON`; literal secrets in SQLite require `ALLOW_PLAINTEXT_SECRETS=true`; `ALLOW_LEGACY_ENV_REFS=true` is a deprecated migration switch. |

Settings resolve in three layers: environment variables (read once at process start), Overall Config overrides persisted in SQLite via `/api/settings/overall-config` (hot-reloadable keys apply immediately, `restart_required` keys on next start), and names marked `exposed_in_settings` (API presets, prompt optimizer, AI assistant, R2, NodeImage), which are read from SQLite once saved through Web Settings — after that, the matching env var has no further effect. A handful of settings (e.g. `DB_EXECUTOR_WORKERS`, `AI_ASSISTANT_MAX_CONCURRENCY`, `IMAGE_JOB_UNIT_LEASE_RENEW_SECONDS`) are derived from another value and recompute automatically. Known limit: changing `DB_EXECUTOR_WORKERS`, `IMAGE_CPU_CONCURRENCY`, or `FILE_IO_CONCURRENCY` at runtime does not resize an already-created thread pool.

With `ENABLE_METRICS=true`, `/api/metrics` exposes diagnostics for sizing SQLite coordination: `sqlite.write_txn`, `sqlite.write_lock_wait_ms`, `sqlite.write_txn_hold_ms` (write-transaction rate and lock wait/hold times, p50–p99/max — watch the p95 as `MAX_ACTIVE_GENERATE_JOBS x GRANIAN_WORKERS` grows), `sqlite.busy`, `sqlite.busy_retries` (failed/retried lock attempts), and `image_jobs.lease_renewed`, `lease_lost`, `unit_reclaimed`, `unit_exhausted` (image-unit lease health; `lease_lost` should stay at zero).

## Usage

1. Open the panel.
2. Unlock with `ACCESS_KEY` if enabled.
3. Open Settings and create or select an API preset.
4. Set API base URL, API path, model, response format, and API key/env ref.
5. Optionally configure SOCKS5 proxy, webhook, prompt optimizer, AI Assistant, R2 backup, NodeImage upload, or Overall Config overrides.
6. Save the preset and run its health check if needed.
7. Generate images from a prompt, or upload/select source images and run edits.
8. Use Gallery for reuse, filtering, favorites, batch actions, import/export, and R2 sync.
9. Bookmark a preset shortcut with `/?apiUrl=https://api.example.com&apiModel=gpt-image-2`. Opening it offers to create a new preset with that URL and model; the confirmation banner shows the destination host prominently and warns to enter an API key only for a trusted host. Nothing is saved until you confirm, the API key stays empty, and the parameters are removed from the address bar. Only `https` URLs are accepted and credentials are never read from the URL. (`?model=` is the gallery filter, hence `apiModel`.)
10. Use **Export** to download the selected preset as a versioned JSON package (never containing the key), **Share link** to copy a `?preset=` link, and **Import** to preview a file, clipboard JSON, or shared link before applying it. Order presets with the drag handle or the arrow buttons; the order is stored in SQLite.
11. Optionally enable Agent mode in Settings → AI Assistant (pick a model that supports function calling), then switch to **Agent** in the header. Describe what you want, reference earlier images with `@` (for example `@round-1-image-1`), attach gallery images, and press Ctrl/Cmd+Enter. Use Stop to cancel a running reply; generated images appear in the gallery.

### Agent paths, Markdown, and optional search

Assistant replies support headings, lists, tables, code, and links. Raw HTML is escaped, external Markdown images stay text, and links allow only HTTP(S) URLs without credentials.

Use **Edit message → Send as new branch** or **Regenerate** to create a sibling of a historical turn. **Conversation path** restores any original or new path after reload. Switching paths does not create requests or stop running work; stop the active turn before editing or regenerating. Only ancestors on the executing path enter model history. Image IDs and `@round-N-image-M` references are stable; displayed rounds and `@第N轮图M` follow the current path. To reuse an image from another path, attach it explicitly from Gallery. Deleting Gallery images invalidates references on all paths; deleting a conversation keeps its Gallery images.

Web search is **off by default**. Under **Settings → AI Assistant**, declare that the actual endpoint/model supports Responses web search, then enable **Allow web search**. This requires the inherited Prompt Optimizer route to use `/v1/responses` and a compatible model; chat/completions continues with the existing function tools when search is off. Check your provider documentation before declaring support. Search-enabled incompatible configurations block submission (422 at the API); failures are shown and never automatically retried with search disabled.

Search progress and citations supplied by upstream annotations are saved with the turn. Inline numbers link to the same sources listed under **Sources**, including quoted spans; reloads, stream replay, and path switches restore them. Ordinary Markdown links do not become sources. Search shares tool-round, timeout, and cancellation limits, uses bounded branch history, and does not invent costs when usage is missing. Migrations 37/38 apply automatically at startup.

## GPT Image 2.5

The generation form and preset settings offer `gpt-image-2.5-flare` (fast everyday generation) and `gpt-image-2.5-sunburst` (precise editing). `gpt-image-2` remains the default; saved presets and custom model names are preserved. Choose **Custom model / snapshot** to pin either model to its `-2026-09-08` snapshot.

Use `/v1/images/generations` for generation. Uploading reference images or selecting a gallery image uses multipart `/v1/images/edits`. Both 2.5 variants support `auto`, `low`, `medium`, `high`, `xhigh`, and `max` quality. Earlier/custom models retain the existing quality options. Selecting a model that does not support the current quality resets it to `auto`.

- Prompts support up to 32,000 Unicode characters across generation, editing, assistants, and snippets (Prompt Optimizer shares this limit unless `PROMPT_OPTIMIZER_MAX_OUTPUT_CHARS` overrides it).
- Size is `auto` or `WIDTHxHEIGHT`: edges must be multiples of 16 up to 3840, aspect ratio ≤ 3:1, and total pixels between 655,360–8,294,400. Above 2560×1440 is experimental.
- Output is PNG, JPEG, or WebP; compression (0–100, default 100) applies only to JPEG/WebP. Transparent backgrounds require PNG/WebP — JPEG plus transparency auto-switches to PNG.
- 2.5 always returns Base64: the panel saves the image and serves it from a local URL, ignoring any `response_format` inherited from older presets. Other models keep their prior response-format behavior.
- Edits accept up to 16 PNG/JPEG/WebP inputs (each under 50 MB, subject to configured upload limits; convert other formats first). Outputs of 1–10 use the existing queue, one upstream image per unit.

GPT Image 2.5 is supported through the Images API only — the Responses gateway format and Chat Completions are not part of this integration, but mask editing and paste-back (see below) work the same as with other models. Model access depends on your upstream account; higher quality settings consume more tokens, so equal token prices don't imply equal per-image costs between Flare and Sunburst.

API rules checked on 2026-09-10: [image generation guide](https://developers.openai.com/api/docs/guides/image-generation), [generation reference](https://developers.openai.com/api/reference/resources/images/methods/generate), [edit reference](https://developers.openai.com/api/reference/resources/images/methods/edit).

## Mask Editing & Paste-Back

- The Edit workflow includes a built-in mask editor (brush/eraser/shape/lasso tools, zoom, feathering, magnetic snapping) for painting the exact region to edit, or you can import an existing PNG mask (white is editable for black-and-white masks; below 50% alpha is editable for soft-alpha masks; same-ratio imports are scaled to match). Automatic gap filling and edge smoothing are on by default and only ever add editable pixels, never remove a painted area — both show live in the preview and coverage readout.
- A saved mask (PNG, max 4 MB) persists to disk (`MASKS_DIR`) with its job and is restored on retry after verifying the source image still matches; a changed source refuses the retry.
- By default, a masked edit's result is pasted back onto the original image (`MASK_PASTE_BACK_DEFAULT=true`): only the masked pixels come from the model, everything else reverts to the untouched original with a feathered boundary, and a color-drift guard skips paste-back if the model altered pixels outside the mask. Paste-back can be turned off per edit, and the preview panel can toggle between the result and the original to check the boundary.
- API presets carry a `supports_mask` capability flag (on by default); turning it off in Web Settings hides mask editing for upstreams that reject mask uploads.
- Gallery images record `mask_coverage`, `paste_back` status, and `paste_back_scale`, and can be filtered to masked edits with `mask_only`.

## Supported Upstream Paths

| Path | Notes |
| --- | --- |
| `/v1/images/generations` | Standard image generation endpoint; reads image data from `data[]`. |
| `/v1/responses` | Sends `prompt` and `model`; reads base64 image data from `image_generation_call` output items. |
| `/v1/chat/completions` | Sends OpenAI-compatible chat completions requests; extracts image URLs/base64 data from messages or SSE chunks. |
| `/v1/images/edits` | Used by the Edits flow; sends multipart source images and supported edit params. |

For `/v1/responses` and `/v1/chat/completions`, size/quality/format/compression/quantity controls are disabled because those paths do not share the same parameter contract.

## Custom Async Providers

Some gateways accept a task, return status/result URLs, and finish later. A preset can use a declarative JSON mapping instead of the OpenAI paths: choose **Custom async provider** in Settings and paste the mapping for your gateway (the example below shows every field; the empty field shows the same skeleton as a placeholder).

```json
{
  "version": 1,
  "auth": {"header": "Authorization", "scheme": "Key"},
  "submit": {"path": "/{{model}}", "body": {"prompt": "{{prompt}}", "num_images": "{{n}}", "image_size": {"width": "{{width}}", "height": "{{height}}"}}},
  "poll": {"url_path": "$.status_url", "status_path": "$.status", "done": ["COMPLETED"], "failed": ["FAILED"], "interval_seconds": 2, "timeout_seconds": 600},
  "result": {"url_path": "$.response_url", "images_path": "$.images[*].url", "image_kind": "url"},
  "cancel": {"url_path": "$.cancel_url", "method": "PUT"}
}
```

- **Flow**: `POST` the rendered body to `API URL + submit.path`, read the status URL from the response, poll until `status_path` is in `done` (or `failed`), optionally fetch `result.url_path`, then read images at `images_path` (URLs or base64). Nothing is evaluated as code; paths support `$.a.b[0]` and `[*]` only.
- **Variables**: `prompt`, `model`, `n`, `width`, `height`, `size`, `quality`, `output_format`, `background`. A field whose only content is a variable without a value (for example `width` when the size is `auto`) is left out. `submit.path` may use only `{{model}}`.
- **Task-id mappings (version 2)**: providers that return only a task id can set `"version": 2` and use `poll.task_id_path` with `poll.url_template` (and the same pair for `result`/`cancel`), for example `{"task_id_path": "$.data.id", "url_template": "/v1/jobs/{{task_id}}"}`. Templates are plain paths; the task id is percent-encoded as one path segment, and absolute URLs are rejected. An optional `submit.idempotency_header` (for example `Idempotency-Key`) lets an interrupted submit be retried under the same stable key. **Sync mode**: v2 mappings can set `"mode": "sync"` when the submit response already contains the images — omit `poll` and `cancel` and read images with `result.images_path` from the submit response.
- **Methods and query mapping**: `submit.method` and `poll.method` accept `GET` or `POST` (a GET submit must have an empty body; a POST poll sends an empty JSON object). `submit.query`, `edit_submit.query` and `poll.query` map URL query parameters from templates — the submit variables for submits, `{{task_id}}` for polls; literal values may contain letters, digits and `- _ . ~` only.
- **Image edits (edit_submit)**: adding an `edit_submit` section declares edit support. Multipart edits upload the validated reference images and mask as file parts (`files.images`, one part per image, and `files.mask`); JSON edits inline `{{reference_images}}` (the bounded list of data URLs) and optionally `{{mask}}` (one data URL) in `edit_submit.body`. Edits reuse the panel's upload validation, size caps, mask preprocessing, cancellation and result paste-back, and a configured mask is always sent — a mapping that cannot carry one is rejected before submit.
- **Capabilities**: `capabilities.transparent_background` gates `background: transparent` (when omitted, the panel auto-detects it from `{{background}}` usage) and `capabilities.formats` narrows the accepted output formats; anything undeclared is refused before submit with a clear reason. `stream` stays reserved. Chroma backgrounds remain available for generation — the provider receives the opaque background and the keyed prompt, and the panel removes the color locally.
- **Credentials**: the mapping never contains a key. The preset's API key (env reference or Secret Registry ID) is sent as `header: scheme key`; `header` is `Authorization` or `X-API-Key`, `scheme` is `Bearer`, `Key`, `Token` or empty.
- **Safety**: status, result and cancel URLs come from upstream responses, so they must be on the same origin as the preset's API URL (the key travels with them) and pass the same SSRF and peer-IP checks as the submit URL. Images are downloaded without credentials. Errors are redacted.
- **Recovery**: the unit persists the remote task id, follow-up URLs, absolute poll deadline, idempotency key, and a snapshot of the non-secret mapping after submitting. If a worker dies, another worker resumes polling the same task instead of submitting it again; the deadline and takeover count survive restarts (5 takeovers by default). A submit whose response was never recorded is marked `interrupted` with a `submit_unknown` diagnostic and is never resubmitted automatically. Losing a lease does not cancel the remote task; a user cancellation does send a best-effort `cancel` request. Throttling, 5xx and network errors during polling retry with bounded backoff until the deadline.
- The health check validates the mapping and probes the submit URL; it never submits a task. Settings can copy a mapping-authoring prompt for an LLM and validate a pasted mapping live, including verifying task-id, status, and image extraction against sample responses pasted locally (nothing is sent upstream); the validation response also lists the resolved capabilities.
- **Diagnostics**: failed units keep a redacted, size-capped record of the submit/poll/result stages. Open **Job History → Diagnostics** on a failed job to inspect the stage, HTTP status, failed mapping path, and response snapshot, and to copy the report for a bug tracker.

## Streaming Preview & Cost Estimation

- Streaming preview is opt-in for the Images generation/edit endpoints and `/v1/responses`, with quantities of 1–10. Each image remains a separate queue unit under the existing concurrency limit. Choose 1–3 intermediate frames; the upstream determines their availability and usage. Responses partial images are associated with their output call, and text deltas never become image previews. Declarative custom provider mappings continue to require `capabilities.stream: false`.
- If a compatible upstream rejects the `stream`/`partial_images` parameters, the job fails with a clear error instead of silently retrying non-streaming, since that could double-bill the generation.
- Partial images live only in server memory (one latest frame per running unit/output call, bounded by `PREVIEW_CACHE_MAX_ENTRY_MB`/`PREVIEW_CACHE_MAX_ENTRIES`, defaults 8 MiB / 500 entries). Slow SSE subscribers receive coalesced frames. Final results replace only their own slots; failed, cancelled and completed slots release previews. Reconnect restores the job snapshot before cached frames; a restarted or different worker has no preview frames to replay. A valid JSON final response is accepted from the original request without resubmission.
- **Estimated cost is not a bill.** It's computed only from the `usage` object the upstream actually returns; when usage or a model price is missing, the UI shows why instead of `$0.00`. The builtin rate table covers `gpt-image-1` and GPT Image 2 / 2.5, taken from OpenAI's published pricing — third-party upstreams rarely match it. Override or add rates with `IMAGE_COST_RATES_JSON`.

## Gallery, Touch Controls, and Completion Notifications

- Drag a selection rectangle on desktop, or use Ctrl/⌘/Shift when selecting cards. On touch screens, a deliberate horizontal swipe toggles a card once; vertical movement keeps scrolling. Filter-wide selection retains its server selection token until **Exit select-all** is chosen.
- **Collection overview** shows covers, counts, and default markers from the collection list without per-cover detail requests. Opening a collection preserves other filters; management and ZIP export remain available for empty collections or missing covers.
- The Lightbox supports pinch zoom, pan while enlarged, double tap to zoom, and long press for download, favorite, and edit actions. Escape closes the action menu before closing the viewer.
- **Workspace preferences → Notify when tasks finish** is off by default and asks permission only when enabled. Supported secure-context browsers notify while the page is in the background: once per image parent task or Agent turn, including partial-failure counts. A click opens the result or conversation. Web Locks and a bounded localStorage history coordinate tabs and replays; older browsers use best-effort coordination. The page must remain running; use Webhooks for integrations that need delivery after it closes.
- Queued image tasks freeze non-secret provider settings. Credentials remain in the current preset; changing its API origin stops the queued task with an explicit error instead of sending the updated credentials to the saved origin.

## API Overview

Key backend routes (grouped by area):

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/health`, `/api/access/status`, `/api/version`, `/api/version/latest` | Health check, access state, current/latest version. |
| `POST` | `/api/access` | Unlock the panel with the access key. |
| `GET/PUT` | `/api/settings/overall-config` | Read/save Overall Config overrides. |
| `GET/POST` | `/api/settings` | Read/save active preset, prompt optimizer, R2 backup, proxy, and webhook settings. |
| `POST` | `/api/settings/presets` (+ `/{preset_id}/activate`, `/health`, `DELETE /api/settings/presets/{preset_id}`) | Create, activate, validate, or delete API presets. `POST /api/settings/r2/health` validates draft R2 backup settings. |
| `GET/POST`, `PATCH/DELETE` | `/api/prompt-snippets` (+ `/search`, `/{snippet_id}`) | List/create, search, update/delete reusable prompt snippets. |
| `GET/POST` | `/api/prompt/optimizer-system-prompt`; `POST /api/prompt/optimize`, `/api/prompt/optimizer-health` | Read/save the optimizer system prompt; optimize a prompt or probe optimizer connectivity. |
| `POST` | `/api/assistant/health`, `/api/assistant/prompt/rewrite\|check\|variants`, `/api/assistant/generate/recommend-params`, `/api/assistant/jobs/{job_id}/diagnose`, `/api/assistant/edit/plan`, `/api/assistant/image/prompt`, `/api/assistant/image/prompt/optimize`; `POST/GET /api/assistant/gallery/*` | Probe Assistant connectivity; prompt rewrite/review/variants; parameter recommendations; job diagnosis and edit planning; reverse-prompt local images; gallery describe/analyze/batch operations. |
| `GET/POST` | `/api/agent/conversations`; `GET/PATCH/DELETE /api/agent/conversations/{conversation_id}`; `POST .../turns`; `PATCH .../branch` | List/create, read/rename/delete conversations (deleting keeps gallery images); send a message (idempotent via `client_turn_id`, returns `202`); select a persisted path. |
| `GET` | `/api/agent/turns/{turn_id}` (+ `/events`); `POST /api/agent/turns/{turn_id}/cancel` | Read a turn or stream its replayable SSE events; stop a running turn and cancel its queued image jobs. |
| `POST` | `/api/generate`, `/api/edits`, `/api/edits/from-gallery/{image_id}` | Start generation or edit jobs (edits accept an optional PNG mask and paste-back preference). |
| `GET`, `GET/DELETE` | `/api/generate/jobs` (+ `/events`), `/api/generate/{job_id}` (+ `/events`); `DELETE /api/generate/jobs/history` | List live jobs/history, stream job-list or single-job SSE, read/cancel one job, clear terminal history. |
| `GET`, `POST` | `/api/gallery`, `/api/gallery/search`; `GET/DELETE /api/gallery/{image_id}`; `PATCH /api/gallery/{image_id}/favorite` | List/search/filter gallery images (incl. `mask_only`), read/delete/favorite an image. |
| `POST/PATCH` | `/api/gallery/batch/*`; `POST /api/gallery/thumbnails/status` | Selection-token, favorite, delete, and download batch actions; thumbnail presence checks. |
| `POST`, `GET` | `/api/gallery/nodeimage-upload-jobs`, `/api/gallery/export-jobs`, `/api/gallery/direct-export-jobs`, `/api/gallery/sync-jobs`, `/api/gallery/import-jobs` (each with `/{job_id}`, `/events`, plus `DELETE`/`cancel` or `/download` where applicable) | Async NodeImage uploads, gallery ZIP exports, R2 sync, and import jobs with status/SSE tracking. |
| `GET` | `/api/image/{filename}`, `/api/thumb/{filename}`, `/api/download/{filename}`, `/api/download-all?export_job_id=` | Serve authorized image bytes, thumbnails, single downloads, and streamed ZIP export. |
| `POST` | `/api/import` | Import gallery ZIP archive; `async_job=true` creates an import job. |
| `GET` | `/api/metrics`, `/api/metrics/prometheus` | Optional metrics when `ENABLE_METRICS=true`. |

The public API surface is contract-tested; keep paths, methods, status codes, SSE event names, cookies, and response shapes stable unless a breaking change is intentional.

## Contributor Boundaries

- Keep browser calls same-origin through `/api/*`; do not add direct frontend calls to upstream model APIs, R2, webhook targets, or arbitrary image URLs.
- Keep ownership boundaries intact: routers/request orchestration in `backend/app/api/routers/`, DTOs in `backend/app/schemas/`, persistence and SQLite coordination in `backend/app/repositories/`, upstream integrations in `backend/app/integrations/`, mirrored frontend API types in `frontend/src/lib/api/types.ts`.
- Keep public contracts stable unless a breaking change is intentional: API paths, methods, status codes, cookies, SSE event names, response shapes; generation/edit queue lifecycle, cancellation semantics, and multi-worker SQLite coordination. File-system races are tolerated by UUID filenames, atomic replacement, and orphan GC; do not rely on process-local locks for cross-worker exclusion.
- Keep validation and safety centralized: image byte validation, safe paths, thumbnail/archive helpers; SSRF-sensitive URL handling in validators, safe connector, and integration clients; secrets exposed to the frontend only as masked values or env-ref metadata.
- Preserve current runtime constraints: edits accept up to 16 raster source images, plus an optional single PNG mask capped at 4 MB; gallery ZIP import/export keeps existing safety limits; SSE uses SQLite slot leases with global/per-IP caps and TTL; R2 sync is backup-only — local SQLite rows and local image files remain the source of truth.
- When changing environment variables, update `backend/app/core/settings.py`, `backend/app/core/overall_config.py` when user-visible, `.env.example`, `docker-compose.yml` when configurable in Compose, and this README.
- Do not commit runtime/generated artifacts such as `images/`, `data/`, `frontend/build/`, `.svelte-kit/`, Playwright reports, test results, dependency folders, local DB files, or logs.

## Testing

Activate the project-local `.venv` before running backend or contract tests. The npm contract/performance scripts use `.venv/bin/python`.

```bash
npm run frontend:check
npm run frontend:build
.venv/bin/python -m pytest backend/tests -q
npm run test:contract
npm run test:e2e
npm run test:perf
npm run test:e2e:perf
```

Run the focused subset relevant to your change. For release-bound or broad changes, run all of them.

If Playwright browsers are missing:

```bash
npm --prefix frontend exec playwright install chromium
```

## Contributing

For implementation boundaries and contributor-facing invariants, follow the `Contributor Boundaries` section above. Run the smallest relevant validation set for your change, and keep README/config updates in the same patch when behavior or environment variables change.

## License

This project is licensed under `CC BY-NC 4.0` (`Creative Commons Attribution-NonCommercial 4.0 International`).

See [LICENSE](./LICENSE).
