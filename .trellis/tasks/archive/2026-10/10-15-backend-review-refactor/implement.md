# Implementation plan

## Ordered checklist

1. [ ] Confirm baseline: `git rev-parse HEAD` = `a50da7a`, `git status --porcelain` clean of tracked changes.
2. [ ] Dispatch research wave 1 (parallel, groups 1–5): jobs-and-files, upstream, gallery+coordination, db, generation/edit services. Each agent: read every module in its group, apply the 8 review dimensions from design.md, persist `research/<group>.md` with file:line anchors.
3. [ ] Dispatch research wave 2 (parallel, groups 6–10): assistant/agent services, api layer, core, schemas+runtime+integrations(non-upstream), frontend quick scan.
4. [ ] Cross-check: findings vs predecessor audit deferred items (F5–F8) — new evidence only; verify the 10-13 refactored areas (coordination facades, db/execution ownership, runtime import cleanliness) stayed clean.
5. [ ] Synthesize `research/report.md` (dedupe, categorize, label defect/improvement, prioritize).
6. [ ] Write `refactor-checklist.md` at task root: ordered items, each with priority / impact / remedy sketch / effort / risk / verifiable acceptance criterion.
7. [ ] Read-only baseline validation (optional): backend pytest suite green confirmation to anchor "current tree is healthy" claims in the report.
8. [ ] Present report + checklist to user; user selects items; propose follow-up task creation (one task per selected item or coherent batch).

## Validation commands

- `git rev-parse --short HEAD && git status --porcelain` (baseline check)
- Backend suite (read-only): `cd backend && python -m pytest -q` (use `.venv`; expect ~945 passed / 22 skipped baseline from 10-13 record)
- Report self-check: every finding has file:line anchor; every P1/P2 checklist item has a verifiable acceptance criterion; no finding duplicates F5–F8 without new evidence.

## Risky points / rollback

- No product code is modified; no rollback needed. Documents under the task dir are freely editable.
- Research agents are read-only for product code; they must only write under `.trellis/tasks/10-15-backend-review-refactor/research/`.

## Follow-up before task.py start

- User approves the final planning summary (review-only scope, group split, deliverables).
