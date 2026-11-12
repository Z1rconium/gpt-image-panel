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
    <img alt="Release v1.9.4" src="https://img.shields.io/badge/release-v1.9.4-0e8dcc" />
    <img alt="Python 3.11+" src="https://img.shields.io/badge/Python-3.11+-3776AB?logo=python&logoColor=white" />
    <img alt="Node.js 24" src="https://img.shields.io/badge/Node.js-24-339933?logo=node.js&logoColor=white" />
    <img alt="FastAPI 0.115+" src="https://img.shields.io/badge/FastAPI-0.115+-009688?logo=fastapi&logoColor=white" />
    <img alt="SvelteKit 3" src="https://img.shields.io/badge/SvelteKit-3-FF3E00?logo=svelte&logoColor=white" />
    <img alt="License CC BY-NC 4.0" src="https://img.shields.io/badge/License-CC_BY--NC_4.0-6f42c1" />
    <img alt="Image GHCR" src="https://img.shields.io/badge/GHCR-gpt--image--linux-1f6f8b?logo=github&logoColor=white" />
  </p>
</div>

<a id="english"></a>

## Overview

GPT Image Panel is a web UI for image generation, editing, and gallery management, backed by your own server. It connects to an external GPT-compatible image API and keeps images plus metadata in local SQLite.

This project is only a control panel. It does not provide, proxy, resell, or modify any upstream model service. Generation capability, billing, account permissions, and content policy all come from the provider you configure.

## Features

- **Generation** through `/v1/images/generations`, `/v1/responses`, or OpenAI-compatible `/v1/chat/completions`.
- **GPT Image 2.6** (`gpt-image-2.6-flare` / `gpt-image-2.6-sunburst`) with independent complexity control, vector SVG, animated GIF, and markup-guided edits. **GPT Image 2.5** is also supported; `gpt-image-2` stays the default.
- **Editing** through `/v1/images/edits` with a built-in mask editor (brush/shape/lasso, feathering, auto gap-fill) and a browser markup editor (pen/arrow/shape/text, 100-step undo). Masked results paste back onto the untouched original with a color-drift guard.
- **Reference asset packs**: ordered gallery collections that can be reused as multi-image edit sources or referenced in Agent chat with `@alias`.
- **API presets** with base URL/path/key, health checks, SOCKS5 proxy, webhook, env-ref secrets, and capability flags (`supports_mask`, `prompt_guard`).
- **AI Assistant**: prompt rewrite/check/variants, parameter recommendations, job diagnosis, edit planning, and gallery image analysis. Requires an endpoint whose model supports function calling.
- **Agent mode**: multi-turn chat where the model plans and creates images through the job queue — batches, dependent rounds, and edits of earlier images via `@` references. History is server-side and streaming resumes after reconnects.
- **Job queue** on SQLite with SSE progress, cancellation, retry, persisted history, and shared concurrency limits. Repeated Generate clicks append to one preview group instead of replacing earlier results, and every running job is tracked independently.
- **Streaming previews** (opt-in, 1–10 images) with per-job token usage and estimated USD cost when the upstream reports `usage`.
- **Gallery** with cursor pagination, search/filter, favorites, collections with cover overview, lightbox, batch actions, ZIP import/export, and thumbnails.
- **Optional Cloudflare R2** backup sync; local SQLite and images remain the source of truth.
- **Security**: access-key gate, IP/Host allowlists, trusted proxy headers, CSRF checks, CSP nonce injection, and optional JSON/Prometheus metrics.

## Quick Start

### Docker Compose

```bash
cp .env.example .env
# edit .env: set ACCESS_KEY and your upstream API credentials
docker-compose up -d --force-recreate
```

Open `http://127.0.0.1:9090`.

Compose binds to loopback and serves plain HTTP, so disable Secure cookies for local use:

```bash
ACCESS_COOKIE_SECURE=false ACCESS_KEY_COOKIE_NAME=gpt_image_access docker-compose up -d --force-recreate
```

