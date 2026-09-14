"""Phase 3 SQLite write-budget, idle-precheck and observability tests."""

import asyncio
import logging
from contextlib import contextmanager, suppress

from backend.tests.support.contract import *  # noqa: F403

from backend.app.core.utils import utc_now
from backend.app.repositories import db as db_repo
from backend.app.services import blocking
from backend.app.services.claim_loop import run_claim_loop


def test_idle_claim_loop_skips_claim_without_write_lock(tmp_path):
    _configure_runtime(tmp_path)
    metrics.reset()

    calls = {"claim": 0, "precheck": 0}

    async def scenario() -> None:
        kick = asyncio.Event()

        async def claim_fn():
            calls["claim"] += 1
            return None

        async def precheck_fn() -> bool:
            calls["precheck"] += 1
            return False

        async def run_fn(job):
            return None

        task = asyncio.create_task(
            run_claim_loop(
                claim_fn=claim_fn,
                run_fn=run_fn,
                running_limit=1,
                idle_interval=0.01,
                max_backoff=0.02,
                kick_event=kick,
                claim_precheck_fn=precheck_fn,
                claim_precheck_metric="image_jobs.claim_precheck_skipped",
                wait_on_active_tasks=False,
            )
        )
        try:
            await asyncio.sleep(0.06)
            assert calls["claim"] == 0
            assert calls["precheck"] > 0

            kick.set()
            await asyncio.sleep(0.06)
            assert calls["claim"] == 1
        finally:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task

    asyncio.run(scenario())
    assert (
        metrics.snapshot()["counters"].get("image_jobs.claim_precheck_skipped", 0)
        > 0
    )


def test_run_db_operation_critical_uses_larger_busy_budget(tmp_path, monkeypatch):
    _configure_runtime(tmp_path)
    seen: dict[str, object] = {}
    original_scope = db_repo.persistent_connection_scope

    def capture_scope(busy_timeout_ms: int):
        seen["timeout"] = busy_timeout_ms
        return original_scope(busy_timeout_ms)

    monkeypatch.setattr(db_repo, "persistent_connection_scope", capture_scope)

    def probe() -> None:
        seen["metric"] = db_repo.current_db_metric()

    asyncio.run(blocking.run_db_operation(probe, metric_name="probe", critical=True))
    assert seen["timeout"] == config.SQLITE_CRITICAL_BUSY_TIMEOUT_MS
    assert seen["metric"] == "probe"

    asyncio.run(blocking.run_db_operation(probe, metric_name="poll"))
    assert seen["timeout"] == config.SQLITE_BUSY_TIMEOUT_MS


def test_transaction_observes_write_metrics_and_slow_warning(
    tmp_path, monkeypatch, caplog
):
    _configure_runtime(tmp_path)
    db_repo._ensure_database()
    monkeypatch.setattr(config, "SQLITE_SLOW_TXN_WARN_MS", 0)

    before = metrics.snapshot()["counters"].get("sqlite.write_txn", 0)
    with caplog.at_level(logging.WARNING, logger=db_repo.logger.name):
        with db_repo._connect() as conn:
            with db_repo.metric_name_scope("unit_test_write"):
                with db_repo._transaction(conn):
                    conn.execute("CREATE TABLE IF NOT EXISTS txn_metric_probe (x)")
    after = metrics.snapshot()["counters"].get("sqlite.write_txn", 0)
    assert after == before + 1

    timings = metrics.snapshot()["timings_ms"]
    assert "sqlite.write_lock_wait_ms" in timings
    assert "sqlite.write_txn_hold_ms" in timings
    assert any(
        "Slow SQLite write transaction" in record.message
        and "unit_test_write" in record.message
        for record in caplog.records
    )


def test_has_claimable_image_job_unit_is_read_only(tmp_path, monkeypatch):
    _configure_runtime(tmp_path)
    db_repo._ensure_database()
    parent, units = image_jobs_repo.enqueue_image_job(
        parent_job={"job_id": "precheck-parent", "status": "queued"},
        operation="generation",
        request={"prompt": "precheck", "n": 1},
        image_units=1,
        api_preset_id="default",
        api_preset_name="Default",
        api_path="/v1/images/generations",
        max_active_generate_jobs=2,
        max_queued_generate_jobs=2,
        max_pending_edit_source_bytes=1024 * 1024,
    )
    assert parent["job_id"] == "precheck-parent"

    def forbid_transaction(conn):
        raise AssertionError("precheck must not take a write transaction")

    with monkeypatch.context() as patcher:
        patcher.setattr(db_repo, "_transaction", forbid_transaction)
        assert image_jobs_repo.has_claimable_image_job_unit(utc_now()) is True

    image_jobs_repo.claim_next_image_job_unit(
        worker_id="precheck-worker",
        claim_token="precheck-token",
        lease_expires_at="2099-01-01T00:00:00+00:00",
        now=utc_now(),
        running_limit=1,
    )
    assert units

    with monkeypatch.context() as patcher:
        patcher.setattr(db_repo, "_transaction", forbid_transaction)
        assert image_jobs_repo.has_claimable_image_job_unit(utc_now()) is False


def test_sync_gallery_with_image_files_uses_bounded_transactions(
    tmp_path, monkeypatch
):
    _configure_runtime(tmp_path)
    db_repo._ensure_database()

    entries = [
        {
            "id": f"sync-stale-{index}",
            "prompt": "stale",
            "size": "1024x1024",
            "filename": f"missing-{index}.png",
            "created_at": utc_now(),
        }
        for index in range(5)
    ]
    with db_repo._connect() as conn:
        with db_repo._transaction(conn):
            gallery_mutations._insert_gallery_entries_on_conn(conn, entries)

    monkeypatch.setattr(gallery_mutations, "GALLERY_SYNC_BATCH_SIZE", 2)

    txn_count = {"count": 0}
    original_transaction = gallery_mutations._transaction

    @contextmanager
    def counting_transaction(conn):
        txn_count["count"] += 1
        with original_transaction(conn):
            yield

    monkeypatch.setattr(gallery_mutations, "_transaction", counting_transaction)

    removed = gallery_mutations.sync_gallery_with_image_files()

    assert removed == 5
    assert txn_count["count"] >= 3
    assert "sqlite.write_txn_hold_ms" in metrics.snapshot()["timings_ms"]
