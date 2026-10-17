# Wave C: services module splits

## Goal

Split four services modules along their mixed concerns, with no external behavior
change:

| ID | Finding | Change |
|---|---|---|
| C7 | F-G6-2 | Split `assistant_vision.py` prompt-optimization-with-preview from read-only gallery vision analysis. |
| C6 | F-G6-1 | Split `assistant_runtime.py` endpoint/agent resolution from slot leasing from JSON transport. |
| C1 | F-G5-1 | Split `services/presets.py` into presets CRUD / credential view / per-feature settings builders. |
| C2 | F-G5-2 | Split `services/job_events.py`: state+SSE publication vs persistence vs webhook dispatch (to `webhook_service.py`). |

## Background

Source: `refactor-checklist.md` (Wave C), `research/report.md`, `research/g5-services-pipeline.md`,
`research/g6-services-agent-assistant.md`. Parent: `10-15-backend-review-refactor`.
Branch: `refactor/wave-b-c`. C6 depends on the Wave B lease holder (Wave B must land first).

## Requirements

- Pure moves/extractions; preserve every public function's signature and behavior.
- C1: API responses byte-identical (golden/shape tests); no route changes.
- C2: keep job SSE event names/statuses and persistence semantics; webhook dispatch
  behavior unchanged after moving to `webhook_service.py`.
- C6: slot-lease behavior unchanged (reuses the Wave B holder); JSON transport and
  agent resolution behavior unchanged.
- C7: preview generation and gallery analysis keep identical inputs/outputs.
- Update import sites; keep linted files F401-clean; layer matrix unchanged.

## Acceptance Criteria

- AC1 (C7): assistant vision tests pass; both call paths behaviorally identical.
- AC2 (C6): assistant + agent runtime tests pass; slot-lease behavior unchanged.
- AC3 (C1): settings + preset API responses byte-identical; tests pass; no route changes.
- AC4 (C2): job SSE/persistence tests pass; webhook dispatch tests pass.
- AC5: backend suite >= baseline green; layer matrix 0 violations; ruff clean.

## Out of Scope

- Behavioral changes, new features, route/payload changes.
- Wave D/E items.
