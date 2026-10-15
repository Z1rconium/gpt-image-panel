# G5 services (generation/edit pipeline) review — baseline a50da7a

## Modules reviewed (key)
| Module | Lines | Notable |
|---|---|---|
| services/presets.py | 966 | secret resolution + preset CRUD + 4 feature-settings domains + serialization |
| services/image_unit_execution.py | 676 | ImageUnitExecutionContext (25 methods) |
| services/job_events.py | 621 | state + persistence + SSE + webhook |
| services/job_queue.py | 603 | admission/request construction |
| services/nodeimage_upload_jobs.py | 543 | _run_nodeimage_upload_job(221,nest 5) |
| claim_loop.py, job_executor.py(11), job_scheduler.py, image_unit_session.py, edit_masks.py, edit_paste_back.py, edit_sources.py, image_job_aggregate.py, runtime_metrics.py, startup_maintenance.py, uploads.py, vision_previews.py, webhook_service.py, poll_backoff.py, preset_package.py, provider_mapping_assist.py, dispatchers.py, job_cancel.py | — | — |

## Lifecycle call chain (map)
`job_queue.queue_image_job` (admission, `:380` 171 lines) → `claim_loop.run_claim_loop` claims units `→ job_scheduler.run_image_unit_dispatcher` → `job_executor.run_claimed_image_unit` (thin entry, 11 lines) → `image_unit_session.ImageUnitSession` (lease/progress) + `image_unit_execution.ImageUnitExecutionContext` (prepare/start/run_upstream/complete/fail/cancel/cleanup) → `job_events` (state + SSE publish) → `image_job_aggregate.derive_parent_update`. Boundaries are coherent; the long functions are inside that chain.

## Findings

### F-G5-1 — P2, improvement: presets.py aggregates four independent concerns
- Evidence: `backend/app/services/presets.py` — (a) credential/secret handling `mask_key:71`, `resolve_api_key:91`, `secret_response_fields:165`, `validate_configured_secret_bindings:802` (62); (b) preset CRUD/serialization `get_api_presets:325`, `serialize_api_preset:537`, `reorder_api_presets:520`; (c) four feature-settings domains `apply_prompt_optimizer_settings:866`, `apply_ai_assistant_settings:895`, `apply_r2_backup_settings:917`, `apply_nodeimage_settings:953` plus their normalize/response builders (`:205-255`, `:599-676`); (d) env-ref migration inventory `list_env_ref_migrations:765`.
- Impact: no single owner for "settings resolution"; a change in any of four features lands in the same file as credential masking.
- Remedy sketch: split into `presets`, `credentials/secrets view`, and per-feature settings modules, or at minimum extract the feature-settings builders.
- Effort/risk: medium / medium (settings responses are user-visible; broad regression surface).

### F-G5-2 — P2, improvement: job_events.py mixes four responsibilities
- Evidence: `backend/app/services/job_events.py` module docstring "Generation job state, persistence, and SSE publication": preview cache `:51-120`, SSE publication `:143 publish_job_edges`, per-process memory/staleness `:202-297`, **webhook dispatch** `:365-423` (`validate_job_webhook_url`, `dispatch_job_webhook_async`, task tracking), job view overlay + **persistence** `:487 store_generate_job`, `:510 store_generate_job_async` (59).
- Impact: webhook delivery and job persistence are unrelated to SSE publication but share the module; state ownership is spread across in-memory + storage in one file.
- Remedy sketch: separate state/publication from persistence, and move webhook dispatch to `webhook_service.py` (which already exists).
- Effort/risk: medium / medium.

### F-G5-3 — P2, improvement: five near-identical lease-renewal loops
- Evidence: `services/image_unit_session.py:148 renew_lease_loop`, `services/assistant_runtime.py:247 _renew_assistant_slot_loop`, `services/nodeimage_upload_jobs.py:341 renew_lease` (nested), `services/agent_run_lifecycle.py:43 _renew_lease_loop`, `services/gallery_job_shared.py:49 _sleep_while_renewing_background_lease`.
- Impact: each re-implements `asyncio.create_task` + periodic renew + "lost → cancel main" + cleanup. Fixes (e.g. backoff, cancellation semantics) must be applied five times; they already differ subtly.
- Remedy sketch: a shared "renewing lease/slot holder" helper parameterized by renew/release callbacks and a lost-handler.
- Effort/risk: medium / medium.

## Coverage notes
- `job_queue.queue_image_job` (171, `:380`) and `nodeimage_upload_jobs._run_nodeimage_upload_job` (221, `:322`, nest 5) are long orchestrators; they did not show duplicated logic against each other but are candidates for internal extraction if touched.
- Cancellation accounting from 10-13 verified still present (`runtime_metrics.py`); not re-reported.
