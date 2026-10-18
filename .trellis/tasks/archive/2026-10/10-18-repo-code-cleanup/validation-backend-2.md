# Batch 2: finite stale import/local cleanup

## Change boundary

B4 only: remove screened copied imports from gallery query/batch/task routers and assistant batch/text implementations, delete the exact duplicate assistant DTO import, remove the unused lifespan local and unused prompt-health exception binding. Keep module-attribute test seams and import-time initialization reachable through live owners. No algorithm, route/auth/SSE/default, facade, schema, dependency, data or unrelated defect changes. Frontend and main-session documentation edits belong to other owners; Git operations and integrated acceptance belong to the main session.

The maintenance gap is stale imports after earlier extraction, not a behavior defect. Validation uses external consumer/reflection screening, initialization/import-graph checks, source-shape comparison and the existing focused contracts/architecture checks. Changes are confined to the eight approved implementation paths and this record; no new helper or framework is needed.

## Pre-edit evidence

- Batch 1 was committed/pushed by the main session as `8fa3cbe`; backend files were unchanged at the start of Batch 2. Existing frontend/spec changes and untracked scaffolding are outside this worker's scope.
- Read updated `backend/refactoring-compatibility.md`, task PRD/design/implementation/research and the complete eight targets, plus module composition, process state initialization and affected test/support consumers.
- Scoped Ruff baseline across the eight files: **288 findings** = **284 F401**, **2 F811** (exact duplicate DTO imports), **2 F841** (lifespan local, health exception binding), zero F821.
- Repository source and tests show gallery router imports composed through `api/routers/gallery.py`; assistant implementations are registered by `api/routers/assistant.py`; dispatchers consume `assistant_batch.run_ai_analyze_dispatcher`. These active imports and registration bodies stay unchanged.
- Existing external seams include `assistant_batch.assistant_client` via `tests/support/contract.py:25` and assistant contracts; `assistant_text.assistant_client` in image-model tests; gallery query `run_db_operation`, `get_gallery_page`, `upload_image_file`, `_resolve_gallery_image_path` and `kick_thumbnail_dispatcher`; gallery batch `NODEIMAGE_BATCH_MAX`; gallery task cleanup helper. All are retained. Favorite/DB/coordination and test/support facades are not cleanup targets.

## Validation

### Implemented changes

| File under `backend/app/` | Removal / retained interface |
|---|---|
| `api/routers/gallery_queries.py` | 80 unused import aliases removed; retain active query/media/NodeImage dependencies and all observed patches |
| `api/routers/gallery_batch.py` | 75 unused import aliases removed; retain batch selection/download/upload dependencies and batch-limit patch |
| `api/routers/gallery_tasks.py` | 59 unused import aliases removed; retain import reservations, direct/tracked export, cancellation and SSE dependencies |
| `services/assistant_batch.py` | 35 unused import aliases removed; `assistant_client` retained as an explicit same-name re-export, documenting the existing patch surface |
| `services/assistant_text.py` | 34 unused import aliases removed; retain live transport alias, DTOs, diagnostic redaction and runtime resolution |
| `api/routers/assistant.py` | Remove the earlier duplicate import of `AssistantImagePromptResponse` and `AssistantImagePromptOptimizeResponse`; existing grouped import retains both names |
| `api/app.py` | Remove only unused `worker = state.worker_id`; `init_defaults()` still sets worker state and all startup/shutdown operations remain unchanged |
| `api/routers/prompt.py` | Remove only unused `as e` in the unexpected health-probe exception branch; keep the handler, exception logging and response unchanged |

Total: **283 stale import aliases removed** from the five selected blocks. The remaining original F401 alias is preserved explicitly, not deleted. No other namespace/facade cleanup and no new test file were needed; existing tests exercise the observed interfaces.

### Initialization and source-shape evidence

- Screened direct/aliased module imports, module-attribute access, multiline monkeypatch targets, dynamic import/reflection markers and module registrations in source/tests; repository-wide source/document searches add no consumer of the removed aliases. Selected modules have no dynamic `__all__`/registry exposing the copied import blocks. Support's `gallery_queries` is the repository module, distinct from `gallery_queries_router`; repository functions/facades remain untouched.
- Inspected initialization of removed local import edges: archive helpers contain definitions/constants, job-event subscriber setup is lazy, `sse_limiter` owns a singleton, and assistant runtime owns ContextVars. Live service owners still import these dependencies (`gallery_common`, gallery export/import/job SSE services, assistant runtime/vision). A fresh interpreter import of `backend.app.main` before and after edits loads the **same 200 local modules**, with no added/removed module. This confirms the existing singleton/ContextVar owner modules remain initialized through active imports.
- Fresh app **OpenAPI is identical before/after** (all **99 paths**, schemas and operation definitions compared as complete objects). Router composition and decorators/registration bodies are unchanged.
- AST comparison for the five import-cleanup files, excluding only top-level import statements, is exactly equal before/after. Their functions, annotations/defaults, constants and registration bodies are unchanged. All eight edited sources compile successfully.
- Runtime identity check: `assistant_batch.assistant_client is assistant_text.assistant_client is integrations.assistant_client` — passed.

### Focused tests and lint

```sh
.venv/bin/python -m pytest backend/tests/test_architecture_boundaries.py backend/tests/test_assistant_contract.py backend/tests/test_gallery_media_contract.py backend/tests/test_gallery_collections_contract.py backend/tests/test_nodeimage_upload_contract.py -q
```

**120 passed**, 25.99 seconds. Covers gallery APIs/exports/collection flows, NodeImage upload/cancellation, assistant HTTP/batch/lease and existing monkeypatch seams, plus architecture. No socket/sandbox restriction in this subset.

```sh
.venv/bin/python -m pytest backend/tests/test_access_settings_contract.py -k prompt_optimizer -q
```

**5 passed, 94 deselected**, 0.41 seconds. Adds the affected prompt-optimizer health/settings contracts. Both runs report only the same two pre-existing TestClient deprecation warnings.

```sh
.venv/bin/ruff check backend/app/api/routers/gallery_queries.py backend/app/api/routers/gallery_batch.py backend/app/api/routers/gallery_tasks.py backend/app/services/assistant_batch.py backend/app/services/assistant_text.py backend/app/api/routers/assistant.py backend/app/api/app.py backend/app/api/routers/prompt.py --select F401,F841,F811,F821 --no-cache --output-format concise
```

**Passed, zero diagnostics**, down from 288 scoped findings. No retained scoped diagnostic: the intentional `assistant_client` seam uses explicit re-export syntax. This is not a global-lint result; unselected DB/facade imports and separate baseline logger/type findings remain untouched.

`git diff --check --` on the eight source paths passed. Source review contains import changes, one unused-local deletion and one unused exception-binding deletion only; no new dependency or runtime-data change.

## Handoff

Batch 2 is ready for main-session review and the next phased commit/push. Changed paths are the eight source files listed above and this record. No Git write operation or full backend suite was performed by this worker. Final integrated backend acceptance remains with the main session; frontend and its acceptance remain with their assigned owners. The original behavior-preserving scope and acceptance meanings are unchanged.
