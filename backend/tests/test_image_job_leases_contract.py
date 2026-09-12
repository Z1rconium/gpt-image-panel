"""Phase 1 image-unit lease fencing contract tests (defects D1/D2)."""

from backend.tests.support.contract import *  # noqa: F403

from backend.app.integrations.upstream.errors import UpstreamApiError
from backend.app.core.utils import utc_now
from backend.app.services import job_executor


def _mark_running_with_expired_lease(
    unit_id: str,
    *,
    attempts: int,
    claim_token: str | None,
    claimed_by: str = "legacy-worker",
    expires_at: str = "2026-01-01T00:00:00+00:00",
):
    with db_repo._connect() as conn:
        with db_repo._transaction(conn):
            conn.execute(
                """
                UPDATE image_job_units
                SET status = 'running',
                    claimed_by = ?,
                    claim_token = ?,
                    attempts = ?,
                    claim_expires_at = ?,
                    stage = 'waiting_for_api',
                    started_at = '2026-01-01T00:00:00+00:00',
                    updated_at = '2026-01-01T00:00:00+00:00'
                WHERE unit_id = ?
                """,
                (claimed_by, claim_token, attempts, expires_at, unit_id),
            )


# ── migration ────────────────────────────────────────────────────────────────


def test_image_job_unit_lease_fencing_migration_adds_columns_idempotently():
    with sqlite3.connect(":memory:") as conn:
        conn.row_factory = sqlite3.Row
        conn.execute(
            """
            CREATE TABLE image_job_units (
                unit_id TEXT PRIMARY KEY,
                parent_job_id TEXT NOT NULL,
                unit_index INTEGER NOT NULL,
                status TEXT NOT NULL,
                claim_expires_at TEXT
            )
            """
        )
        conn.execute(
            """
            INSERT INTO image_job_units (unit_id, parent_job_id, unit_index, status)
            VALUES ('legacy-unit', 'legacy-parent', 0, 'running')
            """
        )

        db_repo._migration_image_job_unit_lease_fencing(conn)
        columns = db_repo._table_columns(conn, "image_job_units")
        row = conn.execute(
            "SELECT claim_token, attempts FROM image_job_units WHERE unit_id = 'legacy-unit'"
        ).fetchone()

        assert {"claim_token", "attempts"}.issubset(columns)
        assert row["claim_token"] is None
        assert row["attempts"] == 0

        index_names = {
            r["name"]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'index'"
            )
        }
        assert "idx_image_job_units_running_count" in index_names

        # Idempotent re-run.
        db_repo._migration_image_job_unit_lease_fencing(conn)
        assert {"claim_token", "attempts"}.issubset(
            db_repo._table_columns(conn, "image_job_units")
        )


# ── claim / running-count (D2) ───────────────────────────────────────────────


def test_claim_excludes_expired_leases_from_running_count(tmp_path):
    _configure_runtime(tmp_path)
    parent, units = image_jobs_repo.enqueue_image_job(
        parent_job={"job_id": "claim-parent", "status": "queued"},
        operation="generation",
        request={"prompt": "expired", "n": 1},
        image_units=1,
        api_preset_id="default",
        api_preset_name="Default",
        api_path="/v1/images/generations",
        max_active_generate_jobs=2,
        max_queued_generate_jobs=2,
        max_pending_edit_source_bytes=1024 * 1024,
    )
    unit_id = str(units[0]["unit_id"])
    # Simulate a unit left running by a pre-fencing version: no token, attempts 0.
    _mark_running_with_expired_lease(
        unit_id, attempts=0, claim_token=None, claimed_by="legacy"
    )

    reclaimed = image_jobs_repo.claim_next_image_job_unit(
        worker_id="worker-a",
        claim_token="token-a",
        lease_expires_at="2099-01-01T00:00:00+00:00",
        now="2026-01-01T00:00:01+00:00",
        running_limit=1,
        max_attempts=2,
    )
    assert reclaimed is not None
    assert reclaimed["unit_id"] == unit_id
    assert reclaimed["attempts"] == 1
    assert reclaimed["claim_token"] == "token-a"

    # The active (unexpired) lease now counts against the limit.
    blocked = image_jobs_repo.claim_next_image_job_unit(
        worker_id="worker-b",
        claim_token="token-b",
        lease_expires_at="2099-01-01T00:00:00+00:00",
        now="2026-01-01T00:00:02+00:00",
        running_limit=1,
        max_attempts=2,
    )
    assert blocked is None