Keep `ACCESS_COOKIE_SECURE=true` when serving over HTTPS. To skip the access gate for local testing, leave `ACCESS_KEY` empty and set `ALLOW_UNAUTHENTICATED=true` — every non-health route then becomes public.

### Docker

```bash
docker build -t gpt-image-panel .
docker run -d --name gpt-image-panel \
  -p 127.0.0.1:9090:9090 \
  -e ACCESS_KEY=change-me \
  -e ACCESS_COOKIE_SECURE=false \
  -e ACCESS_KEY_COOKIE_NAME=gpt_image_access \
  -v $(pwd)/images:/app/images \
  -v $(pwd)/data:/app/data \
  gpt-image-panel
```

The image installs from the hashed `requirements.lock`, so every build gets identical versions. If Docker Hub is slow, override the base images:

```bash
docker build \
  --build-arg PYTHON_BASE_IMAGE=docker.m.daocloud.io/library/python:3.12-slim \
  --build-arg NODE_BASE_IMAGE=docker.m.daocloud.io/library/node:24-alpine \
  -t gpt-image-panel .
```

### Reverse Proxy (Caddy)

```caddyfile
panel.example.com {
    reverse_proxy 127.0.0.1:9090
}
```

For HTTPS, set the matching origin and Host allowlist:

```dotenv
PUBLIC_ORIGIN=https://panel.example.com
ALLOWED_HOSTS=panel.example.com
ACCESS_COOKIE_SECURE=true
```

Caddy automatic HTTPS needs the hostname to resolve to this server and ports `80`/`443` reachable. Behind a CDN proxy, make sure its edge certificate covers the full hostname, or browsers may fail with `ERR_SSL_VERSION_OR_CIPHER_MISMATCH` before traffic reaches Caddy. If Caddy runs in a container, `127.0.0.1` refers to that container — use the app service name instead.

### Troubleshooting

| Symptom | Cause and fix |
| --- | --- |
| `400 Bad Request: Host is not allowed` | `Host` header doesn't match `ALLOWED_HOSTS` / `PUBLIC_ORIGIN`. Fix the typo, then `docker-compose up -d --force-recreate`. |
| Login loop over plain HTTP | `ACCESS_COOKIE_SECURE=true` blocks cookies on HTTP. Set it to `false` (with the cookie name override) for direct HTTP testing. |
| Saving a preset fails | Use a secret reference bound to the same purpose and origin, or enable plaintext secrets where appropriate. |

## Configuration

All environment variables and defaults are documented in **[CONFIGURATION.md](CONFIGURATION.md)**; `.env.example` lists the common ones.

Settings resolve in three layers:

1. **Environment variables** — read once at process start.
2. **Overall Config** — overrides persisted in SQLite via `/api/settings/overall-config`. Hot-reloadable keys apply immediately; others need a restart.
3. **Web Settings** — API presets, prompt optimizer, AI Assistant, R2, and NodeImage are read from SQLite once saved, after which the matching env var has no further effect.

Compose forwards only the variables listed in `docker-compose.yml`. If a secret registry entry references another env var, add that var explicitly to the service environment.

Common variables:

