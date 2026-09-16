"""Bounded-executor fan-out and shutdown behaviour."""

import threading
import time
from pathlib import Path

import pytest

from backend.app.runtime import blocking


def _executor(workers: int) -> blocking._BoundedExecutor:
    executor = blocking._BoundedExecutor("test", lambda: workers)
    executor._get_executor()
    return executor


def test_worker_fanout_returns_within_its_deadline_while_a_worker_is_busy():
    """A busy worker must not be able to hold the fan-out.

    The fan-out used to rendezvous on a barrier sized to the pool, so a worker
    still running an earlier task left the other arrivals waiting. shutdown()
    then cancelled a queued arrival, which kept the arrivals - and the wait on
    them - blocked forever. This test fails by hanging on that implementation.
    """
    executor = _executor(2)
    busy_released = threading.Event()
    busy_started = threading.Event()
    reached: list[str] = []

    def occupy_worker() -> None:
        busy_started.set()
        busy_released.wait(timeout=10)

    try:
        busy = executor._get_executor().submit(occupy_worker)
        assert busy_started.wait(timeout=2)

        started_at = time.monotonic()
        closed_workers = executor.run_on_each_worker(
            lambda: reached.append("closed"), timeout=0.2
        )
        elapsed = time.monotonic() - started_at

        assert elapsed < 1.0
        assert closed_workers < 2
        assert reached

        busy_released.set()
        busy.result(timeout=5)
    finally:
        busy_released.set()
        executor.shutdown()


def test_shutdown_finishes_after_a_worker_was_skipped():
    executor = _executor(4)
    busy_released = threading.Event()
    busy_started = threading.Event()

    def occupy_worker() -> None:
        busy_started.set()
        busy_released.wait(timeout=10)

    try:
        busy = executor._get_executor().submit(occupy_worker)
        assert busy_started.wait(timeout=2)
        assert executor.run_on_each_worker(lambda: None, timeout=0.2) < 4
    finally:
        busy_released.set()
        busy.result(timeout=5)
        started_at = time.monotonic()
        executor.shutdown()
        assert time.monotonic() - started_at < 5.0


def test_worker_fanout_is_a_no_op_without_an_executor():
    executor = blocking._BoundedExecutor("test", lambda: 2)
    calls: list[int] = []

    assert executor.run_on_each_worker(lambda: calls.append(1), timeout=0.2) == 0
    assert calls == []

    executor.shutdown()


def test_storage_probe_tolerates_another_worker_removing_its_file(tmp_path, monkeypatch):
    """Concurrent workers must not fail a writable directory.

    Every worker verifies storage at startup. With a fixed probe name, one
    worker's cleanup deleted another's probe before it could remove its own,
    and the check raised PermissionError for a directory that was writable.
    """
    import builtins

    from backend.app.repositories.db import connection

    real_open = builtins.open

    def racing_open(file, *args, **kwargs):
        handle = real_open(file, *args, **kwargs)
        # The other worker finishes its check and unlinks its probe first.
        Path(file).unlink(missing_ok=True)
        return handle

    monkeypatch.setattr(builtins, "open", racing_open)

    connection._check_directory_writable(tmp_path)


def test_storage_probe_reports_an_unwritable_directory(tmp_path, monkeypatch):
    from backend.app.repositories.db import connection

    def failing_open(file, *args, **kwargs):
        raise PermissionError("read-only file system")

    monkeypatch.setattr("builtins.open", failing_open)

    with pytest.raises(PermissionError, match="Directory is not writable"):
        connection._check_directory_writable(tmp_path)
