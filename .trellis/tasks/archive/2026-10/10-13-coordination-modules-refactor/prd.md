# Split coordination persistence by responsibility

## Goal and Evidence

Separate independent persistence domains with stable coordination operations. Parent research/audit.md F4 anchors coordination.py access :36-176, gallery/admission :177-911, SSE :912-1040, leases :1041-1236, workers :1237-1381 and incidental dynamic exports.

## Requirements

- CO1: Separate five responsibility domains.
- CO2: Preserve existing repositories.coordination operation import names/signatures/returns.
- CO3: Preserve SQL, transactions, owner fencing, cancellation, admission and expiration.
- CO4: Explicitly export intended operations.

## Acceptance Criteria

Existing access/gallery/SSE/background-slot/worker tests pass. Facade operations map to compatible implementation owners. Domain modules import siblings directly rather than their own facade. No new cycle workaround/schema migration/behavior change.

## Out of Scope

SQL optimization, state-machine/admission policy rewrite and bootstrap-spec work.
