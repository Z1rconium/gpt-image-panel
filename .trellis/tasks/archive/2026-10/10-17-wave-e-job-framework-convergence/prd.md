# Wave E: shared background-job runtime

## Goal

Extract a shared background-job runtime — claim loop + lease holder + progress/SSE
publisher — configured by both the image-job and gallery-job systems, so the two
parallel frameworks converge on one implementation. This is the highest-value and
highest-risk item in the checklist and requires explicit sign-off before starting.

| ID | Finding | Change |
|---|---|---|
| E1 | F-G7-1 | Extract a shared background-job runtime used by both image jobs and gallery jobs. |

## Background

Source: `.trellis/tasks/10-15-backend-review-refactor/refactor-checklist.md` (Wave E),
`research/report.md`, `research/g5-services-pipeline.md`, `research/g7-services-gallery.md`.
Parent: `10-15-backend-review-refactor`.

Two parallel frameworks re-implement the same primitives (admission, claim,
lease+lost, progress throttle, SSE fan-out, cooperative cancel):

| Side | Modules |
|---|---|
| image | `services/job_queue.py`, `services/claim_loop.py`, `services/image_unit_session.py`, `services/job_events.py` |
| gallery | `services/gallery_jobs.py`, `services/coordination/gallery_jobs.py`, `services/gallery_job_shared.py`, `services/gallery_job_sse.py` |

Note: Wave B already extracted a shared `services/lease_renewal.py` holder — E1
builds on it rather than duplicating it.

## Requirements

- Migrate each system **separately**, behind its existing tests; do not rewrite
  both at once.
- Preserve observable semantics: job SSE names/statuses, lease semantics, progress
  throttle cadence, and cooperative cancel behavior.
- Parameterize the runtime by the callbacks that genuinely differ (admission,
  work unit, lost handling) rather than forcing a one-size-fits-all shape.
- Where parallel-run equivalence is feasible, demonstrate it; otherwise document
  the migration boundary and its regression coverage.

## Acceptance Criteria

- AC1: one shared runtime module/primitive implements claim + lease + progress/SSE
  publication; both sides configure it.
- AC2: image-job and gallery-job regression tests pass with no behavior change to
  job SSE names/statuses or lease semantics.
- AC3: backend suite ≥ baseline green; layer matrix 0 violations.
- AC4: each system migrated independently with its own commit and green test run.
- AC5: a written sign-off note (design.md) records the risk assessment and the
  rollback path before implementation begins.

## Out of Scope

- Changing job semantics, SSE contracts, lease durations, or cancellation policy.
- New background-job consumers beyond image jobs and gallery jobs.
- Dependency upgrades or feature work.

## Gate

Do not start this task until the user explicitly approves the `design.md` risk
assessment. Wave D may proceed independently.
