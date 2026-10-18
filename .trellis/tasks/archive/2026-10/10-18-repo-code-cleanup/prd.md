# Repository-wide behavior-preserving cleanup

## Goal

Reduce maintenance cost across the application's code by deleting proven redundant implementation and reusing existing equivalent logic, while keeping current functionality and supported usage unchanged.

## Background

The user requested whole-repository code cleanup and approved task creation/investigation on 2026-10-18. Planning baseline is main at `12a19db`. Current code is a FastAPI/SQLite backend plus SvelteKit frontend. Recent backend refactors already resolve much of the earlier audit; their final outcomes supersede early proposals. The new inventory covers 202 backend modules, 146 frontend source files, tests and build/deployment tooling. Inspection depth and confirmed findings are recorded in `research/summary.md` and its linked per-area reports.

## Requirements

- **R1 — Whole-repository assessment:** document structural/static coverage across tracked application source, tests, supporting scripts and build/deployment configuration; distinguish direct review from sampling and improvements from demonstrated defects.
- **R2 — Finite equivalence cleanup:** implement the selected B1–B4, F1–F3 and T1 candidates in `research/summary.md`, subject to their caller/side-effect evidence. Delete only proven dead internal code; reuse existing equivalent implementations without introducing general-purpose frameworks. Retain a candidate if new evidence disproves its safety and document why.
- **R3 — Compatibility:** retain existing application/import entry points, intentional facades and monkeypatch interfaces; HTTP/JSON/SSE shapes and errors; auth/CSRF/secret/SSRF policy; settings values/defaults; database schemas/SQL/transactions; job scheduling, leases, cancellation and recovery; UI interactions, parameter reuse, i18n, lazy loading and supported browser/runtime fallbacks.
- **R4 — Preservation:** do not alter or stage unrelated pre-existing untracked scaffolding, the unrelated `gpt-with-taste/` project, local data/images or existing user work.
- **R5 — Acceptance evidence:** characterize value-conversion edge cases before their implementation changes, validate affected behavior and run existing application checks. Keep existing bundle budgets. Distinguish environmental restrictions, baseline failures and introduced failures.
- **R6 — Complete handoff:** report selected changes, retained complexity and separate baseline defects with source anchors and usable test evidence; do not describe a structural scan as a line-by-line semantic audit.

## Acceptance Criteria

- **AC1 (R1, R6):** per-area research and synthesized decisions provide current source anchors, coverage/depth, callers, preserved contracts and validation paths for every selected edit.
- **AC2 (R2, R3):** selected cleanup is implemented or retained for documented new safety evidence; public interfaces, wire/data meaning and supported conditions of use remain intact. New helper tests characterize normalization/escaping and frontend converters on the old implementation and pass unchanged afterward.
- **AC3 (R3, R5):** affected focused tests and full backend/frontend unit suites pass without introduced failures; Svelte/TypeScript check, production build, unchanged bundle budgets and Worker build checks pass. Socket-restricted cases have successful loopback-capable evidence rather than a waiver.
- **AC4 (R3, R5):** gallery filtering/paging/batch selection, image edit/parameter reuse/retry, and prompt optimizer click/drag flows pass focused browser acceptance; actual workspace/gallery renders are inspected for unchanged visible behavior.
- **AC5 (R4, R6):** final source diff contains only approved cleanup and directly necessary regression tests/docs; handoff gives locations, valid checks and material unresolved issues. Existing unrelated lint diagnostics/bugs are not silently redefined as cleanup success.

## Out of Scope

Feature changes or bug fixes that change current behavior; dependency upgrades; framework replacement; UI/visual redesign; migration squashing or data/schema conversion; deployment/release default changes; release/deployment publishing; mass formatting/lint-policy expansion; removal of supported legacy behavior solely for being old. Existing live SSE logger defect and type-only annotation issue remain separately recorded in `research/summary.md`.

## Key Decisions

Prefer small deletion and reuse of established helpers over new abstractions or broad module splitting. Static unused diagnostics alone do not prove imports are removable. Keep prior intentional backend divergence and frontend lazy/lifecycle boundaries. The user explicitly approved the final planning summary and implementation on 2026-10-18, including phased commits and pushes to the existing GitHub remote without a PR. No blocking product decisions remain.
