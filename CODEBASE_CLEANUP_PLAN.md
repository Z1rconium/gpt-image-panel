# Codebase cleanup plan

Static audit of the tracked backend, frontend, tests, build scripts, and deployment configuration on 2026-09-25. The changes below target code with no repository callers or exactly duplicated behavior. Runtime output, database schema, API contracts, and release configuration are outside this cleanup.

## Implemented in the working tree

1. Remove uncalled repository entry points: `record_worker_metrics_snapshot` in `backend/app/repositories/coordination.py`; `get_generate_job_updated_at_edge`, `count_active_image_job_units`, `create_image_job_units`, and `cancel_image_job_units` in `backend/app/repositories/image_jobs.py`. Repository-wide name searches found no callers. Keep `_cancel_image_job_units_on_conn`, which is called from the active cancellation transaction. Keep the worker metrics table and `refresh_runtime_coordination_metrics`, which writes current snapshots.
2. Consolidate gallery thumbnail URL decoration. `backend/app/repositories/thumbnail_jobs.py` imports `_attach_gallery_thumbnail_url` from `db` and then shadows it with an identical local function. Remove both the shadow and the now unused import. In `gallery/mutations.py`, keep only the `db` import; in `gallery/queries.py`, import the helper from `db` directly. Preserve the helper in `db/rows.py` and its export in `db/__init__.py`.
3. Move the identical `_failure_rates` functions from `backend/app/services/runtime_metrics.py` and `backend/app/api/routers/metrics.py` into `backend/app/core/observability.py`, then use the shared function at both call sites. Keep the metric keys and zero denominator behavior unchanged.
4. Remove unused imports left by the gallery archive split from `gallery_archive_import.py`, `gallery_archive_export.py`, and `gallery_archive_shared.py`. Check each symbol against local usage and annotations before deletion. Do not change callback aliases that are still referenced by type annotations.
5. Remove `sameGalleryImageList` from `frontend/src/lib/features/gallery/query.ts`: it has no source or test caller. Keep `sameGalleryEntryThumbnail`, which is used by the gallery store. Replace all component uses of the misleading `formatBeijingTime` alias with `formatLocalTime`, then remove the alias from `frontend/src/lib/utils/format.ts`. The formatter already uses the browser's local time zone.
6. Remove `containsRect` and `subtractRect` from `frontend/src/lib/features/mask/maskDocument.ts`. They are referenced only by their own unit cases, not by runtime code. Remove those isolated cases and imports without altering tests for active mask behavior.
7. Update the stale `FRONTEND_IMPLEMENTATION_PLAN.md` reference in `frontend/scripts/bundle-budget.mjs`; that file is absent. Keep the bundle budget script and its checks.
8. Remove unused frontend type exports after confirming there are no source or test imports: `PanelController` from `frontend/src/lib/features/workspace/panelController.ts`, and `MessageResponse`, `NodeImageBatchUploadJobStatus`, and `NodeImageBatchUploadCreateResponse` from `frontend/src/lib/api/types/gallery.ts`. Keep the active `NodeImageUploadJobStatus` definition.

## Follow-up contract review

- The settings dependency path is `services/presets.py` → `repositories/settings.py` → `repositories/db/settings_store.py`. Shared default values and integer coercion now live in `core/settings_defaults.py`, which both layers can import without a cycle. The two input normalization functions remain separate because persistence and API input have different rules.
- No repository module uses `from ... import *` for the gallery modules. `gallery_common.py`'s dynamic `__all__` is unused in the repository, and its imported names with no local or explicit caller were removed. Uncalled gallery query and filter wrappers, the old hash update helper, and `ConflictError` were also removed. The underlying query and migration helpers that active paths call remain.
- Schema versions 1–25 remain registered and in order. `_run_schema_migrations` records and executes missing versions, while the storage contract tests exercise idempotence and legacy database upgrade. The version-1 no-op is part of the recorded baseline contract. No historical migration was removed or rewritten.
- FastAPI route handlers and schema validators remain outside this cleanup because framework registration and validation use them indirectly.

The architecture boundary test now identifies deferred imports by module and containing function instead of source line. This keeps the allowlist specific while avoiding failures when unrelated lines move. The backend suite passed after this follow-up: 571 passed, 21 skipped. `npm run frontend:check` reported 0 errors and 0 warnings.

## Review criteria

- Only the intended symbols and imports disappear; current job creation, cancellation, gallery URL, metric, and date formatting paths retain their behavior.
- No newly unused or duplicate imports remain in the edited modules. `git diff --check` and frontend diagnostics should be clean.
- The main agent reviews the diff independently and reports any limits of static validation.

## Review outcome

In the initial pass, the main agent corrected a missing gallery helper import and an overbroad removal of a `unionRect` test. That pass used static checks only. The follow-up ran the full backend suite and frontend diagnostics; `git diff --check` passed.