def test_claim_caps_attempts_and_marks_exhausted(tmp_path):
    _configure_runtime(tmp_path)
    parent, units = image_jobs_repo.enqueue_image_job(
        parent_job={"job_id": "exhaust-parent", "status": "queued"},
        operation="generation",
        request={"prompt": "exhaust", "n": 1},
        image_units=1,
        api_preset_id="default",
        api_preset_name="Default",
        api_path="/v1/images/generations",
        max_active_generate_jobs=2,
        max_queued_generate_jobs=2,
        max_pending_edit_source_bytes=1024 * 1024,
    )
    unit_id = str(units[0]["unit_id"])

    first = image_jobs_repo.claim_next_image_job_unit(
        worker_id="worker-a",
        claim_token="token-1",
        lease_expires_at="2026-01-01T00:00:00+00:00",
        now="2026-01-01T00:00:00+00:00",
        running_limit=2,
        max_attempts=2,
    )
    assert first is not None and first["attempts"] == 1

    second = image_jobs_repo.claim_next_image_job_unit(
        worker_id="worker-b",
        claim_token="token-2",
        lease_expires_at="2026-01-01T00:00:00+00:00",
        now="2026-01-01T00:00:01+00:00",
        running_limit=2,
        max_attempts=2,
    )
    assert second is not None and second["attempts"] == 2

    # Exhausted: no further claim once attempts reached the cap.
    third = image_jobs_repo.claim_next_image_job_unit(
        worker_id="worker-c",
        claim_token="token-3",
        lease_expires_at="2099-01-01T00:00:00+00:00",
        now="2026-01-01T00:00:02+00:00",
        running_limit=2,
        max_attempts=2,
    )
    assert third is None

    exhausted = image_jobs_repo.expire_exhausted_image_job_units(
        "2026-01-01T00:00:03+00:00", max_attempts=2
    )
    assert [str(unit["unit_id"]) for unit in exhausted] == [unit_id]
    assert all(str(unit["parent_job_id"]) == parent["job_id"] for unit in exhausted)
    assert exhausted[0]["status"] == "interrupted"
    assert exhausted[0]["stage"] == "interrupted"
    assert exhausted[0].get("claim_token") is None

    stored = image_jobs_repo.get_image_job_unit(unit_id)
    assert stored["status"] == "interrupted"
    assert stored["stage"] == "interrupted"
    assert stored.get("claim_expires_at") is None
    assert stored.get("claim_token") is None

    aggregate = image_jobs_repo.aggregate_image_job_units(parent["job_id"])
    assert aggregate["all_terminal"] is True
    assert aggregate["completed"] == 1
    assert aggregate["running_count"] == 0

    # Idempotent: nothing left to expire.
    assert (
        image_jobs_repo.expire_exhausted_image_job_units(
            "2026-01-01T00:00:04+00:00", max_attempts=2
        )
        == []
    )


# ── fencing (D1) ─────────────────────────────────────────────────────────────


