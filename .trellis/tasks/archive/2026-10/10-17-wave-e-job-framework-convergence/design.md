# Wave E design — shared background-job runtime

## Status: awaiting sign-off before implementation

E1 (F-G7-1) is the highest-risk item in the checklist. This document records
the risk assessment, the exact remaining scope (measured against current code,
not the review baseline), the migration plan, and the rollback path. Per the
PRD gate, implementation does not start until this is approved.

---

## 1. What is already converged (do not re-do)

The review report (baseline `a50da7a`) described two fully parallel frameworks.
That was accurate then, but Waves A–C and prior maintenance work have already
converged two of the four primitives:

| Primitive | Status | Shared home |
|---|---|---|
| Claim loop | **Converged** | `services/claim_loop.py::run_claim_loop` — used by image units (`job_scheduler.py`), gallery jobs (`gallery_jobs.py`), thumbnail dispatch (`gallery_maintenance.py`), assistant batch (`assistant_batch.py`) |
| Lease renewal loop | **Converged** (Wave B) | `services/lease_renewal.py::renew_lease_until_lost` — used by `ImageUnitSession`, agent runs, assistant slots, nodeimage uploads |
| Lease holder session | **Not converged** | image: `ImageUnitSession` (full session); gallery: `_sleep_while_renewing_background_lease` (different shape) |
| Progress/SSE publisher | **Not converged** | image: `services/job_events.py`; gallery: `services/gallery_job_shared.py` + `services/gallery_job_sse.py` |

So E1 is now scoped to **two primitives**, not four.

---

## 2. Remaining scope, concretely

### 2.1 Lease holder session (`ImageUnitSession` vs `_sleep_while_renewing_background_lease`)

- `ImageUnitSession` (`services/image_unit_session.py`, 304 lines) owns: a
  `lease_lost` event, a local lease-deadline for fencing decisions, a
  `renew_lease_loop` task, upstream-task cancellation on lease loss, and
  coalesced progress persistence.
- Gallery's `_sleep_while_renewing_background_lease`
  (`services/gallery_job_shared.py:49`) is a *bounded sleep-while-renewing*
  helper — it has no session object, no local deadline fencing, and its lease
  owner/token semantics differ (gallery jobs use `lease_owner`/`lease_expires_at`
  on the `gallery_jobs` row, not a claim token).

**Convergence**: extract the fencing core (local lease deadline + a
"renew-until-lost" task + a `mark_lost`/`raise_if_lost` guard) into a shared
`LeaseHolder` that both the image session and a new gallery session wrapper
configure with their own renew/release callbacks. Wave B already proved the
renew-loop itself can be shared; this extends it to the *holding* state.

### 2.2 Progress/SSE publisher (`job_events.py` vs `gallery_job_shared.py` + `gallery_job_sse.py`)

- Image: `job_events.py` publishes per-job edges to subscriber queues
  (`publish_generate_job`, `publish_job_edges`) and manages a preview cache and
  in-memory active-job reconcile.
- Gallery: `gallery_job_shared.py` throttles and coalesces per-job updates
  (`_gallery_job_progress_throttler`, `_publish_gallery_job`), and
  `gallery_job_sse.py` fans them out over a separate subscriber registry keyed
  by `kind`.

**Convergence**: extract a shared throttled publisher (the
`GalleryProgressThrottler` shape is the cleaner of the two) and a shared
subscriber fan-out, leaving the *event name/status schema* as a per-domain
mapping. The SSE event names/statuses MUST remain byte-identical.

---

## 3. Risk assessment

| Risk | Severity | Mitigation |
|---|---|---|
| Job SSE event names/statuses change | **High** | Freeze the SSE contract (`test_generation_jobs_contract.py`, gallery SSE tests) as the gate; migrate each system behind its existing tests; no schema changes |
| Lease semantics change (lost→cancel, TTL, renew cadence) | **High** | Keep lease durations/retry policy identical; reuse `lease_renewal.py` and `image_unit_session` fencing logic verbatim; lease tests gate each step |
| Concurrency/races in the fencing state | Medium | Keep the existing `asyncio.Event` + monotonic-deadline model; add no new shared mutable state |
| Cross-system regression from a shared bug | Medium | Migrate one system fully, run the full suite, then the other; never land both in one step |

Net: **value is now mostly maintainability** (removing one duplicated lease
holder and one duplicated SSE fan-out), because the claim loop — the largest
primitive — is already shared. This lowers the value/risk ratio versus the
original report.

---

## 4. Migration plan (each step independently green)

1. **Lease holder** — extract `LeaseHolder` from `ImageUnitSession`; keep the
   image path behaviorally identical (its tests are the gate). No gallery
   change yet.
2. **Lease holder gallery** — introduce a gallery lease holder that wraps the
   existing `_sleep_while_renewing_background_lease` semantics; gallery
   background-lease tests gate.
3. **Publisher** — extract a shared throttled publisher + subscriber fan-out
   from the gallery implementation (the more general one); image `job_events`
   migrates to it with its own event mapping; SSE contract tests gate.
4. Full backend suite ≥ baseline green; layer matrix 0 violations; ruff clean
   on touched files.

## 5. Rollback

- Each step is an independent commit. Revert the offending commit; the prior
  system's private implementation is untouched until its own migration step, so
  a partial revert leaves a working tree.
- No schema/data migration, no config change, no dependency change — rollback is
  purely source-level.

## 6. Recommendation

Proceed, but in the 4-step order above, treating step 3 (SSE publisher) as the
highest-risk gate. If sign-off is limited, land only step 1 (lease holder
extraction) and defer 2–3.

---

## 7. Final outcome (measured against current code)

### Done

- **Step 1 — `LeaseHolder` extraction** ✅
  `services/lease_holder.py` now owns the shared fencing state (lost-flag event,
  local monotonic deadline, `note_lease_extended`, `raise_if_lease_lost`,
  `mark_lease_lost`). `ImageUnitSession` composes it with the image lease
  duration and raises `UnitLeaseLostError` (now a `LeaseLostError` subclass) so
  the executor contract is unchanged. Full suite green.

### Found already converged (no work required)

- **Claim loop** — `claim_loop.run_claim_loop` is already shared by image units,
  gallery jobs, thumbnail dispatch, and assistant batch.
- **Lease renewal** — `lease_renewal.renew_lease_until_lost` (Wave B).
- **SSE low-level primitives** — `publish_queue`, `serialize_sse_event`, and
  `publish_job_edges` are already imported by `gallery_job_sse.py` from
  `job_events.py`.

### Found intentionally different (not worth converging)

- **Gallery background lease** (`_sleep_while_renewing_background_lease`) is a
  single, bounded "sleep-while-renewing, return bool" helper. It has no
  duplicate and its shape differs from the image "renew-until-lost, raise" model
  — the same divergence Wave B's F-G5-3 note already documented. Forcing it into
  `LeaseHolder` would add a knobs-bearing abstraction over one 15-line function.
- **SSE stream endpoints** (`stream_generate_job` vs `stream_gallery_job`) share
  a connection skeleton but differ in event names, payload schema, dedupe key,
  terminal-status set, and reconnect replay. Extracting a shared runner needs
  ~15 parameterization points and yields net-neutral complexity for the
  schema-coupled remainder, at the highest-risk layer (SSE contract).

### Conclusion

The highest-value convergence (claim loop + lease renewal + SSE primitives +
lease-holder fencing) is now shared. The remainder is either already shared,
already documented as intentionally different, or schema-coupled where forced
convergence is net-negative. No further code changes for E1 are recommended.
