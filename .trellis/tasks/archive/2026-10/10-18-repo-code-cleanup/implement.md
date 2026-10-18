# Implementation plan

## Entry gate and ownership

- [x] User approved task creation and planning.
- [x] Repository and prior refactor evidence inspected; research and pre-change baseline recorded.
- [x] PRD convergence pass: scope, supported conditions, acceptance and retained/deferred findings integrated into final PRD.
- [x] Final planning summary approved by the user on 2026-10-18; implementation and phased commits/pushes to GitHub are authorized, without a PR.
- [x] Only then run `python3 .trellis/scripts/task.py start .trellis/tasks/10-18-repo-code-cleanup`.
- [x] Load Phase 2.1 context; dispatch a Trellis implement agent starting with the active task path. Each source area has one active writer, with adjacent test repairs included; preserve other owners' edits. Do not split a coherent converter/state responsibility across writers.

## Batch 1 — backend dead implementation and equivalent helpers

- [x] Read complete targets/direct callers/tests and current diff before edits; recheck B1/B2 non-use including facade, registration and reflective uses.
- [x] Add characterization for favorite normalization and LIKE escaping under both established helper names; prove it passes pre-refactor. Cover bool/int/negative/non-1, null/empty/whitespace/string/unknown values and slash/percent/underscore combinations.
- [x] Implement B1/B2 deletions and B3 delegation; keep live data class constructor, exports and worker/archive paths intact.
- [x] Run characterization plus focused tests:
  `.venv/bin/python -m pytest backend/tests/test_gallery_media_contract.py backend/tests/test_gallery_collections_contract.py backend/tests/test_performance_regressions.py backend/tests/test_r2_sync.py backend/tests/test_secret_registry_review_regressions.py backend/tests/test_access_settings_contract.py backend/tests/test_storage_migrations_contract.py -q`
- [x] Check changed implementation files for applicable F401/F841/F811 and F821 against baseline; no new findings. Do not demand global lint cleanliness or remove intentional exports.

## Batch 2 — finite import/local hygiene

- [x] Screen only B4's five copied implementation import blocks plus exact duplicate assistant import, unused app local and unused prompt exception binding. Trace module-attribute consumers and import-time side effects before each removal. Leave uncertain names in place with reasons.
- [x] Preserve contract_app.proxy, assistant_batch.assistant_client, DB facades, pytest fixtures and support exports; no global autofix.
- [x] Run focused backend contracts and architecture checks:
  `.venv/bin/python -m pytest backend/tests/test_architecture_boundaries.py backend/tests/test_assistant_contract.py backend/tests/test_gallery_media_contract.py backend/tests/test_gallery_collections_contract.py backend/tests/test_nodeimage_upload_contract.py -q`
- [x] Inspect import-only diff and run scoped unused-code checks. No route/auth/DB/background algorithm change.

## Batch 3 — frontend reuse and tooling

- [x] Ownership: frontend/src, directly necessary frontend unit/browser tests and frontend/scripts/bundle-budget.mjs. Read full converter/query/reset sections and direct consumers first.
- [x] Expand galleryQuery tests and add promptForm tests on old implementation. Freeze empty/full/legacy values, false/null flags, cursor/direction, API-path differences, dimensions/fallbacks, chroma modes, zero compression, formats/qualities, model/quantity and job-specific stream/paste-back semantics.
- [x] Run new characterization tests before production edits; then implement F1–F3 and T1 without altering exports, lazy imports or template/CSS behavior.
- [x] Run `npm --prefix frontend run test:unit -- tests/unit/galleryQuery.test.ts tests/unit/promptForm.test.ts` and `npm --prefix frontend run check`.
- [x] Browser acceptance from frontend/ (substitute a free isolated port; preserve existing user servers):
  `NO_PROXY=127.0.0.1,localhost no_proxy=127.0.0.1,localhost PLAYWRIGHT_DEV_PORT=<free-port> npm run test:e2e -- tests/e2e/gallery.spec.ts tests/e2e/prompt-assistant.spec.ts tests/e2e/preferences.spec.ts --grep 'gallery edit choice|gallery cards can reuse|lightbox use all|filtered results through a batch token|floating prompt optimizer (opens|compares)|retry pins'`
- [x] Include existing gallery paging/filter cases if not matched above. Exercise pointer release/re-drag and inspect actual workspace/gallery renders; save acceptance evidence. Use existing browser tests rather than a new app harness.

## Final integrated acceptance

- [x] Dispatch Trellis check agent with complete approved requirements and actual candidate; it owns in-scope corrections and targeted checks. Feed newly invalidated safety assumptions to the task owner, not a behavioral workaround.
- [x] Final full-backend evidence: GitHub Verify `39090408639` at `79090f1` on Python 3.12 reports **1191 passed / 22 skipped**. The earlier local loopback-capable full run also passed at Batch 1; no redundant local full rerun was needed after the valid final CI result.
- [x] `.venv/bin/python -m pip check` if dependency state changed; no installation/upgrade is planned, so reuse the unchanged consistency baseline otherwise.
- [x] `npm --prefix frontend run test:unit`
- [x] `npm --prefix frontend run check`
- [x] `npm --prefix frontend run build`
- [x] `npm --prefix frontend run check:bundle`
- [x] `npm --prefix frontend run check:worker-build`
- [x] `node --check frontend/scripts/bundle-budget.mjs`; same report semantics/thresholds for the same inputs after T1.
- [x] Compare final diff and check evidence with R1–R6 / AC1–AC5, including intentionally preserved namespace aliases and supported edge cases. Reuse valid checks for the same candidate rather than repeating for reassurance.
- [x] Record actual outcome, necessary spec updates and final locations; follow finish-work guidance without committing/publishing unrelated user files or performing unauthorized Git/release actions.

## Stop/rollback conditions

If removal has an actual consumer or import side effect, retain it and record evidence. If a refactor changes wire/default/state/cancellation behavior, restore that contract within the owning batch. Reverse only task-owned changes if necessary; no destructive resets/cleanup. Separate baseline bug findings remain documented, not silently fixed. A materially different scope/design needs an updated final review before proceeding.
