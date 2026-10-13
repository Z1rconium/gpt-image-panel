"""Database execution scopes, retry policy and shutdown composition."""

import asyncio
import contextvars
import sqlite3

import pytest

from backend.app.core import settings as config
from backend.app.repositories.db import connection, execution, state
from backend.app.runtime import blocking


def test_async_database_callback_copies_context_and_restores_worker_scopes(monkeypatch):
    marker = contextvars.ContextVar("execution_test_marker", default="default")
    marker.set("request")
    monkeypatch.setattr(config, "SQLITE_BUSY_TIMEOUT_MS", 19)

    def probe():
        return (
            marker.get(),
            connection.current_db_metric(),
            state._thread_local.busy_timeout_ms,
            state._thread_local.keep_connection_open,
        )

    async def scenario():
        result = await execution.run_db_operation(probe, metric_name="scoped")
        restored = await blocking.run_db_callback(
            lambda: (
                connection.current_db_metric(),
                getattr(state._thread_local, "busy_timeout_ms", None),
                getattr(state._thread_local, "keep_connection_open", False),
            ),
            metric_name="probe",
        )
        return result, restored

    # One worker makes the scope-restoration probe inspect the same thread.
    asyncio.run(execution.close_blocking_executors())
    monkeypatch.setattr(config, "DB_EXECUTOR_WORKERS", 1)
    try:
        result, restored = asyncio.run(scenario())
        assert result == ("request", "scoped", 19, True)
        assert restored == (None, None, False)
    finally:
        asyncio.run(execution.close_blocking_executors())


@pytest.mark.parametrize("current_thread", [False, True])
@pytest.mark.parametrize("retry_busy", [False, True])
def test_busy_retry_budget_and_backoff_are_preserved(monkeypatch, current_thread, retry_busy):
    monkeypatch.setattr(config, "SQLITE_BUSY_RETRY_ATTEMPTS", 2)
    monkeypatch.setattr(config, "SQLITE_BUSY_RETRY_BASE_MS", 10)
    monkeypatch.setattr(execution.random, "uniform", lambda low, high: 1.0)
    delays = []
    attempts = []

    def fail():
        attempts.append(connection.current_db_metric())
        raise sqlite3.OperationalError("database is locked")

    async def async_sleep(delay):
        delays.append(delay)

    if current_thread:
        monkeypatch.setattr(execution.time, "sleep", delays.append)
        operation = lambda: execution.run_db_operation_in_current_thread(
            fail, metric_name="retry_probe", retry_busy=retry_busy
        )
    else:
        monkeypatch.setattr(execution.asyncio, "sleep", async_sleep)
        operation = lambda: asyncio.run(
            execution.run_db_operation(fail, metric_name="retry_probe", retry_busy=retry_busy)
        )
    with pytest.raises(sqlite3.OperationalError, match="database is locked"):
        operation()
    assert attempts == ["retry_probe"] * (3 if retry_busy else 1)
    assert delays == ([0.01, 0.02] if retry_busy else [])
    assert connection.current_db_metric() is None


def test_database_worker_cleanup_precedes_executor_shutdown(monkeypatch):
    events = []

    class Executor:
        def __init__(self, name):
            self.name = name

        def run_on_each_worker(self, callback):
            callback()
            return 1

        def shutdown(self):
            events.append(f"{self.name}.shutdown")

    monkeypatch.setattr(config, "DB_EXECUTOR_WORKERS", 1)
    for name in ("db", "image", "file"):
        monkeypatch.setattr(blocking, f"_{name}_executor", Executor(name))
    monkeypatch.setattr(execution, "_close_thread_connection", lambda: events.append("db.cleanup"))

    asyncio.run(execution.close_blocking_executors())

    assert events == ["db.cleanup", "file.shutdown", "image.shutdown", "db.shutdown"]
