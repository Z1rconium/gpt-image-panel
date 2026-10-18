# Cleanup decision and coverage summary

Date: 2026-10-18. Baseline: `main` at `12a19db`.

## Whole-repository coverage

The current application has 202 backend source modules and 146 frontend source files. Both complete source inventories received structural/static analysis; candidate edits received direct source/caller inspection. Backend tests/support (64 Python files) were statically scanned; frontend test inventories and relevant cases were inspected. Root build/deployment/configuration files, all workflow/deploy files, frontend tool configs and scripts were reviewed. Documentation and previous task records supplied supported behavior and prior decisions; dependency lockfiles, product prose and locale catalogs are not rewrite targets.

This establishes repository-wide structural coverage, not a fresh semantic proof of every line. Detailed coverage/depth is in backend.md and frontend.md; tooling.md covers infrastructure. No source file was proven wholly unreachable in the frontend.

## Accepted implementation candidates

All are maintainability improvements, not demonstrated defects. Preserve each boundary and validate its surviving behavior.

| ID | Selected change | Anchors at baseline | Required acceptance |
|---|---|---|---|
| B1 | Delete three uncalled, unexported private functions | backend/app/repositories/db/rows.py:786; backend/app/services/gallery_job_shared.py:95; backend/app/services/gallery_archive_export.py:179 | URL generation, worker-driven progress and streamed archive behavior unchanged |
| B2 | Delete only obsolete RemoteKeyLookup.contains; retain live class/fields/defaults | backend/app/integrations/r2/config.py:69–95 | Batch LIST/HEAD fallback and R2 regression tests unchanged; no config→sync import cycle |
| B3 | Reuse favorite normalization and LIKE escaping implementations through existing helper names | backend/app/repositories/db/rows.py:227,301,761,781 | Characterize value/escaping edge cases before edit; preserve function names/signatures/facade exports |
| B4 | Screen and remove stale imports in five post-extraction gallery/assistant implementation modules; remove exact duplicate assistant import and unused lifespan local/exception binding | api/routers/gallery_queries.py, gallery_batch.py, gallery_tasks.py; services/assistant_batch.py, assistant_text.py; api/routers/assistant.py:12,20; api/app.py:27; api/routers/prompt.py:180 (all under backend/app/) | Preserve module side effects, route globals and monkeypatch/re-export interfaces; focused API/architecture and full backend tests |
| F1 | Search body reuses existing filter-to-selection serializer | frontend/src/lib/features/gallery/query.ts:55–87 | Full empty/populated JSON shapes equivalent; paging/filter/selection browser flow |
| F2 | Edit form reuses gallery form converter with three explicit overrides; delete private response-format pass-through | frontend/src/lib/utils/promptForm.ts:33–35,68–110 | New characterization before refactor; preserve current API path, edit size and chroma behavior, all normalization edge cases |
| F3 | Pointer-up reuses existing resetPointerState | frontend/src/lib/components/PromptOptimizerAssistant.svelte:167–176,344–363 | Capture-release ordering, click suppression, drag and persisted position unchanged |
| T1 | Delete unused non-exported isEntryNamed CLI helper | frontend/scripts/bundle-budget.mjs:73–77 | Same production budget report semantics/thresholds and Worker check |

Implementation may omit a candidate only if new caller/side-effect evidence invalidates its equivalence argument; record the finding and retain the supported behavior. Such evidence does not authorize redesign or a different behavioral change.

## Retained or separate

- Preserve public DB/coordination/app facades, test support exports, assistant_client patch alias, provider v1/v2 support, migration history, runtime fallbacks and asynchronous ownership guards.
- Do not restart landed backend Waves A–E. Do not reopen D2a, forced gallery/image lease or SSE convergence, or Workspace/galleryDeferredActions splits for size alone.
- The 620 existing F401/F841/F811 diagnostics include intentional interfaces. They are not a deletion count or an acceptance target. No repository-wide autofix or lint-policy expansion.
- `backend/app/services/gallery_job_sse.py:132` has an undefined logger in a live exception path. This is a separate baseline bug, recorded but excluded from equivalence edits. Its future fix needs error-path coverage and explicit bug-fix scope.
- `backend/app/repositories/db/state.py:33` has a quoted annotation without a type-only import; retain for now, not a reason to introduce a runtime import cycle.
- Keep dependency versions/security floors, release defaults, nginx/buildx behavior, bundle budgets, visual design and supported legacy use unchanged.
- Preserve pre-existing untracked scaffolding, the unrelated untracked gpt-with-taste project and runtime data/images.

## Verification basis

See baseline.md and logs: backend combined 993 passing cases / 22 skips across sandbox full run plus successful approved rerun of six loopback-restricted cases; frontend 206 passing tests, zero diagnostics, build/budgets/Worker green. Browser checks and post-edit checks remain required for the implemented candidate.
