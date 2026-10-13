# Database runtime implementation

- [x] Read artifacts/specs and inspect mixed blocking imports.
- [x] Add queued/running cancellation and rejected-submission regressions.
- [x] Extract DB scopes/retries/cleanup into repositories/db/execution.py.
- [x] Expose minimal generic scheduling/cleanup; fix per-submission accounting.
- [x] Migrate DB consumers/test patch locations and extend deferred boundary checks.
- [x] Run .venv/bin/python -m pytest backend/tests/test_blocking_executor.py backend/tests/test_architecture_boundaries.py backend/tests/test_performance_regressions.py backend/tests/test_sqlite_coordination_contract.py backend/tests/test_generation_jobs_contract.py -q
- [x] Run full backend suite and unused-import lint; inspect scope/retry/shutdown diff.
- [x] Independent Trellis check and validation record (105 targeted tests, scoped/CI lint, normalized policy AST and import/shutdown review passed).
- [x] Root: scoped contract recorded in backend/db-execution.md; integrated backend suite passed (945 passed, 22 skipped) after coordination extraction and fixture stabilization.

Ownership: runtime/blocking.py, DB execution adapter, caller DB import migration, dispatchers shutdown and related tests. No coordination extraction/frontend edits.