| Variable | Purpose |
| --- | --- |
| `ACCESS_KEY` / `ALLOW_UNAUTHENTICATED` / `TURNSTILE_*` | Access gate (required unless unauthenticated is allowed); optional Cloudflare Turnstile. |
| `DEFAULT_API_URL` / `DEFAULT_API_KEY` / `DEFAULT_API_PATH` | Default upstream preset. Prefer env refs such as `${OPENAI_API_KEY}`. |
| `PUBLIC_ORIGIN` / `ALLOWED_HOSTS` | Reverse-proxy Host/CSRF hardening; must match the deployed domain. |
| `MAX_ACTIVE_GENERATE_JOBS` / `MAX_QUEUED_GENERATE_JOBS` | Running image-unit limit and queue capacity (new jobs return `429` beyond it). |
| `GRANIAN_WORKERS` | Worker process count; set equal to the actual process count. |
| `IMAGES_DIR` / `DATA_DIR` / `DATABASE_FILE` / `LOG_LEVEL` | Storage paths and logging. |
| `AI_ASSISTANT_*` / `PROMPT_OPTIMIZER_*` | Assistant and optimizer endpoints; Assistant is on by default. |
| `R2_*` / `NODEIMAGE_API_KEY` | Optional gallery backup and NodeImage uploads. |
| `SECRET_REGISTRY_JSON` | Declares `${ENV_VAR}` references with purpose and origin bindings. |
| `ENABLE_METRICS` | Enables JSON/Prometheus metrics. |

With `ENABLE_METRICS=true`, `/api/metrics` exposes SQLite coordination diagnostics: `sqlite.write_txn`, `sqlite.write_lock_wait_ms`, `sqlite.write_txn_hold_ms` (watch p95 as `MAX_ACTIVE_GENERATE_JOBS × GRANIAN_WORKERS` grows), `sqlite.busy_retries`, and image-unit lease health (`lease_renewed`, `lease_lost`, `unit_reclaimed`). `lease_lost` should stay at zero.

## Usage

1. Open the panel and unlock with `ACCESS_KEY`. The header button switches between English and Simplified Chinese per browser.
2. In **Settings**, create or select an API preset: base URL, path, model, response format, and API key or env ref.
3. Optionally configure SOCKS5 proxy, webhook, prompt optimizer, AI Assistant, R2 backup, or NodeImage upload, then save and run the health check.
4. Generate from a prompt, or upload/select source images and run edits.
5. Use **Gallery** for reuse, filtering, favorites, batch actions, import/export, and R2 sync.

**Preset shortcuts**: `/?apiUrl=https://api.example.com&apiModel=gpt-image-2` offers to create a preset with that URL and model. Nothing is saved until you confirm, the API key stays empty, only `https` is accepted, and parameters are stripped from the address bar. (`?model=` is the gallery filter, hence `apiModel`.)

**Preset sharing**: **Export** downloads a versioned JSON package (never containing the key), **Share link** copies a `?preset=` link, and **Import** previews a file, clipboard JSON, or shared link before applying.

**Agent mode**: enable it in Settings → AI Assistant with a function-calling model, then switch to **Agent** in the header. Reference earlier images with `@round-1-image-1`, attach gallery images, and press Ctrl/Cmd+Enter. Use Stop to cancel a running reply.

### Agent paths and search

**Edit message → Send as new branch** or **Regenerate** creates a sibling of a historical turn; **Conversation path** restores any path after reload. Switching paths creates no requests and stops no work — stop the active turn before editing. Only ancestors on the executing path enter model history. Image IDs stay stable, but displayed round numbers follow the current path. Deleting gallery images invalidates references on all paths; deleting a conversation keeps its gallery images.

Web search is **off by default**. Enable **Allow web search** only after confirming your endpoint/model supports Responses web search; it requires the `/v1/responses` path. Incompatible configurations block submission rather than silently retrying without search. Citations from upstream annotations are saved with the turn and restored on reload.

### GPT Image 2.6

Choose `gpt-image-2.6-flare` for fast iteration or `gpt-image-2.6-sunburst` for detailed compositions and precise edits. Both support `auto/low/medium/high/xhigh/max` quality, with **Complexity** controlling planning separately from quality.

