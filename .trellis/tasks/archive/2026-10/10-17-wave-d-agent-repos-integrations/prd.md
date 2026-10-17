# Wave D: agent / repositories / R2 extraction

## Goal

Extract the smaller, independently-verifiable hotspots in the agent, repositories
jobs/files, and R2 areas, with no external behavior change:

| ID | Finding | Change |
|---|---|---|
| D6 | F-G6-3 | Extract image-lifecycle helpers from `AgentToolExecutor`. |
| D7 | F-G1-1 | Move mask-file helpers out of `repositories/image_jobs.py`. |
| D8 | F-G1-2 | Document/guard the off-loop contract of `_thumbnail_cpu_slot`. |
| D9 | F-G10-3 | Route R2 credential reads through `core/secrets`. |

## Background

Source: `.trellis/tasks/10-15-backend-review-refactor/refactor-checklist.md` (Wave D),
`research/report.md`, `research/g1-repositories-jobs-files.md`, `research/g6-services-agent-assistant.md`,
`research/g10-schemas-runtime-integrations.md`. Parent: `10-15-backend-review-refactor`.

Baseline anchors from `a50da7a` (10-15 review); re-locate before editing:

- **D6**: `services/agent_tool_executor.py:39` class; `_run_batch:114` (107), `_run_image:231` (129) — auth + batch + image lifecycle.
- **D7**: `repositories/image_jobs.py:37` and `:1249` — mask-file persistence in the job-DB module.
- **D8**: `repositories/thumbnail_jobs.py:91 _thumbnail_cpu_slot` (`time.sleep:108`) — blocking CPU-slot acquire is a latent event-loop footgun; callers verified safe today (`gallery_maintenance.py:104`, `gallery_queries.py:394/357` use `asyncio.to_thread`).
- **D9**: `integrations/r2/config.py` — a second credential-env read path overlapping `core/secrets`.

## Requirements

- Pure moves/extractions; preserve public function signatures and behavior.
- D6: keep `AgentToolExecutor` auth + batch semantics; extract only image-lifecycle helpers.
- D7: mask-file helpers moved to a dedicated module; mask tests pass.
- D8: add a docstring/assertion documenting that `_thumbnail_cpu_slot` must only be
  called off-loop (via `asyncio.to_thread`); thumbnail tests pass.
- D9: R2 credential reads go through `core/secrets`; R2 config tests pass.
- Update import sites; keep linted files F401-clean; layer matrix unchanged.

## Acceptance Criteria

- AC1 (D6): agent tool-execution tests pass.
- AC2 (D7): mask tests pass.
- AC3 (D8): docstring/assertion added; thumbnail tests pass.
- AC4 (D9): R2 config tests pass.
- AC5: backend suite ≥ baseline green; layer matrix 0 violations; ruff clean.

## Out of Scope

- Behavioral changes, new features.
- Wave D items D1–D5 (tracked in `10-17-wave-d-db-upstream-gallery`) and Wave E.