def test_stale_owner_cannot_complete_or_progress_unit(tmp_path):
    _configure_runtime(tmp_path)
    _parent, units = image_jobs_repo.enqueue_image_job(
        parent_job={"job_id": "fence-parent", "status": "queued"},
        operation="generation",
        request={"prompt": "fence", "n": 1},
        image_units=1,
        api_preset_id="default",
        api_preset_name="Default",
        api_path="/v1/images/generations",
        max_active_generate_jobs=2,
        max_queued_generate_jobs=2,
        max_pending_edit_source_bytes=1024 * 1024,
    )
    unit_id = str(units[0]["unit_id"])
    claimed = image_jobs_repo.claim_next_image_job_unit(
        worker_id="worker-a",
        claim_token="owner-token",
        lease_expires_at="2099-01-01T00:00:00+00:00",
        now="2026-01-01T00:00:00+00:00",
        running_limit=2,
    )
    assert claimed["claim_token"] == "owner-token"
    before = image_jobs_repo.get_image_job_unit(unit_id)

    assert (
        image_jobs_repo.renew_image_job_unit_lease(
            unit_id, "stale-token", "2099-01-01T00:00:00+00:00"
        )
        is False
    )
    assert (
        image_jobs_repo.update_image_job_unit_progress(
            unit_id,
            claim_token="stale-token",
            stage="waiting_for_api",
            message="stale progress",
        )
        is None
    )
    assert (
        image_jobs_repo.complete_image_job_unit(
            unit_id,
            claim_token="stale-token",
            result={"images": [{"image_id": "stale"}]},
            stage_timings={},
            duration="1.00s",
            completed_at="2026-01-01T00:00:10+00:00",
        )
        is None
    )
    assert (
        image_jobs_repo.fail_image_job_unit(
            unit_id,
            claim_token="stale-token",
            status="error",
            stage="generation_failed",
            message="stale failure",
            error="stale failure",
        )
        is None
    )

    after = image_jobs_repo.get_image_job_unit(unit_id)
    assert after["status"] == before["status"] == "running"
    assert after["claim_token"] == "owner-token"
    assert after["stage"] == before["stage"]

    # The real owner still owns the unit.
    assert image_jobs_repo.renew_image_job_unit_lease(
        unit_id, "owner-token", "2099-01-01T00:00:00+00:00"
    ) is True


# ── executor: renewal and lease loss ─────────────────────────────────────────


def test_lease_is_renewed_during_slow_upstream(client, monkeypatch):
    monkeypatch.setattr(config, "IMAGE_JOB_UNIT_LEASE_SECONDS", 2)
    monkeypatch.setattr(config, "IMAGE_JOB_UNIT_LEASE_RENEW_SECONDS", 0.5)

    calls: list[int] = []

    async def slow_generation_api(
        api_url,
        api_key,
        api_path,
        payload,
        api_preset_name=None,
        progress=None,
        socks5_proxy=None,
        **kwargs,
    ):
        calls.append(1)
        if progress:
            progress("waiting_for_api", "Waiting for upstream")
        await asyncio.sleep(3)
        raise UpstreamApiError("slow upstream failed after renewal window")

    monkeypatch.setattr(
        backend_main.proxy, "call_image_generation_api", slow_generation_api
    )

    renewed_tokens: list[str] = []
    real_renew = image_jobs_repo.renew_image_job_unit_lease

    def tracking_renew(unit_id, claim_token, claim_expires_at):
        renewed_tokens.append(claim_token)
        return real_renew(unit_id, claim_token, claim_expires_at)

    monkeypatch.setattr(job_executor, "renew_image_job_unit_lease", tracking_renew)

    response = client.post(
        "/api/generate", json={"prompt": "slow", "model": "gpt-image-2"}
    )
    assert response.status_code == 202
    job = _wait_for_job(client, response.json()["job_id"], timeout=15)

    assert job["status"] == "upstream_error"
    assert calls == [1]
    assert len(renewed_tokens) >= 2
    assert len(set(renewed_tokens)) == 1

    with db_repo._connect() as conn:
        row = conn.execute(
            "SELECT attempts, status FROM image_job_units"
        ).fetchone()
    assert row["attempts"] == 1
    assert row["status"] == "upstream_error"

    counters = metrics.snapshot()["counters"]
    assert counters.get("image_jobs.lease_renewed", 0) >= 2


