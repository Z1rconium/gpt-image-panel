# Execution and integration plan

## Review Gate

- [x] Audit and baseline validation recorded.
- [x] Parent/three children and curated context documented.
- [x] Present final summary; subsequent user approval received on 2026-10-13.
- [x] Start database-runtime child after approval.

## Delivery

1. [x] Database-runtime implemented and independently checked.
2. [x] Coordination-modules implemented after DB import migration and independently checked.
3. [x] Generation-jobs-lifecycle implemented and independently checked.
4. [x] Parent full-scope integration review, regression validation and scoped spec capture completed.
5. [x] Phase 3.4: five approved split work commits created; user authorized pushing the branch on 2026-10-13.
6. [x] Work commits and three child archives complete; this parent is ready for the authorized archive/session-journal sequence. Preserve unrelated files and Bootstrap Guidelines; push the branch after bookkeeping.

## Integration Validation

- .venv/bin/python -m pytest backend/tests -q (six transport tests require loopback permission).
- npm --prefix frontend run check
- npm --prefix frontend run test:unit
- npm --prefix frontend run build
- npm --prefix frontend run check:bundle
- npm --prefix frontend run check:worker-build
- npm --prefix frontend run test:e2e -- jobs-edit prompt-submit phase-five
- Apply existing F401,F841 lint selection to changed backend implementation files; explicitly mark deliberate facade re-exports.
- Review git diff --check and scoped diffs against research/audit.md baseline.

Each child must satisfy its acceptance criteria. Final review covers no reversed runtime imports, facade compatibility, shutdown/lease invariants, stale frontend callbacks, budget preservation and prior-work preservation. Planning artifacts alone do not complete implementation.
