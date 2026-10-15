# Database execution and bounded worker lifecycle

## 1. Scope / Trigger

Use these contracts when adding async repository calls, off-loop SQLite operations, or worker shutdown. Generic scheduling belongs to backend/app/runtime/blocking.py; SQLite connection/retry/cleanup policy belongs to backend/app/repositories/db/execution.py.

## 2. Signatures

Import from repositories.db.execution:

```python
async def run_db_operation(callback, *args, metric_name=None,
                           retry_busy=True, critical=False, **kwargs): ...
def run_db_operation_in_current_thread(callback, *args, metric_name=None,
                                      retry_busy=True, critical=False, **kwargs): ...
async def close_blocking_executors(): ...
```

runtime.blocking exposes run_db_callback(callback, *, metric_name), run_on_db_workers(callback), run_image_operation, run_file_operation, executor_gauges and upstream_memory_lease. It must not import repositories, including in function bodies.

## 3. Contracts

- Async DB calls preserve ContextVars and run metric_name_scope + persistent_connection_scope on the DB worker.
- Already-off-loop synchronous calls use busy_timeout_scope and per-attempt metrics; do not call this synchronous adapter from the event loop.
- critical=True uses SQLITE_CRITICAL_BUSY_TIMEOUT_MS/SQLITE_CRITICAL_BUSY_RETRY_ATTEMPTS. Other calls use SQLITE_BUSY_TIMEOUT_MS/SQLITE_BUSY_RETRY_ATTEMPTS. retry_busy=False means no retries.
- Only sqlite3.OperationalError containing locked/busy is retried, using SQLITE_BUSY_RETRY_BASE_MS, exponential backoff and existing jitter.
- The DB-aware shutdown adapter performs bounded worker connection cleanup before shutting file/image/DB pools. Services call this adapter, not generic runtime shutdown alone.
- Executor worker counts are bounded; this API does not introduce a submission admission limit.

## 4. Validation & Error Matrix

| Condition | Result |
|---|---|
| Successful callback | Return callback result; account queued→running→finished once |
| SQLite busy within budget | Increment sqlite.busy_retries and retry |
| Non-busy error / exhausted budget | Propagate error |
| Cancel queued awaiter before callback starts | Callback does not run; queued count balances |
| Cancel running awaiter | Worker may continue; running count balances when worker finishes |
| Submission rejected | Propagate submission error; queued count balances |
| Worker cleanup exceeds bounded fan-out | Existing warning/fallback behavior; no cross-worker barrier |

## 5. Good / Base / Bad Cases

Good: await run_db_operation(repo.claim, critical=True) for a lease claim.
Base: await run_db_operation(repo.list_jobs) for polling reads.
Bad: runtime importing repositories to provide SQLite policy, or resetting running counters when only the awaiter was cancelled.

## 6. Required Tests

- test_blocking_executor.py: queued cancellation never executes callback; no leaked queue count; running cancellation remains counted until actual completion; rejected submission balances.
- DB execution regression coverage: ContextVars, retry exhaustion, critical/normal budgets, connection scopes and cleanup ordering.
- test_architecture_boundaries.py checks both top-level and deferred imports; frozen deferred imports are not permission to violate the layer matrix.

## 7. Wrong vs Correct

```python
# Wrong: SQLite policy in generic runtime, or importing the moved helper there.
from backend.app.runtime.blocking import run_db_operation

# Correct: DB policy owner; generic image work still stays in runtime.
from backend.app.repositories.db.execution import run_db_operation
from backend.app.runtime.blocking import run_image_operation
```
