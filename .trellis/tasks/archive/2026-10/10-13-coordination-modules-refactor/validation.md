# Coordination extraction validation

## Boundary and ownership

The prior coordination module combined independent persistence responsibilities. The change replaces `backend/app/repositories/coordination.py` with a stable explicit facade and `access.py`, `gallery_jobs.py`, `sse_slots.py`, `leases.py`, and `workers.py`. It preserves operation behavior while making each persistence policy's owner explicit. No SQL, state-machine, schema, transaction, lease-fencing, cancellation, admission, or expiration policy was rewritten. Database execution and frontend changes belong to other workers.

The consumer scan found 35 public operations and no consumed private coordination helpers. One existing access test patched the original `_transaction` global; it now patches `coordination.access._transaction` so the read-only guarantee still observes the actual implementation. Two pre-existing F401/F841 findings in that touched test file (`urllib.parse` and `stored`) are preserved outside this extraction's scope.

## Preservation evidence

Before extraction, saved the source for comparison. A one-time Python AST/source check compared every original function with its extracted owner: **43 functions and both constants have identical ASTs and exact source segments**. Imported the baseline under the original repository package context and compared `inspect.signature` for all **35 public operations** with the facade: all signatures match, and the facade export set exactly matches those operations. This comparison covers SQL strings, transaction scopes, nested worker-metric reads, and helper bodies as well as signatures. No runtime facade indirection or import-cycle workaround was introduced.

Implementation modules import only required owners from `db.connection`, `db.schema`, `db.rows`, `db.constants`, and `db.settings_store`; `workers` imports its two same-connection SSE helpers directly from `sse_slots`. Existing public consumers keep their imports unchanged.

## Checks

- Targeted contracts, new repository tests, and architecture checks: **225 passed**, two pre-existing dependency deprecation warnings. Command: `.venv/bin/python -m pytest backend/tests/test_storage_migrations_contract.py backend/tests/test_sqlite_coordination_contract.py backend/tests/test_worker_distribution_contract.py backend/tests/test_access_settings_contract.py backend/tests/test_agent_boundaries.py backend/tests/test_gallery_job_sse.py backend/tests/test_assistant_contract.py backend/tests/test_nodeimage_upload_contract.py backend/tests/test_coordination_repository.py backend/tests/test_architecture_boundaries.py -q`.
- New tests exercise facade operation ownership and wildcard export boundaries, plus worker metrics observing background leases/heartbeats, read-only exclusion of expired SSE slots, maintenance cleanup, and snapshot publication across sibling modules.
- Package/new-test F401/F841: **passed**, using `.venv/bin/python -m ruff check --select F401,F841 backend/app/repositories/coordination backend/tests/test_coordination_repository.py`. Including the existing access test in lint reports its two baseline unused names; the sole intended change in that test remains the monkeypatch owner.
- Focused access/new-test rerun after the test import cleanup: **101 passed**, two pre-existing warnings. Command: `.venv/bin/python -m pytest backend/tests/test_coordination_repository.py backend/tests/test_access_settings_contract.py -q`. The restored access test's diff is exactly one monkeypatch-owner line; its baseline unused names remain.
- `git diff --check`: **passed**. Final source/signature equivalence probe repeated successfully.
- Integrated full backend suite and independent Trellis check are handed to the main session; the implementer does not stage, commit, archive, or update project specs.

## Remaining work

Main session integrated full backend validation. No unresolved extraction risk found in targeted contracts or independent review. Private imported globals are intentionally absent from the operation facade; the only actual monkeypatch consumer was updated to its implementation owner.

## Independent Trellis review

- Compared the tracked `HEAD` source independently with all five current implementation modules: all 43 functions have identical ASTs and exact source segments; both constants have identical ASTs. The explicit facade exports exactly the original 35 public operations and every signature matches. External globals referenced by those operations still resolve to the original dependency objects. SQL, transaction boundaries, cancellation, admission, owner fencing, and expiration comparisons retain their prior implementation.
- Inspected domain imports: persistence owners use direct `db` implementation imports; `workers` imports same-connection SSE helpers from `sse_slots`; no implementation imports its own coordination facade. Public consumers retain the stable import path, and the access test now patches the `_transaction` global used by the real read operations. No missing private-helper consumer was found.
- Reviewed the new tests against real execution: metrics observation is read-only, publication removes expired SSE rows and persists the worker snapshot, and facade exports resolve to implementation functions. Existing contracts remain responsible for admission and lease state-machine behavior.
- Reviewer rerun: **65 passed**, two existing dependency deprecation warnings. Command: `.venv/bin/python -m pytest backend/tests/test_coordination_repository.py backend/tests/test_sqlite_coordination_contract.py backend/tests/test_worker_distribution_contract.py backend/tests/test_storage_migrations_contract.py backend/tests/test_architecture_boundaries.py backend/tests/test_agent_boundaries.py backend/tests/test_access_settings_contract.py::test_access_failures_normalize_ipv6_and_lockout_reads_without_write_transaction -q`.
- Lint: package and new repository tests pass `ruff --select F401,F841`. Python compilation and `git diff --check` pass. No backend static type-check command is configured; compilation is syntax verification, not a static type-check claim.
- **No code fixes or unresolved extraction findings.** The two historical unused names in the existing access test are outside the change and CI lint scope. Specs and the integrated full backend suite remain owned by the main session.
