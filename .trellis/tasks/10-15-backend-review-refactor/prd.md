# Backend full-code review and refactor checklist

## Goal

Deliver an evidence-backed, implementation-level review of the backend (deep) and frontend (quick scan) that identifies outdated or unreasonable module designs, and produce a prioritized refactor checklist. The user then selects checklist items; each selection becomes a separately scoped follow-up refactor task.

## Background

- Baseline: `main` at `a50da7a` (2026-10-15). Working tree has no tracked source modifications.
- Predecessor audit `10-13-architecture-legacy-refactor` (research/audit.md) was an explicit **module/dependency-level** audit, not a line-by-line review. Its F1–F4 findings are already fixed on main; F5–F8 are deferred improvements (mostly frontend). This task goes deeper — implementation-level review — and must not re-report F5–F8 as new findings; it references them as known deferred items.
- Backend ≈ 45K lines across `backend/app` (178 modules); frontend ≈ 30K lines across `frontend/src`.
- Repo conventions for review evidence: file:line anchors, defects distinguished from improvements, no rewrite proposals without a demonstrated problem.

## Requirements

- R1 — Deep backend review, grouped by layer:
  - R1.1 `repositories/` jobs & files (image_jobs, thumbnail_jobs, image_files, thumbnails, settings, sse_limiter)
  - R1.2 `integrations/upstream/` (generation, async_provider, transport, payloads, contracts, edit_paste_back, chroma, diagnostics, errors)
  - R1.3 `repositories/gallery/` + `repositories/coordination/` (queries, mutations, collections, filters, sync_state; coordination domains post-10-13 refactor)
  - R1.4 `repositories/db/` (migrations, rows, settings_store, connection, schema, execution, state)
  - R1.5 `services/` generation & edit pipeline (image_unit_execution, job_events, job_queue, nodeimage_upload_jobs, presets, startup_maintenance)
  - R1.6 `services/` assistant & agent (assistant_vision, assistant_batch, assistant_runtime, agent_tool_executor, and remaining services)
  - R1.7 `api/` (middleware, routers, contract_app, main)
  - R1.8 `core/` (settings, overall_config, secrets, validators, security, redaction, streaming, safe_connector, media, image_models, image_cost, provider_mapping*, observability, logging_config, diagnostics, cdn, api_paths, errors, constants, utils)
  - R1.9 `schemas/` + `runtime/` + non-upstream `integrations/` (agent_client, assistant_client, json_client, session_pool, nodeimage, prompt_optimizer_client, turnstile, agent_parsers, agent_search, r2)
- R2 — Frontend quick scan: module sizes, structural debt signals, status of known deferred items (F5 galleryDeferredActions split, F6 Workspace composition, F8 history cleanup). No deep per-file review.
- R3 — Every finding carries file:line evidence anchors; defects (demonstrable wrong behavior) are distinguished from improvements (maintainability/structure opportunities).
- R4 — Prioritized refactor checklist: each item has priority (P1 defect / P2 structural debt / P3 improvement), impact, remedy sketch, effort estimate, risk, and independent verifiability (testable acceptance) so it can become a follow-up task.
- R5 — Findings persisted per group under `research/`; synthesized `research/report.md` + task-root `refactor-checklist.md`.

## Acceptance Criteria

- AC1 (R1–R3): Every reported finding cites file:line anchors at baseline `a50da7a`; each is labeled defect or improvement; none duplicates known deferred items F5–F8 without new evidence.
- AC2 (R1): All nine backend groups in R1 have a persisted research file covering their modules; no backend module group is skipped.
- AC3 (R2): Frontend scan records sizes/structure signals and F5/F6/F8 status in a research file.
- AC4 (R4): `refactor-checklist.md` orders items by priority; every P1/P2 item states an independently verifiable acceptance criterion and follow-up-task shape.
- AC5 (R5): Final report cross-references previous audit items and states explicitly which remain deferred.

## Out of Scope

- Any product code changes — this task reviews and reports only; refactoring happens in follow-up tasks selected by the user.
- Security assessment beyond what design review surfaces incidentally.
- Dependency upgrades, deployment changes, schema/data migrations.
- Re-auditing the already-refactored F1–F4 areas beyond a regression sanity check.

## Key Decisions

- Review-only task: user explicitly chose "审查报告 + 重构清单" with refactor selection as a separate later step.
- Backend deep / frontend quick: user-selected scope.
- Deliverables live in this task's directory (`research/report.md`, `refactor-checklist.md`), following the predecessor audit's convention of persisting evidence under the task.