def test_lease_loss_aborts_inflight_upstream_without_terminal_write(
    client, monkeypatch
):
    monkeypatch.setattr(config, "IMAGE_JOB_UNIT_LEASE_SECONDS", 30)
    monkeypatch.setattr(config, "IMAGE_JOB_UNIT_LEASE_RENEW_SECONDS", 0.2)

    state = {"started": False, "cancelled": False}

    async def slow_generation_api(
        api_url,
        api_key,
        api_path,
        payload,
        api_preset_name=None,
        progress=None,
        socks5_proxy=None,
        **kwargs,
    ):
        if progress:
            progress("waiting_for_api", "Waiting for upstream")
        state["started"] = True
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            state["cancelled"] = True
            raise
        return []

    monkeypatch.setattr(
        backend_main.proxy, "call_image_generation_api", slow_generation_api
    )

    async def scenario() -> dict:
        parent, units = image_jobs_repo.enqueue_image_job(
            parent_job={"job_id": "loser-parent", "status": "queued"},
            operation="generation",
            request={"prompt": "loser", "n": 1},
            image_units=1,
            api_preset_id="default",
            api_preset_name="Default",
            api_path="/v1/images/generations",
            max_active_generate_jobs=2,
            max_queued_generate_jobs=2,
            max_pending_edit_source_bytes=1024 * 1024,
        )
        unit_id = str(units[0]["unit_id"])
        claimed = image_jobs_repo.claim_next_image_job_unit(
            worker_id="worker-loser",
            claim_token="loser-token",
            lease_expires_at="2099-01-01T00:00:00+00:00",
            now=utc_now(),
            running_limit=2,
        )
        assert claimed is not None

        task = asyncio.create_task(
            job_executor.run_claimed_image_unit(dict(claimed), "worker-loser")
        )
        for _ in range(200):
            if state["started"]:
                break
            await asyncio.sleep(0.05)
        assert state["started"] is True

        # Simulate another worker taking over the unit's lease.
        with db_repo._connect() as conn:
            with db_repo._transaction(conn):
                conn.execute(
                    """
                    UPDATE image_job_units
                    SET claim_token = 'winner-token',
                        claimed_by = 'worker-winner',
                        claim_expires_at = '2099-01-01T00:00:00+00:00'
                    WHERE unit_id = ?
                    """,
                    (unit_id,),
                )

        await asyncio.wait_for(task, timeout=10)
        loser_view = image_jobs_repo.get_image_job_unit(unit_id)
        return {"unit_id": unit_id, "parent_id": parent["job_id"], "loser_view": loser_view}

    result = client.portal.call(scenario)
    assert state["cancelled"] is True

    loser_view = result["loser_view"]
    assert loser_view["status"] == "running"
    assert loser_view["claim_token"] == "winner-token"
    assert loser_view["claimed_by"] == "worker-winner"

    counters = metrics.snapshot()["counters"]
    assert counters.get("image_jobs.lease_lost", 0) >= 1

    # The new owner can finish the unit and drive the parent to a terminal state.
    completed = image_jobs_repo.complete_image_job_unit(
        result["unit_id"],
        claim_token="winner-token",
        result={
            "images": [
                {
                    "image_id": "winner-image",
                    "image_url": "/api/image/winner-image.png",
                    "completed_at": utc_now(),
                }
            ]
        },
        stage_timings={},
        duration="1.00s",
        completed_at=utc_now(),
    )
    assert completed is not None
    assert completed["status"] == "success"

    client.portal.call(
        job_executor.aggregate_parent_image_job, result["parent_id"]
    )
    parent_view = image_jobs_repo.get_generate_job(result["parent_id"])
    assert parent_view["status"] == "success"


def _run_unit_until_upstream_started(
    client,
    *,
    job_id: str,
    worker_id: str,
    claim_token: str,
    state: dict,
    upstream_started_timeout: float = 10.0,
):
    """Enqueue one unit, claim it with `claim_token`, and start the executor.

    Runs inside the app's event loop so the live dispatcher cannot interleave
    between enqueue and claim. Returns (unit_id, parent_id, task) once the
    upstream mock has reported `state["started"]`.
    """

    async def scenario():
        parent, units = image_jobs_repo.enqueue_image_job(
            parent_job={"job_id": job_id, "status": "queued"},
            operation="generation",
            request={"prompt": job_id, "n": 1},
            image_units=1,
            api_preset_id="default",
            api_preset_name="Default",
            api_path="/v1/images/generations",
            max_active_generate_jobs=2,
            max_queued_generate_jobs=2,
            max_pending_edit_source_bytes=1024 * 1024,
        )
        unit_id = str(units[0]["unit_id"])
        claimed = image_jobs_repo.claim_next_image_job_unit(
            worker_id=worker_id,
            claim_token=claim_token,
            lease_expires_at="2099-01-01T00:00:00+00:00",
            now=utc_now(),
            running_limit=2,
        )
        assert claimed is not None and claimed["unit_id"] == unit_id

        task = asyncio.create_task(
            job_executor.run_claimed_image_unit(dict(claimed), worker_id)
        )
        deadline = time.monotonic() + upstream_started_timeout
        while not state["started"] and time.monotonic() < deadline:
            await asyncio.sleep(0.02)
        assert state["started"] is True
        return unit_id, parent["job_id"], task

    return client.portal.call(scenario)


