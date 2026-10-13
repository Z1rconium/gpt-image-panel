# Database execution boundary

## Ownership and Flow

runtime/blocking.py owns generic pools/scheduling/metrics/memory admission. New repositories/db/execution.py owns SQLite connection scopes, busy retry policy and DB-worker cleanup/shutdown composition.

Services/API → execution.run_db_operation → generic runtime callback scheduling → SQLite scope → repository callback.

Preserve async persistent_connection_scope plus metric_name_scope in the worker; preserve synchronous busy_timeout_scope and per-attempt timing. Keep defaults, critical budgets, retry recognition and jitter unchanged. Import connection primitives directly rather than expanding db's legacy facade.

Runtime exposes minimal generic callback scheduling and worker cleanup capabilities. The repository adapter composes DB cleanup with runtime shutdown in the existing order; dispatchers calls the adapter. No startup-only binding/global registration hook.

## Accounting

Lock-protected per-submission state balances queued→started/cancelled/rejected exactly once. Cancelling a running awaiter does not stop the worker callback; running remains until its finally completes.

## Imports and Boundary Checks

Migrate DB helper and DB-aware shutdown imports in 41 current consumer modules and affected tests. Leave image/file functions imported from runtime. Apply the matrix to deferred imports; delete only the three obsolete runtime→repositories deferred exceptions. Retain justified unrelated cycle-break imports.

## Files and Compatibility

runtime/blocking.py; new repositories/db/execution.py; API/service/gallery-mutation caller imports; dispatchers shutdown; architecture/blocking/performance tests. Internal DB import locations change; metrics/resource ownership and user APIs stay compatible. Rollback runtime/adapter/caller imports together.
