# Research: current backend cleanup opportunities

- Query: Which remaining backend redundancies can be removed with a clear behavior-equivalence argument after the October 15–17 refactors?
- Scope: internal; backend application source and tests, with repository-wide reference searches for proposed removals
- Date: 2026-10-18

## Findings

### Recommendation

Use three finite batches: remove four unused implementation helpers/methods; collapse two byte-identical row-normalization bodies while retaining their established names; clean selected post-extraction import blocks after checking re-exports and monkeypatch consumers. Do not restart the completed Waves A–E or split modules simply because they are long.

The highest-value distinction is between **unused implementation** and **an apparently unused import serving another module**. The current static findings contain both. A global `ruff --fix` is not appropriate.

### Coverage and evidence method

- Enumerated and AST-parsed all **202** Python files under `backend/app`: API 32, core 23, integrations 26, repositories 36, runtime 3, schemas 18, services 62, plus two root files.
- AST-scanned every application function for identical multi-statement bodies and scanned backend source/test references for uniquely named private functions. Rechecked the proposed deletions with repository-wide text searches excluding environments, build outputs, historical task documents and the unrelated `gpt-with-taste/` tree.
- Ran read-only Ruff F401/F841/F811 on application and all **64** backend test/support Python files; ran F821 on application source.
- Read the archived review checklist and prior per-area review findings, then Wave E design §7 final outcome. Current-source deep inspection focused on remaining dead helpers, row transformations, duplicated gallery DTO validators, large stale import blocks and their consumers. This is a structural scan of every module, **not a fresh line-by-line semantic audit of every backend function**.
- Parent session owns the current full pytest baseline; no test suite was duplicated by this researcher.

| Area | Current inspection and retained contract |
|---|---|
| API | Composition (`main.py`, `contract_app.py`, router registry), app lifespan, assistant routes, gallery query/batch/task imports, body-limit content-type helper. Preserve app/proxy entry points, routes, middleware order and active route monkeypatch targets. |
| Core | All files AST/static scanned; checked redaction dependency direction, streaming descriptor and utility ownership. No new compelling core abstraction proposed. Preserve auth, URL/SSRF and secret behavior. |
| Repositories: DB | Read current `__init__`, `connection`, `state`, `rows` import surfaces and row helpers; reused prior schema/execution/migration evidence. Preserve the DB facade and all exported helper names, SQL and transaction scopes. |
| Repositories: gallery/coordination/jobs | All files AST/static scanned; traced favorite/filter consumers in mutations/settings. Prior keyset primitive, maintenance split, coordination ownership and image job cohesion remain relevant. No migration or lifecycle rewrite proposed. |
| Integrations | All files AST/static scanned; deep-read R2 lookup data structure and batch lookup algorithm; inspected equal async-provider extraction helper and payload JSON media-type detector. Preserve provider versions, session pooling and injected persistence boundary. |
| Runtime | All three files AST/static scanned; prior executor/ContextVar/cancellation/accounting ownership retained unchanged. |
| Schemas | All files AST/static scanned; inspected gallery batch/export validators and their differing model-level target constraints. Avoid model inheritance merely to remove two small equal validators. |
| Services | All 62 files AST/static scanned; deep-read gallery job progress/export dead helpers, assistant import surfaces and tests, presets entry-point inventory, and two job sanitization helpers. Preserve leases, cancellation, retries, SSE shapes and settings write-generation fencing. |
| Tests | All 64 files static scanned; inspected shared fixture exports and affected contract/monkeypatch sites. Fixture imports and shared support globals are actual interfaces. |

### B1 — remove three unused private functions (recommended, low risk)

| Definition | Current evidence | Required preservation |
|---|---|---|
| `backend/app/repositories/db/rows.py:786–787`, `_public_file_url` | Only definition found; active `image_url_for_filename` at :790–795 uses `signed_media_url` for public CDN URLs and quoted API URLs otherwise. Not exported by DB facade. | Do not change the signed URL path, signature/expiry semantics, `quote` import (still used), or image/thumbnail URL builders. |
| `backend/app/services/gallery_job_shared.py:95–101`, async `_publish_gallery_job_progress` | Only definition found. Active progress publication uses `_publish_gallery_job_progress_from_worker` at :104–120 and throttler at :123–130; tested in `test_performance_regressions.py:229,252`. | Retain the worker adapter, best-effort failure policy, metric name, expiry stamping and repository import used by the worker adapter. |
| `backend/app/services/gallery_archive_export.py:179–186`, `_build_export_metadata_from_rows` | Only definition found. Current export prepares JSON/NDJSON incrementally in `_prepare_gallery_zip_stream` starting :189 rather than building the old materialized metadata object. | Keep `_export_metadata_header`, streaming writers, archive schema, skipped entries and cleanup paths. |

