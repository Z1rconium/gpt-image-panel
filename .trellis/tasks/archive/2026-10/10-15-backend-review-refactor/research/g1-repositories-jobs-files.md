# G1 repositories (jobs & files) review — baseline a50da7a

## Modules reviewed
| Module | Lines |
|---|---|
| repositories/image_jobs.py | 1379 |
| repositories/thumbnail_jobs.py | 619 |
| repositories/image_files.py | — |
| repositories/thumbnails.py | — |
| repositories/settings.py | — |
| repositories/sse_limiter.py | — |

## Findings

### F-G1-1 — P3, improvement: mask-file persistence lives in the job-DB module
- Evidence: `backend/app/repositories/image_jobs.py:37 delete_persisted_masks`, `:1249 list_persisted_mask_job_ids`.
- Impact: a filesystem concern (mask PNG files named `<job_id>.png`) is owned by the generate-job persistence module whose docstring is "Image generation and edit job queue persistence". Mild boundary blur; makes the module's responsibility harder to state.
- Remedy sketch: move mask-file path/lifecycle helpers beside the existing mask file handling (or a small `mask_files` repository) and keep image_jobs focused on rows.
- Effort/risk: low / low.

### F-G1-2 — P3, improvement: blocking CPU-slot acquire is a latent event-loop footgun
- Evidence: `backend/app/repositories/thumbnail_jobs.py:91 _thumbnail_cpu_slot` uses synchronous `time.sleep` (`:108`) inside a `@contextmanager`; single call site `:567` inside sync `generate_thumbnail_for_image`.
- Verified safe today: both entry paths hop to a thread first — `services/gallery_maintenance.py:104 asyncio.to_thread(generate_thumbnail_for_image, …)` and `api/routers/gallery_queries.py:394 asyncio.to_thread(_resolve_gallery_thumbnail_path, …)`.
- Impact: no current defect, but any future direct call from an async path blocks the loop up to ~1s per retry. The blocking primitive has no guard.
- Remedy sketch: keep as-is or add a one-line docstring contract ("must run off the event loop") and a debug assertion.
- Effort/risk: low / low.

## Coverage notes
- `image_jobs.py` (1379 lines) is **cohesive, not a god-module**: functions map 1:1 to job-lifecycle verbs (enqueue, claim, renew lease, progress, complete/fail, aggregate, cancel, trim, list, mask). Size reflects domain complexity (units + aggregate + leases), and the largest functions (`enqueue_image_job` :330 101, `claim_next_image_job_unit` :529 101) are single-purpose. Explicitly **not** reported as a finding — size alone is not evidence.
- No layer violations; no direct `sqlite3.connect` outside `repositories/db/connection.py`.