def _slow_upstream(state: dict, *, sleep_seconds: float, result=None):
    async def slow_generation_api(
        api_url,
        api_key,
        api_path,
        payload,
        api_preset_name=None,
        progress=None,
        socks5_proxy=None,
        **kwargs,
    ):
        if progress:
            progress("waiting_for_api", "Waiting for upstream")
        state["started"] = True
        try:
            await asyncio.sleep(sleep_seconds)
        except asyncio.CancelledError:
            state["cancelled"] = True
            raise
        state["finished"] = True
        if progress:
            progress("received_api_response", "Received upstream API response")
        if isinstance(result, Exception):
            raise result
        return result if result is not None else []

    return slow_generation_api


def test_outer_cancellation_aborts_inflight_upstream(client, monkeypatch):
    """Graceful shutdown cancels the executor task; the upstream request must
    be cancelled with it instead of running to completion and having its late
    progress misreported as a lost lease."""
    monkeypatch.setattr(config, "IMAGE_JOB_UNIT_LEASE_SECONDS", 30)
    monkeypatch.setattr(config, "IMAGE_JOB_UNIT_LEASE_RENEW_SECONDS", 5)

    state = {"started": False, "cancelled": False, "finished": False}
    monkeypatch.setattr(
        backend_main.proxy,
        "call_image_generation_api",
        _slow_upstream(state, sleep_seconds=1.5),
    )
    lease_lost_before = metrics.snapshot()["counters"].get("image_jobs.lease_lost", 0)

    unit_id, parent_id, task = _run_unit_until_upstream_started(
        client,
        job_id="shutdown-parent",
        worker_id="worker-shutdown",
        claim_token="shutdown-token",
        state=state,
    )

    async def cancel_and_wait():
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        # Give a leaked upstream task time to finish if it was not cancelled.
        await asyncio.sleep(2.0)
        leaked = [
            t
            for t in asyncio.all_tasks()
            if not t.done() and t.get_coro().__qualname__.endswith("run_upstream")
        ]
        return len(leaked)

    leaked = client.portal.call(cancel_and_wait)

    assert state["cancelled"] is True
    assert state["finished"] is False
    assert leaked == 0

    unit = image_jobs_repo.get_image_job_unit(unit_id)
    assert unit["status"] == "cancelled"
    assert unit.get("claim_token") is None
    parent = image_jobs_repo.get_generate_job(parent_id)
    assert parent["status"] == "cancelled"

    lease_lost_after = metrics.snapshot()["counters"].get("image_jobs.lease_lost", 0)
    assert lease_lost_after == lease_lost_before


def test_transient_renewal_error_retries_without_aborting_upstream(
    client, monkeypatch
):
    """One failing renewal must not abort an in-flight upstream call while the
    locally tracked lease is still valid; the loop retries and recovers."""
    monkeypatch.setattr(config, "IMAGE_JOB_UNIT_LEASE_SECONDS", 30)
    monkeypatch.setattr(config, "IMAGE_JOB_UNIT_LEASE_RENEW_SECONDS", 0.3)
    monkeypatch.setattr(job_executor, "IMAGE_UNIT_LEASE_RENEW_RETRY_SECONDS", 0.2)

    state = {"started": False, "cancelled": False, "finished": False}
    monkeypatch.setattr(
        backend_main.proxy,
        "call_image_generation_api",
        _slow_upstream(
            state,
            sleep_seconds=1.5,
            result=UpstreamApiError("upstream failed after renewal recovered"),
        ),
    )

    renew_calls: list[str] = []
    real_renew = image_jobs_repo.renew_image_job_unit_lease

    def flaky_renew(unit_id, claim_token, claim_expires_at):
        renew_calls.append(claim_token)
        if len(renew_calls) == 1:
            raise RuntimeError("simulated transient renewal DB failure")
        return real_renew(unit_id, claim_token, claim_expires_at)

    monkeypatch.setattr(job_executor, "renew_image_job_unit_lease", flaky_renew)
    counters_before = metrics.snapshot()["counters"]
    lease_lost_before = counters_before.get("image_jobs.lease_lost", 0)
    retry_before = counters_before.get("image_jobs.lease_renew_retry", 0)

    unit_id, parent_id, task = _run_unit_until_upstream_started(
        client,
        job_id="flaky-renew-parent",
        worker_id="worker-flaky",
        claim_token="flaky-token",
        state=state,
    )

    async def wait_task():
        await asyncio.wait_for(task, timeout=10)

    client.portal.call(wait_task)

    assert state["cancelled"] is False
    assert state["finished"] is True
    assert len(renew_calls) >= 2
    assert set(renew_calls) == {"flaky-token"}

    unit = image_jobs_repo.get_image_job_unit(unit_id)
    assert unit["status"] == "upstream_error"
    assert unit.get("claim_token") is None
    parent = image_jobs_repo.get_generate_job(parent_id)
    assert parent["status"] == "upstream_error"

    counters_after = metrics.snapshot()["counters"]
    assert counters_after.get("image_jobs.lease_lost", 0) == lease_lost_before
    assert counters_after.get("image_jobs.lease_renew_retry", 0) >= retry_before + 1