No decorators, registry registration, string lookup, facade export or documented consumer of these three names was found. Removing definitions does not alter any application call path. Do not add tests that assert removed private names are absent; exercise the surviving behavior instead.

Validation after changes:

```sh
.venv/bin/python -m pytest backend/tests/test_gallery_media_contract.py backend/tests/test_gallery_collections_contract.py backend/tests/test_performance_regressions.py -q
```

### B2 — remove the obsolete R2 single-key lookup method, retain its live data class (recommended)

- `backend/app/integrations/r2/config.py:69–72`: `RemoteKeyLookup` is **live** and must remain with `keys`, `etags` and `use_head_fallback` fields and unchanged constructor defaults.
- The instance method `contains` at :74–95 has no backend caller. Repository search found no Python `.contains(...)` call, reflective reference, documentation, package-level re-export or supported external entry point for the method. `integrations/r2/__init__.py` contains only a package docstring. The reference in `test_r2_sync.py:365` constructs the data class, not the method.
- Current algorithm uses `_remote_key_lookup_for_batch` (`sync.py:179–209`), `_head_remote_keys` (:123 onward), `_list_remote_keys` (:79 onward) and `_complete_head_fallback` (:155–176). It reads `lookup.keys`/`lookup.etags` directly and completes HEAD lookups in a batch before sync. The old instance method duplicates a superseded per-key HEAD path.
- Removing **only the method** also removes two undefined-name references to `_etag_from_response` and `_is_not_found_error` now owned by `sync.py`. Do not fix this by importing `sync` from `config`: that introduces a cycle into the current config → client → sync ownership shape.
- Risk: `contains` lacks a leading underscore, but code/doc/package evidence identifies this class as an integration-internal data carrier, not a user-facing library API. Retain all actually used class imports and fields. This is a recommended internal dead-method deletion, not class deletion.

Validation:

```sh
.venv/bin/python -m pytest backend/tests/test_r2_sync.py backend/tests/test_secret_registry_review_regressions.py -q
.venv/bin/ruff check backend/app/integrations/r2/config.py --select F821 --no-cache
```

### B3 — one implementation for each of two equal row helper bodies (recommended, low risk)

1. Favorite normalization is byte-equivalent in `repositories/db/rows.py:227–233` (`_normalize_gallery_favorite`) and :761–767 (`_normalize_prompt_snippet_favorite`). Preserve both established function names/signatures; make the snippet-named function delegate to the existing gallery helper rather than maintaining a second implementation. This avoids an extra generic abstraction or caller migration.
2. SQL LIKE escaping is byte-equivalent at :301–303 (`_like_contains_param`) and :781–783 (`_like_prompt_snippet_query`). Retain the snippet name as a one-line delegate to the existing escaping primitive.

Callers and contract:

- `repositories/settings.py:234,277,306` uses the snippet helpers for search and mutation.
- `repositories/gallery/mutations.py:341,412,458` and `rows.py:194` use gallery favorite normalization.
- `rows.py:340` uses the generic LIKE helper for prompt filters.
- `repositories/db/__init__.py:87,89,92` intentionally re-exports the established normalization names. Keep these exports.
- Favorite conversion must preserve handling of bool, all integers (including negative and non-1 truthy integers), empty values, stripped/casefolded supported strings and unrecognized strings. **Do not replace with `bool(value)`** or reuse `_normalize_gallery_filter_bool`, whose tri-state policy and accepted vocabulary differ (:236–250).
- SQL matching must preserve the exact escaping order for backslash, `%`, `_` and the enclosing `%...%`. No changes to SQL `ESCAPE`, query normalization or FTS selection.
- Add focused parametrized characterization covering both names on favorite values and LIKE strings including `\\`, `%`, `_` and combinations. Existing HTTP tests cover favorites and snippets but do not establish every edge value for this dedupe.

Validation:

