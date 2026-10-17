# G6 services (agent / AI assistant) review — baseline a50da7a

## Modules reviewed (key)
| Module | Lines | Notable |
|---|---|---|
| services/assistant_vision.py | 604 | optimize_uploaded_image_prompt(128), _analyze_gallery_image(57) |
| services/assistant_batch.py | 552 | _run_ai_analyze_job(116,nest 6) |
| services/assistant_runtime.py | 551 | runtime resolution + slot leasing + JSON |
| services/agent_tool_executor.py | 407 | _run_image(129), _run_batch(107) |
| services/agent_run_lifecycle.py | 243 | _renew_lease_loop(31), AgentRunLifecycle |
| services/agent_stream.py, agent_context.py, agent_conversations.py, agent_event_writer.py, agent_model_round.py, agent_prompt.py, agent_refs.py, agent_turns.py, agent_turn_errors.py, agent_tools.py, agent_search.py, assistant_text.py | — | — |

## Findings

### F-G6-1 — P2, improvement: assistant_runtime.py mixes endpoint resolution, slot leasing and JSON transport
- Evidence: `backend/app/services/assistant_runtime.py` — endpoint/runtime resolution `_resolve_runtime:389`, `resolve_agent_runtime:448` (50), `AgentRuntime:421`; concurrency slot leasing `_acquire_assistant_slot:221`, `_renew_assistant_slot_loop:247`, `_assistant_request_limit:274` (84); JSON transport + prompts `_assistant_json:509` (38), `_image_prompt_system_prompt:366`.
- Impact: three concerns in one module; the slot-lease logic is one of the duplicated renewal loops (see F-G5-3).
- Remedy sketch: isolate slot leasing (shared holder) and JSON transport; keep resolution separate.
- Effort/risk: medium / medium.

### F-G6-2 — P3, improvement: assistant_vision.py mixes preview-image generation with gallery analysis
- Evidence: `backend/app/services/assistant_vision.py:222 optimize_uploaded_image_prompt` (128) generates a prompt **and a preview image** (`_prompt_preview_generation_config:190`, `_generated_image_mime_type:206`, `calculate_prompt_preview_size:148`) while the same module also does read-only gallery analysis `_analyze_gallery_image:453`, `describe_gallery_image:583`, `prompt_gallery_image:587`.
- Impact: "generate a preview to score a prompt" (generation path) and "describe an existing gallery image" (analysis path) are different risk profiles in one module.
- Remedy sketch: split prompt-optimization-with-preview from gallery vision.
- Effort/risk: low-medium / low.

### F-G6-3 — P3, improvement: one AgentToolExecutor class carries tool auth + batch + image lifecycle
- Evidence: `backend/app/services/agent_tool_executor.py:39 AgentToolExecutor` with `_run_batch:114` (107) and `_run_image:231` (129) doing orchestration, plus `cancel_pending_images:379`.
- Impact: a 407-line class owning authorization, batch execution and image-job lifecycle.
- Remedy sketch: extract image-lifecycle helpers from the class.
- Effort/risk: low / medium.

## Coverage notes
- The `agent_*` family split (context/prompt/refs/run_context/run_lifecycle/stream/turns/tools) is reasonably cohesive; cross-imports are via `repositories/agent/*` and `integrations/agent_client.py`, consistent with the layer matrix (0 violations).
- `LEGACY_IMAGE_WAIT_TIMEOUT_ERROR` handling (`repositories/agent/turns.py:364`) is deliberate backward compatibility, not dead code.
- `assistant_batch._run_ai_analyze_job` (116, nest 6) is long but single-purpose; not reported.