def test_persistent_renewal_errors_abort_before_lease_deadline(client, monkeypatch):
    """Renewal keeps retrying while the local lease is valid, then abandons the
    unit shortly before expiry without writing a terminal state."""
    monkeypatch.setattr(config, "IMAGE_JOB_UNIT_LEASE_SECONDS", 2)
    monkeypatch.setattr(config, "IMAGE_JOB_UNIT_LEASE_RENEW_SECONDS", 0.3)
    monkeypatch.setattr(job_executor, "IMAGE_UNIT_LEASE_RENEW_RETRY_SECONDS", 0.2)

    state = {"started": False, "cancelled": False, "finished": False}
    monkeypatch.setattr(
        backend_main.proxy,
        "call_image_generation_api",
        _slow_upstream(state, sleep_seconds=30),
    )

    renew_calls: list[str] = []

    def failing_renew(unit_id, claim_token, claim_expires_at):
        renew_calls.append(claim_token)
        raise RuntimeError("simulated persistent renewal DB failure")

    monkeypatch.setattr(job_executor, "renew_image_job_unit_lease", failing_renew)
    lease_lost_before = metrics.snapshot()["counters"].get("image_jobs.lease_lost", 0)

    unit_id, _parent_id, task = _run_unit_until_upstream_started(
        client,
        job_id="renew-fail-parent",
        worker_id="worker-renew",
        claim_token="renew-token",
        state=state,
    )
    upstream_started_at = time.monotonic()

    async def wait_task():
        await asyncio.wait_for(task, timeout=10)

    client.portal.call(wait_task)
    elapsed = time.monotonic() - upstream_started_at

    assert state["cancelled"] is True
    # Retried at least once instead of giving up on the first error...
    assert len(renew_calls) >= 2
    # ...but abandoned before the 2s lease could actually expire.
    assert elapsed < config.IMAGE_JOB_UNIT_LEASE_SECONDS + 1.0

    loser_view = image_jobs_repo.get_image_job_unit(unit_id)
    assert loser_view["status"] == "running"
    assert loser_view["claim_token"] == "renew-token"

    counters = metrics.snapshot()["counters"]
    assert counters.get("image_jobs.lease_lost", 0) == lease_lost_before + 1


def test_renew_interval_clamps_when_misconfigured(monkeypatch):
    monkeypatch.setattr(config, "IMAGE_JOB_UNIT_LEASE_SECONDS", 120)
    monkeypatch.setattr(config, "IMAGE_JOB_UNIT_LEASE_RENEW_SECONDS", 100.0)
    assert job_executor.image_unit_lease_renew_interval() == 30.0

    monkeypatch.setattr(config, "IMAGE_JOB_UNIT_LEASE_SECONDS", 30)
    monkeypatch.setattr(config, "IMAGE_JOB_UNIT_LEASE_RENEW_SECONDS", 6.0)
    assert job_executor.image_unit_lease_renew_interval() == 6.0

    monkeypatch.setattr(config, "IMAGE_JOB_UNIT_LEASE_RENEW_SECONDS", 15.0)
    assert job_executor.image_unit_lease_renew_interval() == 7.5

    monkeypatch.setattr(config, "IMAGE_JOB_UNIT_LEASE_RENEW_SECONDS", 0.0)
    assert job_executor.image_unit_lease_renew_interval() == 7.5