- **Markup edits**: draw in the browser (**Draw markup**) or upload a transparent PNG layer. The overlay is sent separately as `markup` and never flattened onto the original. It must match the upright primary image, contain transparent pixels, and stay under 50 MiB. Markup and masks are mutually exclusive, and markup edits do not use paste-back.
- **SVG**: single vector result; preview and download keep the vector, the gallery uses a raster thumbnail. Active content, embedded images, and external resources are rejected.
- **GIF**: 2–64 frames at 20–2000 ms each (default 9 × 200 ms). The lightbox plays the animation; thumbnails show the first frame.
- SVG/GIF are generation-only — no reference inputs, masks, markup, streaming previews, or chroma removal. SVG is limited to one result.
- Built-in 2.6 rates: $5/M text input, $8/M image input, $30/M image output tokens. SVG also has authoring-model charges; when unavailable, the estimate is marked incomplete.

Docker includes the Cairo runtime needed for SVG thumbnails. For local development install Cairo first (`brew install cairo`, or `apt-get install libcairo2`). On Apple Silicon, if the loader can't find Homebrew Cairo, start the backend with `DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib`.

### Mask editing and paste-back

The Edit workflow includes a mask editor (brush/eraser/shape/lasso, zoom, feathering, magnetic snapping) or accepts an imported PNG mask. Automatic gap filling and edge smoothing only ever add editable pixels. Saved masks persist to `MASKS_DIR` and are restored on retry after verifying the source image still matches.

By default (`MASK_PASTE_BACK_DEFAULT=true`), only the masked pixels come from the model; everything else reverts to the untouched original with a feathered boundary. A color-drift guard skips paste-back if the model altered pixels outside the mask. Turn it off per edit, or use `supports_mask=false` in a preset for upstreams that reject mask uploads.

### Supported upstream paths

| Path | Notes |
| --- | --- |
| `/v1/images/generations` | Standard generation; reads image data from `data[]`. |
| `/v1/responses` | Reads base64 images from `image_generation_call` output items. |
| `/v1/chat/completions` | Extracts image URLs/base64 from messages or SSE chunks. |
| `/v1/images/edits` | Multipart source images and supported edit params. |

For `/v1/responses` and `/v1/chat/completions`, size/quality/format/compression/quantity/background controls are disabled because those paths don't share the same parameter contract.

### Custom async providers

Gateways that accept a task and finish later can use a declarative JSON mapping instead of the OpenAI paths. Choose **Custom async provider** in Settings and paste the mapping for your gateway.

- **Flow**: submit → poll until a terminal status → read images. Paths support only `$.a.b[0]` and `[*]`; nothing is evaluated as code.
- **Versions**: v1 uses status/result URLs from the response. v2 adds task-id mappings (`poll.task_id_path` + `poll.url_template`) for providers that return only an id, plus `"mode": "sync"` when the submit response already contains images.
- **Edits**: an `edit_submit` section declares edit support; a configured mask or markup is always sent, and a mapping that can't carry one is rejected before submit.
- **Safety**: status/result/cancel URLs must be on the same origin as the preset and pass the same SSRF checks. Images are downloaded without credentials.
- **Recovery**: the unit persists the remote task id and deadline, so another worker resumes polling instead of resubmitting. A submit with no recorded response is marked `interrupted` and never resubmitted automatically.
- **Diagnostics**: failed units keep a redacted record — open **Job History → Diagnostics** to inspect the stage, HTTP status, and failed mapping path.

### Streaming preview and cost

Streaming preview is opt-in for Images generation/edit and `/v1/responses`, with 1–10 images and 1–3 intermediate frames. Each image remains a separate queue unit under the concurrency limit. If a compatible upstream rejects `stream`/`partial_images`, the job fails with a clear error instead of silently retrying non-streaming, which could double-bill.

Partial frames live only in server memory (`PREVIEW_CACHE_MAX_ENTRY_MB` / `PREVIEW_CACHE_MAX_ENTRIES`, defaults 8 MiB / 500 entries). A restarted or different worker has no frames to replay.

**Estimated cost is not a bill.** It's computed only from the `usage` object the upstream returns; when usage or a model price is missing, the UI explains why instead of showing `$0.00`. Override rates with `IMAGE_COST_RATES_JSON`.

