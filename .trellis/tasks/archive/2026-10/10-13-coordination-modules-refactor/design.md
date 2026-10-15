# Coordination decomposition

Replace coordination.py with coordination/__init__.py and five implementation modules.

- access.py: IP normalization, expiration and access operations.
- gallery_jobs.py: lifecycle/progress/cancellation/fencing, capacity and import upload reservations. Keep related atomic admission logic together.
- sse_slots.py: acquire/refresh/release/count and same-connection cleanup.
- leases.py: background lease and slot operations.
- workers.py: heartbeats, metric snapshots and coordination metric publication.

Use direct db.connection/schema/rows/constants/settings_store imports. workers imports SSE helpers from sse_slots. Do not introduce a catch-all common module without actual shared policy.

## Compatibility and Invariants

Follow repositories/agent's explicit facade style. Export all used operation names; preserve any private helper access with real consumers. Incidental stdlib exports are not intentional API. Trace service/test monkeypatch locations so moved globals do not invalidate fixtures.

Extract function bodies without rewriting SQL, lease guards, expiry comparisons or transaction boundaries, including nested worker-metrics connection behavior. No persistence migration.

## Files and Rollback

coordination.py→coordination/*; focused compatibility/ownership tests. Keep public consumers untouched unless an actual private-helper need requires adjustment. Restore all package moves together if a contract fails.
