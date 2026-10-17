# G7 services (gallery operations) review — baseline a50da7a

## Modules reviewed
| Module | Lines | Notable |
|---|---|---|
| services/gallery_jobs.py | 106 | dispatchers |
| services/gallery_job_shared.py | 133 | lease/publish helpers |
| services/gallery_job_sse.py | 233 | per-process SSE fan-out |
| services/gallery_archive_export.py | — | _prepare_gallery_zip_stream(141), _gallery_zip_response(203) |
| services/gallery_archive_import.py | — | zip validation + streaming import |
| services/gallery_archive_shared.py, gallery_common.py, gallery_export_jobs.py(_gallery_zip_response 203), gallery_import_jobs.py(_run_gallery_import_job 121), gallery_job_payloads.py, gallery_maintenance.py(gc_gallery_export_jobs 110, nest 6), gallery_sync_jobs.py | — | — |

## Findings

### F-G7-1 — P2, improvement: two parallel job-lifecycle frameworks (image jobs vs gallery jobs)
- Evidence — image side: `services/job_queue.py` (admission), `services/claim_loop.py` (claim), `services/image_unit_session.py` (lease renew), `services/job_events.py` (progress+SSE publish), `repositories/coordination/leases.py`, `repositories/coordination/sse_slots.py`.
- Evidence — gallery side: `services/gallery_jobs.py` (dispatch), `repositories/coordination/gallery_jobs.py` (admission/claim/lease/cancel), `services/gallery_job_shared.py:49 _sleep_while_renewing_background_lease` + publish helpers, `services/gallery_job_sse.py` (per-process SSE fan-out).
- Both implement the same primitives: capacity admission, `claim_next_*`, lease renew + lost-handling, progress publishing (with a throttler), SSE subscriber fan-out, cooperative cancellation.
- Impact: two homes for "background job" mechanics; a fix to lease/progress/SSE semantics applies twice; contributors must learn both. 10-13's coordination refactor unified persistence ownership but the **service-layer** job frameworks remain parallel.
- Remedy sketch: extract a small shared background-job runtime (claim loop + lease-renew holder + progress/SSE publisher) that both image units and gallery jobs configure. High value, but highest-effort item in this report.
- Effort/risk: high / high (both job systems are load-bearing; needs careful side-by-side migration with regression tests on each).

### F-G7-2 — P3, improvement: ZIP export/import long functions
- Evidence: `services/gallery_archive_export.py:62 _gallery_zip_response` (203), `:189 _prepare_gallery_zip_stream` (141); `services/gallery_archive_import.py` mirrors import-side streaming; `services/gallery_import_jobs.py:79 _run_gallery_import_job` (121).
- Impact: ZIP streaming paths are large and duplicated in shape between export and import; `gallery_archive_shared.py` exists but does not absorb the streaming loops.
- Remedy sketch: shared ZIP streaming/validation helpers; extract stage callbacks.
- Effort/risk: medium / medium.

## Coverage notes
- `gallery_job_shared.py` / `gallery_job_sse.py` correctly use the coordination leases and `runtime/blocking` for file/zip work; no direct sqlite3 and no event-loop blocking found (`gc_gallery_export_jobs`, `run_gallery_r2_scheduled_sync` run their sleeps/long work off-loop or via bounded loops).
- F-G7-1 is the anchor finding for the "duplication" dimension; F-G7-2 is subordinate to it.