### Gallery and notifications

- Drag a selection rectangle on desktop, or use Ctrl/⌘/Shift. On touch screens a deliberate horizontal swipe toggles a card; vertical movement scrolls.
- Gallery ZIP exports include collection names, asset aliases, and ordered members. Import creates new collections, suffixes name/alias conflicts, and restores members whose images were imported. Up to 10,000 images per import.
- The lightbox supports pinch zoom, double-tap zoom, and long-press for download/favorite/edit. Escape closes the action menu before the viewer.
- **Workspace preferences → Notify when tasks finish** is off by default and asks permission when enabled. Notifications fire once per image task or Agent turn while the page is in the background; a click opens the result. Use Webhooks for delivery after the page closes.
- Queued tasks freeze non-secret provider settings. Changing the preset's API origin stops the queued task rather than sending updated credentials to the old origin.

## Development

### Project structure

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
```

### Architecture

- **Backend**: FastAPI under `backend/app/`; ASGI entrypoint `backend.app.main:app`, served by Granian.
- **Frontend**: SvelteKit static app; production backend serves `frontend/build/`.
- **Storage**: images in `images/`, thumbnails in `images/thumbs/`, SQLite in `data/app.sqlite3`, logs in `data/logs/`.
- **Multi-worker coordination**: jobs, leases, SSE slots, and scheduler ownership use SQLite leases. File writes use UUID filenames, atomic `Path.replace()`, and orphan GC TTL cleanup rather than process-local locks.
- **Stack**: Python 3.11+, FastAPI, Granian, aiohttp, boto3, SQLite, Pydantic v2, Pillow, CairoSVG · SvelteKit 3, TypeScript, Tailwind CSS 4 · Playwright, pytest. Browser baseline: Chrome 111+, Safari 16.4+, Firefox 128+.

### Local development

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

Production-style smoke test:

```bash
npm run frontend:build
ALLOW_UNAUTHENTICATED=true .venv/bin/granian --interface asgi backend.app.main:app --host 127.0.0.1 --port 9090
```

### Testing

```bash
npm run frontend:check
npm --prefix frontend run test:unit
.venv/bin/python -m pytest backend/tests -q
npm run test:contract
npm run test:e2e
```

`frontend:check`, `test:unit`, and the backend suite run without a build. Bundle and worker checks inspect the production build, so run `npm run frontend:build` first:

```bash
npm --prefix frontend run check:bundle
npm --prefix frontend run check:worker-build
```

Opt-in performance suites:

```bash
npm run test:perf
npm run test:e2e:perf
```

If Playwright browsers are missing: `npm --prefix frontend exec playwright install chromium`. Run the focused subset relevant to your change; for release-bound work, run everything.

### Contributor boundaries

- Keep browser calls same-origin through `/api/*` — no direct frontend calls to upstream APIs, R2, webhook targets, or arbitrary image URLs.
- Keep ownership boundaries: routers in `backend/app/api/routers/`, DTOs in `schemas/`, persistence in `repositories/`, upstream clients in `integrations/`, mirrored frontend types in `frontend/src/lib/api/types/`.
- Keep public contracts stable unless a breaking change is intentional: paths, methods, status codes, cookies, SSE event names, response shapes, queue lifecycle, and cancellation semantics.
- Keep validation centralized: image byte validation, safe paths, SSRF-sensitive URL handling, and secret masking.
- When adding an environment variable, update `core/settings.py`, `core/overall_config.py` when user-visible, `.env.example`, `docker-compose.yml` when configurable in Compose, and `CONFIGURATION.md`.
- Never commit runtime artifacts: `images/`, `data/`, `frontend/build/`, `.svelte-kit/`, Playwright reports, dependency folders, databases, or logs.

## License

Licensed under [CC BY-NC 4.0](./LICENSE) (Creative Commons Attribution-NonCommercial 4.0 International).
