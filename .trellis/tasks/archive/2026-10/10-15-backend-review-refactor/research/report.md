# Backend full-code review — synthesized report (baseline a50da7a)

## Executive summary

27 findings across 11 reviewed groups (10 backend + frontend quick scan): **0 P1 defects, 14 P2 structural-debt items, 13 P3 improvements**. The tree is healthy: backend suite **976 passed / 22 skipped**, layer matrix **0 violations** (top-level and deferred), no direct `sqlite3.connect` outside `repositories/db/connection.py`, no blocking work on the event loop (`time.sleep` / file & ZIP I/O all hop through `asyncio.to_thread` / `runtime/blocking`), and HTTP session pooling is centralized.

The dominant theme is **mixed responsibilities in large modules** and **one duplicated infrastructure pattern**:

1. Large modules that each own several unrelated domains: `services/presets.py` (4 concerns), `services/job_events.py` (4), `api/routers/settings.py` (4), `api/middleware.py` (5), `core/validators.py` (secret-ref + SSRF + masking).
2. Two parallel background-job frameworks (image jobs vs gallery jobs) re-implementing claim/lease/progress/SSE/cancel, plus **five near-identical lease-renewal loops**.
3. Copy-paste duplication: six byte-identical `_normalize_stored_*` helpers; duplicated URL redaction; duplicated host/origin/IP allowlist parsing.

The predecessor audit `10-13-architecture-legacy-refactor` (module/dependency level) and its `F1–F4` fixes are confirmed **held**: coordination's five-domain split is clean, the runtime↔repositories boundary is clean, and cancellation/executor accounting is intact. This review does not re-report `F5–F8` (see §Deferred items).

## Method note (deviation)

The plan dispatched 11 `trellis-research` sub-agents in two waves. **All failed immediately with "Go usage limit exceeded"** and produced no artifacts. The review was executed in the main session instead, using: an AST structural analyzer (line counts, longest functions, nesting depth, import graph, deferred-import layer check, async-blocking probe), targeted `grep` for cross-cutting signals, and selective deep reads of the flagged modules. Per-group evidence is in `research/gN-*.md`. Coverage is complete for the 10 backend groups; frontend is quick-scan by agreement.

## Findings

Format: `ID — priority, defect|improvement — title`. Priority: P1 = demonstrable wrong behavior; P2 = structural debt impeding maintenance; P3 = defer-safe improvement.

### Upstream client

- **F-G2-1 — P2, improvement — gallery-entry construction orchestration lives in the upstream client module.**
  `integrations/upstream/generation.py:155 save_gallery_entries_from_upstream_data` (147 lines) builds gallery metadata, image ids/filenames, and per-task byte budgets. Not an import violation (persistence is an injected callback at `:165`), but a boundary/naming smell.
  *Remedy:* keep pure transport/parse in `generation.py`; move entry/asset construction to services. *Effort/risk:* medium/medium.
- **F-G2-2 — P2, improvement — four ~150-line orchestrators in generation.py; depth-8 streaming parse.**
  `generation.py:684 call_image_generation_api` (170), `:915 call_image_edit_api` (161), `:304 consume_streaming_image_response` (153, nesting 8), `:155` (147).
  *Remedy:* extract a streaming decoder and a shared call/normalize helper; flatten the streaming path. *Effort/risk:* medium/medium.

### Gallery + coordination

- **F-G3-1 — P2, improvement — three overlapping pagination strategies.**
  `repositories/gallery/queries.py:740 _get_gallery_page_rows_on_conn` (147), `:510 _get_gallery_page_rows_by_anchor_on_conn` (98), `:389 _store_gallery_page_anchor_best_effort` (95), plus offset (`:350`) and cursor (`:610`) readers.
  *Remedy:* pick one canonical strategy for the HTTP path; make others thin adapters. *Effort/risk:* medium/medium.
- **F-G3-2 — P2, improvement — mutations.py mixes CRUD with backfill, orphan GC and cache invalidation.**
  `gallery/mutations.py` CRUD (`:84-859`) + byte backfill (`:331`, `:356`, `:411`) + `cleanup_orphan_gallery_files` (`:914`, 89) + `invalidate_thumbnail_cache` (`:1041`).
  *Remedy:* split row mutations from file/byte reconciliation + GC. *Effort/risk:* medium/medium.
- **F-G3-3 — P3, defect (convention) — Chinese docstring in an English module.**
  `gallery/mutations.py:1041` vs `.trellis/spec/backend/index.md:41`. *Remedy:* translate. *Effort/risk:* trivial.

