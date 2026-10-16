# Coordination persistence ownership

## 1. Scope / Trigger

Use when adding access lockouts, gallery task coordination, SSE limits, background leases or worker metrics. backend/app/repositories/coordination is a package; its facade preserves established operation imports.

## 2. Signatures

The explicit facade exports 35 operations. Domain owners:

| Module | Operations |
|---|---|
| access | get_access_lockout, record_access_failure, clear_access_failure, list_access_failures |
| gallery_jobs | gallery CRUD/progress/cancellation/leases, claims, capacity and import upload reservations |
| sse_slots | acquire_sse_slot, refresh_sse_slot, release_sse_slot, count_active_sse_slots |
| leases | acquire/complete/release_background_lease, acquire/renew/release_background_slot |
| workers | mark_worker_heartbeat, get_runtime_coordination_metrics, refresh_runtime_coordination_metrics |

`services.lease_renewal.renew_lease_until_lost(..., retry_interval_seconds=None, minimum_retry_budget_seconds=None, deadline=None, ...)` owns the service renewal skeleton. `minimum_retry_budget_seconds=None` uses the retry cadence as the abandonment margin; Agent passes `0.0` to retain its lease until actual expiry.

Existing repository function signatures/return values are preserved. New implementation code imports its domain owner directly; established consumers may continue importing repositories.coordination.

## 3. Contracts

- Domain modules import DB implementation owners directly and never their own facade.
- workers uses sse_slots same-connection helpers for slot counting/cleanup; do not copy that policy.
- Gallery admission/upload reservation code remains together so atomic capacity policy is not fragmented.
- Preserve SQL, transaction boundaries, cancellation guards, owner fencing and lease-expiration comparisons.
- Shared service renewal loops must preserve each caller's transient-error policy. Agent turns retain their execution window until the actual monotonic lease deadline; a DB renewal exception alone must not cancel a still-valid turn. Image units retain their existing early-abandon policy when the remaining lease cannot cover another retry interval. A false renewal result still signals ownership loss immediately for every caller.
- Coordination extraction has no schema migration or data conversion.
- Facade __all__ contains intentional operations, excluding incidental imported stdlib/type names.

## 4. Validation & Error Matrix

| Condition | Required behavior |
|---|---|
| Existing operation import | Same name/signature/return contract |
| Wrong lease owner / expired ownership | Existing rejection/fencing policy |
| Agent renewal exception with a valid lease | Preserve execution until expiry; permit completion during the remaining valid window |
| Agent renewal hangs or keeps failing through expiry | Cancel the main task and mark lease loss at expiry |
| Image-unit renewal exception with at most one retry interval remaining | Preserve early abandonment |
| Capacity exhaustion | Existing reservation/admission result |
| Expired SSE slot in metrics read | Excluded from counts without a cleanup write |
| Metrics publication | Existing expired-slot cleanup and snapshot publication |
| Monkeypatch implementation dependency | Patch the owning module global, not an inactive facade alias |

## 5. Good / Base / Bad Cases

Good: workers imports _count_active_sse_slots_on_conn from sse_slots.
Base: services keep from repositories.coordination import acquire_background_slot.
Bad: domain modules import repositories.coordination to call their sibling or a test patches a facade's private alias while the real function uses a different global.

## 6. Required Tests

test_coordination_repository.py verifies facade export ownership and cross-domain heartbeat/lease/SSE metrics behavior. Existing access, storage, SQLite coordination, worker distribution, gallery SSE, assistant and NodeImage contracts cover retained algorithms.

The access read-only test patches coordination.access._transaction to prove lockout reads do not open a write transaction. A facade alias would not intercept the actual implementation.

Agent lease regression tests must distinguish a transient renewal error from actual expiry: a main task that completes before expiry remains successful, while hung renewal and expired ownership still stop execution. Shared-loop tests must retain the image-unit retry margin and cancellation propagation.

## 7. Wrong vs Correct

```python
# Wrong: the moved function resolves dependencies in its owner, not the facade.
monkeypatch.setattr(coordination_repo, "_transaction", fail_on_write)

# Correct: observe the dependency actually used by the access implementation.
monkeypatch.setattr(coordination_repo.access, "_transaction", fail_on_write)
```
