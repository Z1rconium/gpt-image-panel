# Wave B: lease-renewal unification

## Goal

Implement checklist item **B1 / F-G5-3**: replace the five near-identical lease/slot
renewal loops with one shared, parameterized "renewing lease/slot holder", so a fix
(e.g. backoff or cancellation semantics) is made once instead of five times.

## Background

Source: `.trellis/tasks/10-15-backend-review-refactor/refactor-checklist.md` (Wave B),
`research/report.md` (F-G5-3), `research/g5-services-pipeline.md`. Parent:
`10-15-backend-review-refactor`. Baseline branch: `refactor/wave-b-c` (stacked on
`refactor/wave-a-dedupe-hygiene`).

The five sites:

| Site | Shape |
|---|---|
| `services/image_unit_session.py:148 renew_lease_loop` | persistent task; computed interval + short retry-until-deadline on transient errors; `mark_lease_lost` on False |
| `services/assistant_runtime.py:247 _renew_assistant_slot_loop` | persistent task; fixed interval; log+continue on error; `on_lost()` on False |
| `services/agent_run_lifecycle.py:43 _renew_lease_loop` | persistent task; bounded by `expires`; `main.cancel()` on False |
| `services/nodeimage_upload_jobs.py:341 renew_lease` | persistent task; stop-event driven; `lease_lost.set()` on False |
| `services/gallery_job_shared.py:49 _sleep_while_renewing_background_lease` | bounded sleep-while-renewing helper (returns bool) |

## Requirements

- One shared holder/helper, parameterized by: the renew callable (returns bool),
  the interval, the lost handler, and the transient-error policy.
- All five sites use it. Each site keeps its exact observable semantics: renew
  cadence, retry/abandon behavior, and what "lost" does (mark lost, cancel main,
  set an event, or return False).
- No change to lease TTLs, renewal cadence constants, SSE/cancel semantics, or
  logging/metrics behaviour that tests assert.

## Acceptance Criteria

- AC1: one shared module/primitive implements the renewal loop; no site keeps a
  private copy of the loop skeleton.
- AC2: each site's existing tests pass unchanged (image units, agent runs,
  assistant slots, nodeimage upload, gallery background lease).
- AC3: backend suite ≥ baseline green; layer matrix green.
- AC4: if a site genuinely cannot use the shared holder without behavior change,
  document why and leave it, rather than forcing it.

## Out of Scope

- Wave C module splits, Wave D hotspots, Wave E job-framework convergence.
- Changing lease durations, retry counts, or cancellation semantics.