### repositories/db

- **F-G4-1 — P2, improvement — `_ensure_database` is one 365-line function.**
  `repositories/db/schema.py:73` spans `:73-437`: idempotency guard + `PRAGMA` + inline `executescript` DDL (`:90`) + FTS + backfills.
  *Remedy:* extract DDL to per-domain statements/resource; separate init policy from schema. *Effort/risk:* medium/medium (must stay DB-compatible).
- **F-G4-2 — P2, improvement — six byte-identical `_normalize_stored_*` helpers + multi-domain store.**
  `repositories/db/settings_store.py:55-115` — six functions identical but for `field_name`; module owns settings + secret refs + overall config + presets.
  *Remedy:* single `_normalize_stored_secret(value, field_name)`; consider splitting stores. *Effort/risk:* low/low.
- **F-G4-3 — P3, improvement — duplicated retry-policy block in execution.py.**
  `repositories/db/execution.py:56-63` and `:97-104`. *Remedy:* factor `_resolve_busy_policy`. *Effort/risk:* low/low.

### services — generation/edit pipeline

- **F-G5-1 — P2, improvement — presets.py aggregates four concerns.**
  `services/presets.py`: credential/secret handling (`:71`, `:91`, `:165`, `:802`), preset CRUD (`:325`, `:537`, `:520`), four feature-settings domains (`:866`, `:895`, `:917`, `:953` + builders `:205-255`, `:599-676`), env-ref inventory (`:765`).
  *Remedy:* split presets / credentials view / per-feature settings. *Effort/risk:* medium/medium.
- **F-G5-2 — P2, improvement — job_events.py mixes four responsibilities.**
  `services/job_events.py`: preview cache (`:51-120`), SSE publication (`:143`), per-process memory (`:202-297`), **webhook dispatch** (`:365-423`), persistence + view overlay (`:487-568`).
  *Remedy:* separate state/publication from persistence; move webhook to `webhook_service.py`. *Effort/risk:* medium/medium.
- **F-G5-3 — P2, improvement — five near-identical lease-renewal loops.**
  `image_unit_session.py:148`, `assistant_runtime.py:247`, `nodeimage_upload_jobs.py:341`, `agent_run_lifecycle.py:43`, `gallery_job_shared.py:49`.
  *Remedy:* shared "renewing lease/slot holder" parameterized by renew/release/lost callbacks. *Effort/risk:* medium/medium.

### services — agent / assistant

- **F-G6-1 — P2, improvement — assistant_runtime.py mixes resolution, slot leasing, JSON transport.**
  `assistant_runtime.py`: resolution (`:389`, `:448`, `AgentRuntime:421`), slot leasing (`:221-357`), JSON transport (`:509`). Includes one of the F-G5-3 loops.
  *Remedy:* isolate slot leasing + JSON transport. *Effort/risk:* medium/medium.
- **F-G6-2 — P3, improvement — assistant_vision.py mixes preview-image generation with gallery analysis.**
  `assistant_vision.py:222 optimize_uploaded_image_prompt` (generates a preview) vs `:453`/`:583`/`:587` (read-only analysis). *Effort/risk:* low-medium/low.
- **F-G6-3 — P3, improvement — AgentToolExecutor class owns auth + batch + image lifecycle.**
  `agent_tool_executor.py:39`, `_run_batch:114` (107), `_run_image:231` (129). *Effort/risk:* low/medium.

### services — gallery

- **F-G7-1 — P2, improvement — two parallel job-lifecycle frameworks.**
  Image side (`job_queue.py`, `claim_loop.py`, `image_unit_session.py`, `job_events.py`) vs gallery side (`gallery_jobs.py`, `coordination/gallery_jobs.py`, `gallery_job_shared.py:49`, `gallery_job_sse.py`) implement the same primitives (admission, claim, lease+lost, progress throttle, SSE fan-out, cooperative cancel).
  *Remedy:* extract a shared background-job runtime both configure. *Effort/risk:* **high/high** — highest-value, highest-risk item.
- **F-G7-2 — P3, improvement — ZIP export/import long functions.**
  `gallery_archive_export.py:62` (203), `:189` (141); `gallery_import_jobs.py:79` (121). *Effort/risk:* medium/medium.

### api

- **F-G8-1 — P2, improvement — routers/settings.py mixes four API surfaces.**
  `api/routers/settings.py`: overall config (`:219-288`), user settings (`:292`), presets (`:450-586`), health (`:393`, `:597-749`).
  *Remedy:* split settings / presets / health routers. *Effort/risk:* low-medium/low-medium.
