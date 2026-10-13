# Architecture audit at 914418f

## Method and scope

Inspected the backend layer matrix, runtime execution and shutdown, SQLite connection/migration facades, coordination and image job repositories, API composition, services, frontend job/gallery stores, existing Agent stream extraction, workspace lifecycle, tests, and CI. Scanned all 178 backend Python modules and 141 frontend source modules for size and import relationships; this is a module/dependency audit, not a comprehensive security assessment or a line-by-line review of every implementation.

Backend layering and existing extraction patterns are useful foundations: repositories/agent and schemas/provider already use stable re-export facades; frontend agentState/agentStream separate state from stream lifecycle. Preserve those patterns.

## Confirmed findings

### F1 — P1: stale global job callbacks and responses mutate current state

Evidence: frontend/src/lib/stores/jobs.ts:164 (loadJobs), :306 (startJobsEvents), :555 (cleanup). Tracked jobs already use trackedJobGeneration and history uses historyRequestSeq, but the global job list has neither. onopen guards source identity; onEvent, onNetworkError and onError do not.

Read-only probe transpiled the actual jobs.ts in memory and mocked only external effects. It started loadJobs, called cleanup, then resolved the request: state.jobs became ['late-after-cleanup']. It replaced the EventSource and invoked the old onEvent: state.jobs became ['replaced-source']. Both behaviors were reproduced on unmodified code.

Impact: old snapshots can repopulate the state after teardown or replace a newer session's job list; an obsolete error callback can also restart fallback polling. A visibility listener is installed only at store construction (jobs.ts:550) and removed by cleanup, so restarting the singleton does not reinstall it.

Remedy: a global-feed lifecycle controller with explicit start/cleanup, source epochs, request sequence and snapshot revision guards; keep tracked-job and history semantics intact. Add tests for teardown, reconnection, late requests, stale source errors, visible/hidden behavior and reuse.

### F2 — P2: queued cancellation leaks executor queue accounting

Evidence: backend/app/runtime/blocking.py:54 increments _pending before submission; only invoke decrements it. A queued task cancelled before invoke runs never decrements _pending. Submission errors also have no balancing path.

Read-only probe used the actual _BoundedExecutor with one held worker and a second queued task. After cancelling the queued task and finishing the running task, gauges reported queued=1, running=0, capacity=1.

Impact: incorrect executor metrics persist until shutdown and can mislead capacity diagnosis. This does not prove an execution-capacity leak.

Remedy: account for queued, started, cancelled-before-start and rejected submissions exactly once under the existing lock, retaining running-callback cancellation behavior.

### F3 — P2: runtime has reverse dependencies on repositories

Evidence: backend/app/runtime/blocking.py:175, :218, :341 import repositories.db inside functions. Its opening comment explicitly documents a cycle break with repositories/gallery/mutations.py:79. Database connection scopes, SQLite retry policy and connection cleanup live in a generic runtime module.

backend/tests/test_architecture_boundaries.py:149 applies the layer matrix only to module_imports; deferred imports are separately frozen rather than checked against that matrix. The runtime reverse edges are allowed as deferred-import debt despite runtime's allowed target layers being core/runtime.

Remedy: keep pools, callback scheduling, gauges and memory admission in runtime; move SQLite scopes, retry policy and database-aware shutdown composition into repositories/db/execution.py. Update internal callers mechanically. Check deferred imports against the dependency matrix as well and remove the three obsolete exceptions.

### F4 — P2: coordination.py combines five independent persistence domains

Evidence: backend/app/repositories/coordination.py:36-176 handles access lockouts; :177-911 gallery job state/admission/upload reservations; :912-1040 SSE slots; :1041-1236 background leases; :1237-1381 worker heartbeats and metrics. The module is 1,381 lines. Its dynamic __all__ at :1381 exports incidental imported names as well as its operations.

Impact: shared namespace and broad responsibility make ownership, review and isolation difficult. No data-corruption defect was demonstrated in the existing SQL/lease algorithms.

Remedy: coordination package with access, gallery_jobs, sse_slots, leases and workers modules plus an explicit operation facade. Preserve signatures, SQL, atomic transaction scopes, lease fencing, return data and existing import paths. Imported stdlib names are not an intentional public API.

## Deferred improvement opportunities

