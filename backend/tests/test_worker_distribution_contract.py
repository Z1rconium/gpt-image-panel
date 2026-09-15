"""Cross-worker image-unit distribution contract tests (per-worker claim share)."""

import asyncio
from contextlib import suppress

from backend.tests.support.contract import *  # noqa: F403

from backend.app.core.utils import utc_now
from backend.app.repositories import db as db_repo
from backend.app.services.claim_loop import run_claim_loop


def _run_workers(unit_count: int, worker_ids: list[str]):
    """Drive one claim loop per worker against the shared image-unit queue.

    Returns the mapping of worker_id -> claimed unit ids and leaves the units in
    the `running` state (the run task sleeps until cancelled), which is enough to
    assert which worker owns which unit.
    """

    claimed_by: dict[str, list[str]] = {worker_id: [] for worker_id in worker_ids}

    async def scenario() -> dict[str, list[str]]:
        def make_claim_fn(worker_id: str):
            async def claim_fn():
                return image_jobs_repo.claim_next_image_job_unit(
                    worker_id=worker_id,
                    claim_token=f"token-{worker_id}",
                    lease_expires_at="2099-01-01T00:00:00+00:00",
                    now=utc_now(),
                    running_limit=config.MAX_ACTIVE_GENERATE_JOBS,
                    max_attempts=config.IMAGE_JOB_UNIT_MAX_ATTEMPTS,
                )

            return claim_fn

        def make_run_fn(worker_id: str):
            async def run_fn(unit):
                claimed_by[worker_id].append(str(unit["unit_id"]))
                await asyncio.sleep(0.3)

            return run_fn

        tasks = [
            asyncio.create_task(
                run_claim_loop(
                    claim_fn=make_claim_fn(worker_id),
                    run_fn=make_run_fn(worker_id),
                    running_limit=config.per_worker_generate_limit(),
                    idle_interval=0.01,
                    max_backoff=0.02,
                    wait_on_active_tasks=True,
                )
            )
            for worker_id in worker_ids
        ]
        try:
            await asyncio.sleep(0.08)
        finally:
            for task in tasks:
                task.cancel()
            for task in tasks:
                with suppress(asyncio.CancelledError):
                    await task
        return claimed_by

    assert unit_count >= 1
    return asyncio.run(scenario())


def _enqueue_multi_unit_job(tmp_path, *, units: int, job_id: str):
    return image_jobs_repo.enqueue_image_job(
        parent_job={"job_id": job_id, "status": "queued", "operation": "generation"},
        operation="generation",
        request={"prompt": "distribution", "n": units},
        image_units=units,
        api_preset_id="default",
        api_preset_name="Default",
        api_path="/v1/images/generations",
        max_active_generate_jobs=config.MAX_ACTIVE_GENERATE_JOBS,
        max_queued_generate_jobs=config.MAX_QUEUED_GENERATE_JOBS,
        max_pending_edit_source_bytes=1024 * 1024,
    )


def _running_unit_count() -> int:
    with db_repo._connect() as conn:
        row = conn.execute(
            """
            SELECT COUNT(*) AS value
            FROM image_job_units
            WHERE status = 'running'
                AND claim_expires_at IS NOT NULL
                AND claim_expires_at > ?
            """,
            (utc_now(),),
        ).fetchone()
    return int(row["value"])


# ── per-worker limit math ────────────────────────────────────────────────────


def test_per_worker_generate_limit_rounds_up(monkeypatch):
    cases = [
        (8, 4, 2),
        (8, 1, 8),
        (2, 10, 1),
        (5, 2, 3),
        (1, 4, 1),
    ]
    for global_limit, workers, expected in cases:
        monkeypatch.setattr(config, "MAX_ACTIVE_GENERATE_JOBS", global_limit)
        monkeypatch.setattr(config, "GRANIAN_WORKERS", workers)
        assert config.per_worker_generate_limit() == expected


def test_per_worker_generate_limit_is_clamped(monkeypatch):
    monkeypatch.setattr(config, "MAX_ACTIVE_GENERATE_JOBS", 4)
    monkeypatch.setattr(config, "GRANIAN_WORKERS", 0)
    assert config.per_worker_generate_limit() == 4

    monkeypatch.setattr(config, "MAX_ACTIVE_GENERATE_JOBS", 0)
    monkeypatch.setattr(config, "GRANIAN_WORKERS", 3)
    assert config.per_worker_generate_limit() == 1


# ── distribution ─────────────────────────────────────────────────────────────


