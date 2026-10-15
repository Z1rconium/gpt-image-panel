# Integrated architecture-refactor validation

Reviewed on 2026-10-13 against baseline `914418f`, on `refactor/architecture-legacy-modules`. All three approved child refactors are implemented. This document records integration evidence; staging, commits, task completion and archival remain with the main session.

## Integration review

- **Database execution:** Runtime has no repository imports, including function-local imports. The AST dependency matrix now applies to deferred imports. No production consumer imports the moved DB policy helpers from runtime. Independent AST comparison of all **41** migrated existing production consumers found no changes except imports; image/file scheduling and gauges remain in runtime. Context copying, SQLite scopes, retry recognition, critical/normal budgets, jitter and cleanup-before-shutdown retain tests. Lock-protected queued cancellation/rejection settlement does not decrement running work when only its awaiter is cancelled.
- **Coordination:** Independently compared all **43** original functions with the five extracted owners: ASTs and source segments are identical. All **35** operation signatures match the original facade; referenced external dependency objects remain identical. SQL, transactions, lease fencing, cancellation, atomic gallery admission and expiration policy are preserved. Domains import DB owners directly; workers shares the SSE same-connection helpers. The access monkeypatch now targets its actual implementation global. Intentional operation exports exclude incidental imported names.
- **Frontend:** Independently compared all **seven** extracted helper bodies/signatures with baseline: unchanged. Inspected every global source callback, epoch/source replacement, pending-list request sequence and accepted-event revision, poll ownership tokens, timer disposal and visibility-listener reuse. Accepted callbacks retain notification observation, selection pruning and tracked-preview convergence. History and tracked-stream generation guards remain in the facade. Independent integration diagnostics and all unit tests pass.
- **Specs:** Reviewed `backend/db-execution.md`, `backend/coordination.md`, and `frontend/jobs-lifecycle.md` against actual signatures, owners and behavior. No scoped contract drift found. Existing Bootstrap Guidelines work and unrelated untracked platform/skill files remain outside this review.

## Reviewer fix: deterministic test DNS

The initial integrated backend run reported 944 passed, 22 skipped and one recovery-contract failure. Independently reproduced `test_async_provider_recovery_contract.py::test_worker_killed_after_submit_resumes_without_second_submit` with host loopback access: production SSRF validation rejected `queue.example.com` resolving via the host VPN to `198.18.0.6`, before the scripted upstream submit.

`backend/tests/conftest.py::example_host_dns` already maps named mock-service hosts to `93.184.216.34`, but omitted this scripted-provider host. Added only `queue.example.com` to that fixture's explicit host set. The same recovery contract then passed. It supplies the meaningful red/green regression; no duplicate fixture-only assertion was added. Production SSRF checks, application hosts and allowlists were untouched. Focused security tests continue to exercise their own private-IP/hostname overrides.

## Final verification

| Check | Final result / evidence |
|---|---|
| `.venv/bin/python -m pytest backend/tests -q` | **945 passed, 22 skipped**, 2 existing dependency deprecation warnings; one complete loopback-permitted run, 99.64 seconds |
| Security, SSRF, async-provider/recovery and architecture subset below | **96 passed**, same 2 warnings, 9.83 seconds |
| Existing CI `F401,F841` selection | **Pass**, all 26 modules from `.github/workflows/lint.yml` |
| Scoped new/modified implementation and regression `F401,F841` selection below | **Pass** |
| Backend static TypeCheck | Not configured; passing import/architecture/runtime tests are not a static type-check claim |
| `npm --prefix frontend run check` | **Pass**, 0 errors / 0 warnings; independently rerun in final integration |
| `npm --prefix frontend run test:unit` | **Pass**, 19 files / **206 tests**; independently rerun in final integration |
| `npm --prefix frontend run build` | **Pass**, independently verified by frontend child reviewer |
| `npm --prefix frontend run check:bundle` | **Pass**, homepage JS **121,105 B** gzip / **122,880 B**; CSS **14,222 B** / **14,336 B**; unchanged budgets |
| `npm --prefix frontend run check:worker-build` | **Pass**, production module Worker verified by frontend child reviewer |
| `npm --prefix frontend run test:e2e -- jobs-edit prompt-submit phase-five` | **29 passed, 3 existing opt-in performance skips**; frontend implementation-stage run |
| Frontend dedicated lint | Not configured; Svelte/TypeScript diagnostics and diff whitespace checks pass |
| `git diff --check` | **Pass** |

Focused backend command:

```sh
.venv/bin/python -m pytest backend/tests/test_security_import_contract.py backend/tests/test_async_ssrf_contract.py backend/tests/test_security_redaction_lookup.py backend/tests/test_async_provider_contract.py backend/tests/test_async_provider_recovery_contract.py backend/tests/test_architecture_boundaries.py -q
```

Scoped lint command:

```sh
.venv/bin/python -m ruff check --select F401,F841 backend/app/runtime/blocking.py backend/app/repositories/db/execution.py backend/app/repositories/coordination backend/tests/conftest.py backend/tests/test_blocking_executor.py backend/tests/test_db_execution.py backend/tests/test_coordination_repository.py backend/tests/test_architecture_boundaries.py backend/tests/test_sqlite_coordination_contract.py
```

The frontend build/budget/Worker checks ran after the child reviewer's extra unit regression. Browser checks preceded that test-only addition; no frontend production/config/browser-test changes followed the browser run. Repeating those checks after a backend test-fixture-only fix is unnecessary. Browser environment used `NO_PROXY/no_proxy=127.0.0.1,localhost`, isolated `PLAYWRIGHT_DEV_PORT=5187`, `CI=true`, and loopback permission; repository proxy configuration was unchanged.

## Limits and deferred findings

- No unresolved production defect was found in the approved extraction scope. HTTP/SSE contracts, schema/data, lease/admission semantics, visual design and bundle budgets are preserved.
- **Existing history-lifetime defect remains outside this scope:** `loadJobHistory` sets `historyLoading=true`; cleanup increments `historyRequestSeq`; the stale request's finally cannot clear loading, so later history loads return at their loading guard. The same code existed before this refactor. A separate history-lifecycle fix should clear or transfer loading ownership on disposal and prove reuse with a delayed-request regression. The current task explicitly preserves history behavior, so the reviewer did not change it.
- Audit F5/F6 (gallery actions, Workspace/mask size) remain deferred. F7 general guideline bootstrap remains a separate existing task; only refactor-specific contracts were reviewed here.
- Backend local interpreter is Python 3.11 while CI/runtime use 3.12. Two pre-existing dependency deprecation warnings remain. Opt-in performance coverage is skipped as configured, not claimed as passed.
- Historical unused imports/variables outside CI/new-source scope remain untouched; child baseline comparison found no introduced findings. This review does not perform broad legacy lint cleanup.

For detailed child evidence, see sibling task `validation.md` files for database-runtime, coordination-modules, and generation-jobs-lifecycle. All parent acceptance criteria have passing implementation/review evidence; the main session still owns final repository handoff.

## Approved work commits

- da2f7df — refactor(db): separate sqlite execution from generic runtime
- ee804df — refactor(storage): split coordination persistence by domain
- 913f7ba — fix(frontend): guard generation job feed lifecycle
- 7a59b15 — test: make async provider fixture DNS deterministic
- 57cdfd1 — docs: record refactoring architecture contracts

Each staged set was checked against the reviewed explicit file manifest. Staged whitespace validation caught one redundant EOF newline in jobsState.ts; the frontend reviewer removed exactly that newline, with no semantic change or need to repeat tests. Child tasks are archived and committed separately. Parent archival/session recording and the user-authorized branch push complete the handoff.