- F5 — P3: frontend/src/lib/stores/galleryDeferredActions.ts:312 has one factory covering NodeImage upload, batch mutations, archive import/export, streaming download and R2 sync (1,043 total lines). Split by operation domain in a later pass. It is deliberately lazy-loaded today; do not infer a measured performance defect from its size.
- F6 — P3: frontend/src/lib/features/workspace/Workspace.svelte remains a 1,749-line composition/controller/template module, despite existing panel/lifecycle/lightbox helpers. Extract remaining use cases only with focused browser regression coverage. MaskEditorDialog and maskDocument are also large, but size alone does not justify rewriting their geometry algorithms.
- F7 — P3: .trellis/spec/backend/index.md and frontend/index.md still advertise template documents as “To fill”, while actual testing/API/security guides already exist. Bootstrap Guidelines is an existing separate task. Add scoped contracts learned in this refactor without taking ownership of that task.
- Job equality helpers omit several newer payload fields, but backend updated_at is refreshed with microsecond timestamps. No production stale-cost defect has been demonstrated; preserve current optimization during extraction and defer changes until an actual counterexample is established.
- Agent import SCCs found by a naive AST scan include TYPE_CHECKING-only imports; they are not runtime cycle findings. Database facade SCCs likewise need actual initialization analysis, not blanket elimination.
- Executor workers are bounded; ThreadPoolExecutor's submission queue is not an admission limit. Existing service admission/queue constraints must be understood before proposing a new queue policy.

## Baseline validation on 2026-10-13

- Backend full suite in sandbox: 927 passed, 22 skipped, 6 failed because bind(127.0.0.1, 0) was prohibited.
- The affected test_agent_client_transport.py rerun with loopback permission: 6 passed. Combined coverage accounts for 933 passing tests and 22 skipped tests; this is not a claim of a single unsandboxed full-suite run.
- Frontend: svelte-check 0 errors/0 warnings; 18 unit test files, 192 tests passed.
- Frontend build, bundle budget and module Worker verification passed.
- Homepage required JS: 120,868 bytes gzip against 122,880 bytes budget. CSS: 14,222 bytes against 14,336 bytes. Keep existing budgets unchanged.
- Two upstream dependency deprecation warnings are pre-existing; local Python is 3.11, CI/runtime use 3.12. Do not mix dependency migration with these refactors.
- Browser E2E was not run during planning. During implementation setup, the host's lowercase proxy variables without loopback bypass caused Playwright to misidentify the server and then fail navigation. With NO_PROXY/no_proxy=127.0.0.1,localhost and an isolated port, the prompt-submit browser test passed (1 test). Focused full-flow validation remains required after frontend implementation; repository configuration was not changed for this host-specific issue.

## Preservation

Initial git status contains 15 untracked Trellis/platform entries and no tracked source modifications. Preserve those entries and the existing bootstrap task. No product code has been edited during planning.

## First-batch implementation outcome

F1-F4 are addressed on refactor/architecture-legacy-modules. Database policy now lives in repositories/db/execution.py and generic runtime contains no repository dependencies. Executor cancellation/submission accounting has regressions. Coordination is split into five domains with 43 functions and 35 public operation signatures preserved. Global job state helpers/feed lifetime are separated, with obsolete callback/response rejection and restartable resources.

Independent child and integrated reviews passed. Final backend run: 945 passed, 22 skipped; frontend unit: 206 passed; diagnostics: 0 errors/0 warnings; production build/budget/Worker checks passed; focused browser: 29 passed, 3 existing opt-in performance skips. Exact evidence is in validation.md and child validation.md files.

The integrated run uncovered one environment-dependent existing test fixture: queue.example.com was missing from example_host_dns, so host VPN fake DNS triggered the production SSRF guard before a scripted upstream call. A minimal fixture-host addition made the recovery test red→green without changing production validators. The subsequent complete suite passed.

Additional deferred finding F8: existing historyLoading cleanup may remain true after an invalidated history request. jobs.ts retains the pre-existing history algorithm under this batch's explicit history-preservation scope; the frontend validation record contains evidence and anchors for a separately scoped fix. F5-F7 remain deferred as planned.

No deployment, schema/data transformation, product feature or budget increase is included. The user approved split commits and pushing the feature branch; five work commits have been created, with task/session bookkeeping and push forming the final handoff.
