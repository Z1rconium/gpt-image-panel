# Separate database execution from generic runtime

## Goal and Evidence

Correct executor lifecycle metrics and remove reversed runtime/storage dependency. Parent research/audit.md F2/F3 records reproduction and anchors: blocking.py:54 leaks queued counts; :175, :218, :341 import repositories; architecture matrix checks omit deferred imports.

## Requirements

- DB1: Runtime scheduling imports no repository code.
- DB2: Preserve DB signatures/returns, scopes, ContextVars, metrics, busy jitter/retries, critical budgets and shutdown ordering.
- DB3: Balance counters exactly once on completion, queued cancellation, running cancellation and submission rejection.
- DB4: Apply the layer matrix to deferred imports as well.

## Acceptance Criteria

- Regressions prove cancelled queued work leaves queued/running zero after completion.
- Rejected submissions balance counters; cancelled running awaiters retain running accounting until callbacks finish.
- Existing executor/performance/storage/SQLite/gallery/Agent tests pass.
- No runtime repository import/new allowlist debt remains; all internal consumers migrate together.
- HTTP/schema behavior stays compatible.

## Out of Scope

Queue capacity policy, schema changes, retry tuning, dependencies and coordination extraction.