def test_multi_unit_job_spreads_across_workers(tmp_path, monkeypatch):
    _configure_runtime(tmp_path)
    monkeypatch.setattr(config, "MAX_ACTIVE_GENERATE_JOBS", 4)
    monkeypatch.setattr(config, "GRANIAN_WORKERS", 2)
    _enqueue_multi_unit_job(tmp_path, units=4, job_id="spread-parent")

    claimed_by = _run_workers(4, ["worker-a", "worker-b"])

    per_worker = config.per_worker_generate_limit()
    assert per_worker == 2
    # The whole n=4 job is claimed, but no worker exceeds its fair share.
    assert sum(len(units) for units in claimed_by.values()) == 4
    assert all(len(units) <= per_worker for units in claimed_by.values())
    assert all(len(units) >= 1 for units in claimed_by.values())
    assert sorted(
        unit_id
        for units in claimed_by.values()
        for unit_id in units
    ) == sorted(set(unit_id for units in claimed_by.values() for unit_id in units))


def test_global_cap_holds_with_more_workers_than_slots(tmp_path, monkeypatch):
    _configure_runtime(tmp_path)
    monkeypatch.setattr(config, "MAX_ACTIVE_GENERATE_JOBS", 2)
    monkeypatch.setattr(config, "GRANIAN_WORKERS", 4)
    _enqueue_multi_unit_job(tmp_path, units=4, job_id="capped-parent")

    claimed_by = _run_workers(4, ["worker-a", "worker-b", "worker-c", "worker-d"])

    assert config.per_worker_generate_limit() == 1
    # Only two workers can ever hold a unit, and the global cap is respected.
    assert sum(len(units) for units in claimed_by.values()) == 2
    assert len([units for units in claimed_by.values() if units]) == 2
    assert all(len(units) <= 1 for units in claimed_by.values())
    assert _running_unit_count() == 2


def test_single_worker_claims_entire_global_budget(tmp_path, monkeypatch):
    _configure_runtime(tmp_path)
    monkeypatch.setattr(config, "MAX_ACTIVE_GENERATE_JOBS", 3)
    monkeypatch.setattr(config, "GRANIAN_WORKERS", 1)
    _enqueue_multi_unit_job(tmp_path, units=3, job_id="single-parent")

    claimed_by = _run_workers(3, ["worker-a"])

    assert config.per_worker_generate_limit() == 3
    assert len(claimed_by["worker-a"]) == 3


# ── precheck mirrors the global running cap ──────────────────────────────────


def test_precheck_respects_running_limit(tmp_path):
    _configure_runtime(tmp_path)
    db_repo._ensure_database()
    _enqueue_multi_unit_job(tmp_path, units=2, job_id="running-parent")

    for index in range(2):
        claimed = image_jobs_repo.claim_next_image_job_unit(
            worker_id=f"worker-{index}",
            claim_token=f"token-{index}",
            lease_expires_at="2099-01-01T00:00:00+00:00",
            now=utc_now(),
            running_limit=2,
        )
        assert claimed is not None

    # No queued units left: nothing to claim either way.
    assert (
        image_jobs_repo.has_claimable_image_job_unit(
            utc_now(), 2, running_limit=2
        )
        is False
    )

    _enqueue_multi_unit_job(tmp_path, units=1, job_id="queued-parent")

    # Saturated: the write transaction would be wasted, so the precheck says no.
    assert (
        image_jobs_repo.has_claimable_image_job_unit(
            utc_now(), 2, running_limit=2
        )
        is False
    )
    # A free global slot makes the queued unit claimable.
    assert (
        image_jobs_repo.has_claimable_image_job_unit(
            utc_now(), 2, running_limit=3
        )
        is True
    )
    # Legacy callers without a running limit keep the old (ungated) behavior.
    assert image_jobs_repo.has_claimable_image_job_unit(utc_now()) is True


def test_precheck_allows_reclaiming_expired_lease(tmp_path):
    _configure_runtime(tmp_path)
    db_repo._ensure_database()
    _enqueue_multi_unit_job(tmp_path, units=1, job_id="expired-parent")
    unit_id = str(
        image_jobs_repo.claim_next_image_job_unit(
            worker_id="worker-a",
            claim_token="token-a",
            lease_expires_at="2099-01-01T00:00:00+00:00",
            now=utc_now(),
            running_limit=1,
        )["unit_id"]
    )

    with db_repo._connect() as conn:
        with db_repo._transaction(conn):
            conn.execute(
                """
                UPDATE image_job_units
                SET claim_expires_at = '2026-01-01T00:00:00+00:00'
                WHERE unit_id = ?
                """,
                (unit_id,),
            )

    # The expired lease is excluded from the running count, so an exhausted
    # attempt reclaim is still seen as claimable while at the global cap.
    assert (
        image_jobs_repo.has_claimable_image_job_unit(
            utc_now(), 2, running_limit=1
        )
        is True
    )