```sh
.venv/bin/python -m pytest backend/tests/test_gallery_media_contract.py backend/tests/test_access_settings_contract.py backend/tests/test_storage_migrations_contract.py -q
```

Run the new helper characterization test file with this subset if implemented separately.

### B4 — clean finite post-extraction import debt (recommended with consumer screening)

Read-only baseline command:

```sh
.venv/bin/ruff check backend/app backend/tests --select F401,F841,F811 --no-cache --output-format concise
```

Result: **620 diagnostics = 596 F401 + 20 F811 + 4 F841** (exit 1). These are pre-existing findings, not a proposed count of deletions.

| Cluster | Diagnostics | Decision |
|---|---:|---|
| `repositories/db/__init__.py` | 118 | Intentional persistence facade; retain exports. Do not autofix. |
| `api/routers/gallery_queries.py` | 80 | High-volume copied import block after split; screen candidates for live aliases/patch sites, then remove proven-unused imports. |
| `api/routers/gallery_batch.py` | 75 | Same post-split pattern; no route or batch logic changes required. |
| `api/routers/gallery_tasks.py` | 59 | Same pattern; preserve cleanup/download endpoint globals actually used. |
| `repositories/db/rows.py` | 58 | Large copied import block; imported helper/constants consumed through the facade or other modules need explicit checking. Not a blanket removal target. |
| `repositories/db/connection.py` | 44 | Similar caution: retain facade dependency and any established re-export needed by repository imports. |
| `tests/support/contract.py` | 42 | Explicit dynamic `__all__` at :716 and many `from ... import *` consumers. Retain support export surface, not an unused-import cleanup target. |
| `services/assistant_batch.py` | 36 | Mostly copied imports; **retain `assistant_client` despite F401** because tests actively patch it through `assistant_router`. |
| `services/assistant_text.py` | 34 | Mostly copied imports; retain active runtime dependencies and module patch surfaces. |
| `tests/test_nodeimage_upload_contract.py` | 20 | Pytest fixture import/shadowing contributes F811; do not remove fixture registration to satisfy lint. |
| `integrations/r2/client.py`, `sync.py`, `config.py` | 15 / 10 / 7 | Old shared headers; same-module imports already preserve active config/client ownership. Screen aliases before removing. |
| Remaining files | 22 | Small isolated hygiene; inspect individually. |

A safe first import batch is the five gallery/assistant implementation modules above, removing only names that are neither locally referenced nor consumed/monkeypatched elsewhere. Standard-library names such as `hashlib`, `inspect`, unused `uuid` and copied typing names are clear examples in gallery blocks; copied schema classes form another large group. Keep import side effects reachable through existing live owner imports; inspect leaf module initialization before dropping its sole import edge.

Concrete exceptions found while auditing:

- `api/contract_app.py:6` F401 for `proxy` is **intentional**. `backend/app/main.py:12` explicitly re-exports `app, proxy`, and `tests/support/contract.py:659–660` patches `backend_main.proxy`. Removing it breaks the established entry point/test seam.
- `services/assistant_batch.py:24` F401 for `assistant_client` is consumed as `assistant_router.assistant_client` by `test_assistant_contract.py:59,134–136,205` and many other tests; `support/contract.py:25` binds that alias. Preserve the alias even though implementation no longer references it.
- `api/routers/assistant.py:12` repeats two exact imports already present at :20–21. Delete the redundant first import line, preserving both names; no DTO changes.
- `api/app.py:27` assigns `worker = state.worker_id` and never reads `worker`. `init_defaults` immediately above already initializes worker state. This can be removed if app lifespan smoke coverage remains green; it is not a reason to change worker initialization.
- `api/routers/prompt.py:180` unused exception binding can become `except Exception:` without changing the handler or log behavior.

Validation for an accepted import-only batch:

```sh
.venv/bin/python -m pytest backend/tests/test_architecture_boundaries.py backend/tests/test_assistant_contract.py backend/tests/test_gallery_media_contract.py backend/tests/test_gallery_collections_contract.py backend/tests/test_nodeimage_upload_contract.py -q
```

Run Ruff only on changed implementation files, retaining/re-exporting intentional interfaces explicitly rather than deleting them. Preserve any pre-existing unrelated diagnostics and report them accurately; no new global lint-clean requirement is justified by the current baseline.

### B5 — retained equal code / larger modules

