# Design: backend implementation-level review

## Review dimensions (what counts as "outdated or unreasonable")

Findings must map to one of these categories, and each must be either a **defect** (demonstrable wrong behavior on current code) or an **improvement** (structural debt without demonstrated wrong behavior):

1. Mixed responsibilities — one module owning multiple unrelated domains (predecessor F4 pattern).
2. Layering violations — imports against the layer matrix (core→repositories, runtime→repositories etc.; check both static and deferred imports).
3. Dead/obsolete code — unreachable paths, unused exports, superseded mechanisms kept alive.
4. Duplication — copy-paste logic across modules that an existing shared helper already covers or should cover.
5. Outdated patterns — pre-refactor idioms surviving next to the new architecture (e.g., direct SQLite access bypassing the repositories/db facade, raw locks where leases exist).
6. Complexity hotspots — functions >150 lines, >4 nesting, god-modules >1000 lines doing orchestration + policy + I/O.
7. Async-correctness smells — blocking calls in async paths, unbounded queues, missing cancellation accounting (predecessor F2 pattern).
8. Convention drift — code contradicting documented specs (db-execution.md, coordination.md, http-compression.md, quality-guidelines).

## Grouping and dispatch

Nine backend groups (PRD R1.1–R1.9) + one frontend quick-scan group. Each group is dispatched as a `trellis-research` sub-agent that reads every module in its group and persists findings to `research/<group>.md`. Groups are sized 2.5K–6K lines each.

Known context each agent receives: predecessor audit's deferred items must not be re-reported; coordination/, db/execution, and the runtime↔repositories boundary were refactored on 10-13 — verify facades stayed clean rather than re-auditing them from scratch.

## Priority rubric

- P1 defect: wrong behavior demonstrable on current code (reproduce or prove by code path).
- P2 structural debt: no demonstrated wrong behavior, but the design actively impedes correctness work or maintenance (mixed domains, layering violations, hotspot orchestration).
- P3 improvement: maintainability/consistency opportunity, safe to defer.

## Synthesis flow

1. Per-group research files (evidence layer).
2. `research/report.md` — deduplicated findings, category, defect/improvement label, file:line anchors, impact, remedy sketch.
3. `refactor-checklist.md` (task root) — the actionable subset: ordered by priority, each with effort/risk/verifiable acceptance criterion/follow-up-task shape. Items explicitly not recommended now (e.g. F5/F6-style large splits without counterexamples) are listed as "keep deferred" with reasons.

## Compatibility

Review only — no product code changes. Tests are read as evidence (regression coverage tells risk of refactoring each area) but not run against modified code because nothing is modified. Baseline validation runs (existing suite) may be executed read-only to confirm the tree is green before reporting.

## Rollback

Not applicable — no product changes. Documents can be re-edited freely.
