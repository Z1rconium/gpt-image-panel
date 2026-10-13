# Architecture audit and legacy module refactoring

## Goal

Improve maintainability and asynchronous correctness through a source-backed repository audit and a first batch of legacy module refactors.

## Background

The user requested architecture/design review and refactoring and authorized task creation/planning on 2026-10-13. Baseline: main at 914418f. Preserve existing untracked Trellis/platform files and the separate Bootstrap Guidelines task.

The authoritative evidence with file:line anchors is research/audit.md: F1 stale frontend job lifecycle; F2 executor cancellation accounting; F3 reverse runtime/database dependencies; F4 mixed coordination responsibilities. F5-F7 are deferred improvements.

## Requirements and task map

| ID | Requirement | Owning child | Finding |
|---|---|---|---|
| R1 | Separate generic execution from SQLite policy and correct queued-cancellation metrics | ../10-13-database-runtime-refactor | F2, F3 |
| R2 | Separate coordination domains behind the established operation facade | ../10-13-coordination-modules-refactor | F4 |
| R3 | Separate global generation-job lifecycle from state and reject obsolete updates | ../10-13-generation-jobs-lifecycle-refactor | F1 |
| R4 | Validate compatibility and integration | Parent review | F1-F4 |
| R5 | Deliver an evidence-backed audit and scoped module contracts | Parent documentation | F1-F7 |

## Acceptance Criteria

- AC1 (R1): Runtime imports no repositories even inside functions; cancellation/submission failure leave correct gauges; SQLite scopes/retries/critical budgets/shutdown retain regression coverage.
- AC2 (R2): Coordination operation imports/signatures and SQL behavior remain compatible; five domains have clear module owners.
- AC3 (R3): Cleanup/replacement invalidates old sources and pending list requests; fresh updates, polling and visibility still work after reuse.
- AC4 (R4): Backend architecture/tests and frontend diagnostics/unit/build/budget/Worker/focused browser checks pass; record environmental limitations explicitly.
- AC5 (R5): Audit distinguishes reproduced defects from improvements and captures ownership/lifecycle contracts.

## Compatibility and Out of Scope

Preserve HTTP paths/payloads, SSE names/statuses, database schema/data, lease/transaction semantics, i18n and visual design. Preserve coordination's intended operation facade. Internal runtime DB helper import paths may move with every repository consumer/test updated together.

Exclude new features, dependency upgrades, new queue admission policy, deployment, destructive storage operations, mask changes and wholesale workspace/gallery UI rewrites. Keep bundle budgets unchanged.

## Planning Status

The user approved the latest first-batch summary on 2026-10-13. R1-R5 implementation and verification are complete on refactor/architecture-legacy-modules. Scoped contracts and validation records are written. The user subsequently approved split work commits and requested pushing the branch; all five work commits are complete. Trellis archival/session recording and the authorized push complete the repository handoff.