- Gallery `GalleryBatchRequest` (`schemas/gallery.py:106–132`) and `GalleryExportRequest` (:202–229) share field-level validators but differ materially in target validation: batch requires exactly one of two targets; export allows no target and adds collection selection. Retain explicit DTOs. Sharing two tiny validators yields little value; inheritance risks changing requiredness/schema and is not recommended here.
- `_select_string` (`integrations/upstream/async_provider.py:222–229`) and `_extract_task_id` (`services/provider_mapping_assist.py:66–73`) have identical bodies. Both already use core `select_first`; moving an eight-line convenience wrapper across layers offers little net improvement. Defer.
- `_is_json_content_type` (`api/body_limit.py:37–39`) and `is_json_content_type` (`integrations/upstream/payloads.py:117–119`) match. A core media helper could remove the duplicate, but it creates cross-layer churn for three lines; retain for this cleanup.
- `public_generate_job` (`services/job_events.py:353–356`) and `_webhook_job_view` (`services/webhook_service.py:195–199`) both remove `webhook_url` from a copied dict. Preserve the security behavior. Sharing would couple the intentionally separated SSE/webhook owners or add a new core job helper just for four lines; not recommended.
- `services/presets.py` still contains CRUD, feature settings and env-ref inventory after the credential split. Its settings write-generation guard (`:156–188`) protects concurrent writes. No current evidence justifies another wholesale module extraction solely to reduce line count.

### Baseline issues outside the recommended equivalence edits

Read-only `.venv/bin/ruff check backend/app --select F821 --no-cache --output-format concise` reports four diagnostics:

1. `integrations/r2/config.py:88,93`: undefined helper names inside unused `RemoteKeyLookup.contains`. Addressed only if B2 dead-method removal is accepted; live batch algorithm uses correctly owned helper functions in `sync.py`.
2. `services/gallery_job_sse.py:132`: `logger` is undefined in the poller's exception path. This is a **live error-path defect**, not dead code. Adding logger setup and a targeted failing-DB/poller-cleanup regression belongs to an explicitly accepted bug fix; do not silently claim it as behavior-equivalent cleanup.
3. `repositories/db/state.py:33`: quoted `_GalleryFilterOptionsCacheEntry` annotation lacks a type-only import. Runtime module loading is unaffected by the quoted annotation; adding a `TYPE_CHECKING` import is a small typing-hygiene candidate, not evidence of a production failure. Avoid a runtime cycle (`rows` imports `state`).

## Files found and related specs

- `.trellis/spec/backend/testing.md` — pytest layout, shared contract fixture, architecture scan and commands.
- `.trellis/spec/backend/db-execution.md` — DB executor/accounting/retry/shutdown policy; preserved.
- `.trellis/spec/backend/coordination.md` — coordination facade and lease transient-error/fencing semantics; preserved.
- `.trellis/spec/backend/api-routes.md`, `auth-and-security.md`, `http-compression.md` — router/auth/middleware/streaming contract context; no changes proposed.
- Backend index/directory/quality/database guideline files contain substantial template sections; use real ownership contracts and code evidence rather than inventing modern-style rules.
- `.trellis/tasks/archive/2026-10/10-15-backend-review-refactor/refactor-checklist.md` — Wave A–E landing evidence, deferred decisions and prior regression commands.
- `.trellis/tasks/archive/2026-10/10-17-wave-e-job-framework-convergence/design.md` §7 — final convergence outcome; supersedes the early sign-off status at the top of that archived document.

## External references / versions

No network research or dependency changes. Versions read from local installed metadata: Ruff 0.16.10, pytest 9.1.1, FastAPI 0.139.0, Pydantic 2.13.4. Recommendations rely on repository code and local tooling, not assumed latest-library behavior.

## Caveats / Not Found

- No product files, tests, specs or dependency files were edited by this research; output is this report only.
- Public facade compatibility is a real constraint. Static F401 is not proof of dead public exports or a license to remove monkeypatch targets.
- Reference absence establishes repository-internal non-use, not every conceivable external import. B1 names are private and unexported; B2 is an undocumented integration-internal method whose data class remains live.
- Do not reopen D2a service extraction (forbidden integration → service edge), v1/v2 validator convergence, migration-chain squashing, or gallery/image SSE/lease forced convergence. Their prior evidence still determines the decision.
- Full pytest execution and baseline pass/fail counts belong to the parent session. Commands above are acceptance paths for future approved edits, not checks this researcher claims to have passed.