- **F-G8-2 — P2, improvement — middleware.py grab-bag of five concerns.**
  `api/middleware.py`: gzip (`:74-124`), security headers (`:141`), error envelope (`:176-229`), host/origin/CSRF (~`:232-478`), registration (`:482-641`).
  *Remedy:* split by concern; reuse core host/origin helpers. *Effort/risk:* medium/medium (security-relevant).

### core

- **F-G9-1 — P2, improvement — validators.py mixes secret-env-ref handling with SSRF/URL validation.**
  `core/validators.py:14-66` secret-ref parsing/normalization + `:69-366` IP/URL validation + `:449-530` masking.
  *Remedy:* move secret-ref helpers to `core/secrets.py`, masking to `core/redaction.py`. *Effort/risk:* low-medium/low.
- **F-G9-2 — P3, improvement — duplicated URL redaction.**
  `core/redaction.py:40 _redact_url` vs `core/validators.py:369 redact_url`. *Effort/risk:* low/low.
- **F-G9-3 — P3, improvement — allowlist validation duplicated across core modules.**
  `core/overall_config.py:274/287/293` vs `core/validators.py` vs `api/middleware.py:_configured_allowed_hosts`. *Effort/risk:* low/low.

### schemas + non-upstream integrations

- **F-G10-1 — P3, improvement — schemas/settings.py aggregates many domains.**
  `schemas/settings.py` (587): settings + presets + provider-mapping + overall-config DTOs. *Effort/risk:* low/low.
- **F-G10-2 — P3, improvement (caution) — provider v1/v2 duplicate validators.**
  `schemas/provider/v1.py` (116) vs `v2.py` (370). Likely a deliberate compatibility contract; consolidate **only** with a v1 deprecation plan. *Effort/risk:* medium/high.
- **F-G10-3 — P3, improvement — r2/config env handling overlaps core/secrets.**
  `integrations/r2/config.py` as a second credential-env read path. *Effort/risk:* low/low.

### repositories jobs & files

- **F-G1-1 — P3, improvement — mask-file persistence in the job-DB module.**
  `repositories/image_jobs.py:37`, `:1249`. *Effort/risk:* low/low.
- **F-G1-2 — P3, improvement — blocking CPU-slot acquire is a latent event-loop footgun.**
  `repositories/thumbnail_jobs.py:91 _thumbnail_cpu_slot` (`time.sleep:108`); callers verified safe today (`gallery_maintenance.py:104`, `gallery_queries.py:394/357` use `asyncio.to_thread`). *Effort/risk:* low/low.

### frontend (quick scan)

- **F-G11-1 — P3, improvement — api client lacks a binary/streaming variant.**
  Raw `fetch` at `jobMaskRestore.ts:22`, `galleryDeferredActions.ts:756`, `:810` re-implement credentials/timeout/`ApiError` mapping. *Remedy:* add `apiFetchRaw`/`apiFetchBlob`. *Effort/risk:* low/low.

## Verified clean (no findings)

- Layer matrix: **0 violations** (top-level and deferred), enforced/tested by `backend/tests/test_architecture_boundaries.py` (`test_deferred_imports_cannot_bypass_the_layer_matrix`).
- `runtime/*` imports no `repositories` (10-13 fix held); `run_db_operation` awaits `asyncio.sleep`, sync variant uses `time.sleep` — each correct.
- Session pooling centralized in `integrations/session_pool.get_pool()`; no per-request `ClientSession`.
- `services/image_jobs.py` (1379) is cohesive, not a god-module — size alone is not a finding.
- `_ensure_database` is the only true mega-function; `migrations.py` boundary flagged as an open question, not a defect.

## Deferred items (cross-reference to predecessor audit `10-13-architecture-legacy-refactor/research/audit.md`)

- **F5** `galleryDeferredActions.ts` (1043) unchanged — remains deferred.
- **F6** `Workspace.svelte` (1749), `MaskEditorDialog.svelte` (2068), `maskDocument.ts` (1913) unchanged — remains deferred (no geometry rewrite without browser coverage).
- **F7** spec index still advertises template docs as "To fill" — separate Bootstrap Guidelines task; not in this report.
- **F8** history-loading cleanup **appears addressed** (seq-guarded `jobs.ts:86/108/117/149/157`); recommend a targeted regression test, not a refactor.

## Out of scope for this report

Security assessment (only design-review signals surfaced), dependency upgrades, schema/data migration changes, and the actual refactors (follow-up tasks from `refactor-checklist.md`).
