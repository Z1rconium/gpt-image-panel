"""Phase 1 remote-task checkpoint, recovery, and idempotency contract tests.

Covers the async provider mapping v2 contract, checkpoint persistence and
fencing, bounded takeover recovery, submit-unknown handling, poll retry
behaviour, remote-cancel policy, and stable gallery result ids.
"""

import asyncio
import base64
import json
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from backend.app.core import media
from backend.app.core.diagnostics import UnitDiagnostics
from backend.app.integrations.upstream import async_provider
from backend.app.runtime import state as runtime_state
from backend.app.schemas.generation import GenerateRequest
from backend.app.services import job_executor
from backend.app.services import presets as presets_service
from backend.tests.support.contract import (
    ORIGINAL_CALL_IMAGE_GENERATION_API,
    PNG_BYTES,
    _configure_runtime,
    _FakeResponse,
    db_repo,
    gallery_mutations,
    image_jobs_repo,
)

API_URL = "https://queue.example.com"
MODEL = "vendor/image-model"

V1_CONFIG = {
    "version": 1,
    "auth": {"header": "Authorization", "scheme": "Key"},
    "submit": {"path": "/submit", "body": {"prompt": "{{prompt}}"}},
    "poll": {
        "url_path": "$.status_url",
        "status_path": "$.state",
        "done": ["done"],
        "failed": ["failed"],
        "interval_seconds": 1,
    },
    "result": {
        "url_path": "$.response_url",
        "images_path": "$.images[*].url",
        "image_kind": "url",
    },
    "cancel": {"url_path": "$.cancel_url", "method": "PUT"},
}

V2_CONFIG = {
    "version": 2,
    "auth": {"header": "Authorization", "scheme": "Key"},
    "submit": {
        "path": "/submit",
        "body": {"prompt": "{{prompt}}"},
        "idempotency_header": "Idempotency-Key",
    },
    "poll": {
        "task_id_path": "$.id",
        "url_template": "/jobs/{{task_id}}",
        "status_path": "$.state",
        "done": ["done"],
        "failed": ["failed"],
        "interval_seconds": 1,
    },
    "result": {
        "task_id_path": "$.id",
        "url_template": "/jobs/{{task_id}}/result",
        "images_path": "$.images[*].url",
        "image_kind": "url",
    },
    "cancel": {
        "task_id_path": "$.id",
        "url_template": "/jobs/{{task_id}}/cancel",
        "method": "DELETE",
    },
}

V2_STATUS_URL = f"{API_URL}/jobs/job-1"
V2_RESULT_URL = f"{API_URL}/jobs/job-1/result"
V2_CANCEL_URL = f"{API_URL}/jobs/job-1/cancel"


def _json(body, status=200):
    return _FakeResponse(
        status,
        headers={"Content-Type": "application/json"},
        chunks=[json.dumps(body).encode("utf-8")],
    )


class _ScriptedSession:
    """Serves canned responses per (method, url); the last one repeats."""

    def __init__(self, routes):
        self.routes = {key: list(value) for key, value in routes.items()}
        self.calls = []

    def _serve(self, method, url, kwargs):
        self.calls.append((method, url, kwargs))
        queue = self.routes.get((method, url))
        assert queue, f"unexpected request: {method} {url}"
        item = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(item, BaseException):
            raise item
        return item

    def post(self, url, **kwargs):
        return self._serve("POST", url, kwargs)

    def get(self, url, **kwargs):
        return self._serve("GET", url, kwargs)

    def put(self, url, **kwargs):
        return self._serve("PUT", url, kwargs)

    def delete(self, url, **kwargs):
        return self._serve("DELETE", url, kwargs)

    def methods(self):
        return [(method, url) for method, url, _ in self.calls]


@pytest.fixture
def provider_session(monkeypatch):
    async def no_sleep(_seconds):
        return None

    async def no_validate(*_args, **_kwargs):
        return None

    monkeypatch.setattr(async_provider, "_sleep", no_sleep)
    monkeypatch.setattr(async_provider.ssrf, "validate_upstream_url", lambda *a, **k: None)
    monkeypatch.setattr(async_provider.ssrf, "validate_upstream_url_async", no_validate)
    monkeypatch.setattr(async_provider.ssrf, "validate_response_peer_ip", lambda *a, **k: None)

    def use(session):
        pool = type("_Pool", (), {"get": lambda self, **kwargs: session})()
        monkeypatch.setattr(async_provider, "get_pool", lambda: pool)
        return session

    return use


def _request(**overrides):
    fields = {"prompt": "a red fox", "model": MODEL, "size": "1024x1024"}
    fields.update(overrides)
    return GenerateRequest(**fields)


def _run_provider(session, config, *, remote=None, checkpoint=None, should_cancel_remote=None,
                  diagnostics=None):
    return asyncio.run(
        async_provider.run_async_provider(
            api_url=API_URL,
            api_key="test-key",
            provider_config=config,
            payload=_request(),
            progress=None,
            socks5_proxy=None,
            remote=remote,
            checkpoint=checkpoint,
            should_cancel_remote=should_cancel_remote,
            diagnostics=diagnostics,
        )
    )


def _future_deadline(seconds=300):
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat()


# ── provider checkpoint / resume ─────────────────────────────────────────────


def test_fresh_v2_run_renders_task_id_urls_and_checkpoints(provider_session):
    routes = {
        ("POST", f"{API_URL}/submit"): [_json({"id": "job-1", "state": "queued"})],
        ("GET", V2_STATUS_URL): [_json({"state": "done"})],
        ("GET", V2_RESULT_URL): [_json({"images": [{"url": "https://cdn.example.com/a.png"}]})],
    }
    session = provider_session(_ScriptedSession(routes))
    states = []

    async def checkpoint(state):
        states.append(dict(state))

    items, _preview = _run_provider(session, V2_CONFIG, checkpoint=checkpoint)

    assert items == [{"url": "https://cdn.example.com/a.png"}]
    assert [state["phase"] for state in states] == ["submitting", "submitted"]
    submitted = states[1]
    assert submitted["task_id"] == "job-1"
    assert submitted["status_url"] == V2_STATUS_URL
    assert submitted["result_url"] == V2_RESULT_URL
    assert submitted["cancel_url"] == V2_CANCEL_URL
    assert submitted["deadline_at"]
    submit_call = session.calls[0][2]
    assert submit_call["headers"]["Idempotency-Key"] == states[0]["idempotency_key"]
    assert states[0]["provider_config"]["version"] == 2


def test_resume_polls_stored_urls_without_resubmitting(provider_session):
    session = provider_session(
        _ScriptedSession(
            {
                ("GET", V2_STATUS_URL): [_json({"state": "done"})],
                ("GET", V2_RESULT_URL): [_json({"images": [{"url": "https://cdn.example.com/a.png"}]})],
            }
        )
    )
    remote = {
        "phase": "submitted",
        "api_url": API_URL,
        "provider_config": V2_CONFIG,
        "submitted_at": "2026-01-01T00:00:00+00:00",
        "deadline_at": _future_deadline(),
        "task_id": "job-1",
        "status_url": V2_STATUS_URL,
        "result_url": V2_RESULT_URL,
        "cancel_url": V2_CANCEL_URL,
    }
    items, _preview = _run_provider(session, V2_CONFIG, remote=remote)

    assert items == [{"url": "https://cdn.example.com/a.png"}]
    assert session.methods() == [("GET", V2_STATUS_URL), ("GET", V2_RESULT_URL)]


def test_resume_after_deadline_times_out_and_cancels_remote(provider_session):
    session = provider_session(
        _ScriptedSession(
            {
                ("GET", V2_STATUS_URL): [_json({"state": "running"})],
                ("DELETE", V2_CANCEL_URL): [_json({})],
            }
        )
    )
    remote = {
        "phase": "submitted",
        "api_url": API_URL,
        "provider_config": V2_CONFIG,
        "deadline_at": (
            datetime.now(timezone.utc) - timedelta(seconds=30)
        ).isoformat(),
        "task_id": "job-1",
        "status_url": V2_STATUS_URL,
        "cancel_url": V2_CANCEL_URL,
    }
    with pytest.raises(async_provider.UpstreamApiError, match="did not finish within"):
        _run_provider(session, V2_CONFIG, remote=remote)
    assert ("GET", V2_STATUS_URL) not in session.methods()
    assert ("DELETE", V2_CANCEL_URL) in session.methods()


def test_poll_request_cannot_outlive_the_absolute_deadline(provider_session, monkeypatch):
    session = provider_session(_ScriptedSession({}))
    cancelled = []

    async def stalled_poll(*args, **kwargs):
        try:
            await asyncio.sleep(10)
        finally:
            cancelled.append(True)

    monkeypatch.setattr(async_provider, "_poll_json", stalled_poll)
    remote = {"phase": "submitted", "api_url": API_URL,
              "status_url": V2_STATUS_URL, "deadline_at": _future_deadline(0.03)}
    with pytest.raises(async_provider.UpstreamApiError, match="did not finish within"):
        _run_provider(session, V2_CONFIG, remote=remote)
    assert cancelled == [True]


def test_unknown_submit_without_idempotency_key_is_never_resubmitted(provider_session):
    session = provider_session(_ScriptedSession({}))
    remote = {
        "phase": "submitting",
        "api_url": API_URL,
        "provider_config": V2_CONFIG,
        "submitted_at": "2026-01-01T00:00:00+00:00",
    }
    with pytest.raises(async_provider.UpstreamApiError, match="unknown"):
        _run_provider(session, V2_CONFIG, remote=remote)
    assert session.calls == []


def test_submitting_phase_resubmits_with_the_same_idempotency_key(provider_session):
    routes = {
        ("POST", f"{API_URL}/submit"): [_json({"id": "job-1", "state": "queued"})],
        ("GET", V2_STATUS_URL): [_json({"state": "done"})],
        ("GET", V2_RESULT_URL): [_json({"images": [{"url": "https://cdn.example.com/a.png"}]})],
    }
    session = provider_session(_ScriptedSession(routes))
    remote = {
        "phase": "submitting",
        "api_url": API_URL,
        "provider_config": V2_CONFIG,
        "submitted_at": "2026-01-01T00:00:00+00:00",
        "idempotency_key": "stable-key-1",
    }
    items, _preview = _run_provider(session, V2_CONFIG, remote=remote)
    assert items
    assert session.calls[0][2]["headers"]["Idempotency-Key"] == "stable-key-1"


@pytest.mark.parametrize("phase", ["submitted", "submitting"])
def test_checkpoint_cannot_send_current_credentials_to_a_previous_origin(provider_session, phase):
    session = provider_session(_ScriptedSession({}))
    remote = {"phase": phase, "api_url": "https://previous.example.com", "idempotency_key": "saved-key",
              "status_url": "https://previous.example.com/jobs/1", "deadline_at": _future_deadline()}
    with pytest.raises(async_provider.UpstreamApiError, match="checkpoint origin"):
        _run_provider(session, V2_CONFIG, remote=remote)
    assert session.calls == []


def test_retryable_poll_errors_back_off_until_success(provider_session):
    routes = {
        ("POST", f"{API_URL}/submit"): [
            _json(
                {
                    "status_url": f"{API_URL}/jobs/1",
                    "response_url": f"{API_URL}/jobs/1/result",
                    "state": "queued",
                }
            )
        ],
        ("GET", f"{API_URL}/jobs/1"): [
            _json({"error": "busy"}, status=503),
            _json({"error": "slow down"}, status=429),
            _json({"state": "done"}),
        ],
        ("GET", f"{API_URL}/jobs/1/result"): [
            _json({"images": [{"url": "https://cdn.example.com/a.png"}]})
        ],
    }
    session = provider_session(_ScriptedSession(routes))
    items, _preview = _run_provider(session, V1_CONFIG)
    assert items
    status_calls = [m for m in session.methods() if m == ("GET", f"{API_URL}/jobs/1")]
    assert len(status_calls) == 3


def test_terminal_poll_error_does_not_retry_or_resubmit(provider_session):
    routes = {
        ("POST", f"{API_URL}/submit"): [
            _json({"status_url": f"{API_URL}/jobs/2", "state": "queued"})
        ],
        ("GET", f"{API_URL}/jobs/2"): [_json({"detail": "gone"}, status=404)],
    }
    session = provider_session(_ScriptedSession(routes))
    with pytest.raises(async_provider.UpstreamApiError):
        _run_provider(session, V1_CONFIG)
    assert session.methods() == [
        ("POST", f"{API_URL}/submit"),
        ("GET", f"{API_URL}/jobs/2"),
    ]


def test_abort_only_cancels_remote_on_user_cancellation(provider_session, monkeypatch):
    async def cancelling_sleep(_seconds):
        raise asyncio.CancelledError()

    async def deny_cancel():
        return False

    async def allow_cancel():
        return True

    def build_session():
        routes = {
            ("POST", f"{API_URL}/submit"): [
                _json({"status_url": f"{API_URL}/jobs/3", "cancel_url": f"{API_URL}/jobs/3/cancel"})
            ],
            ("GET", f"{API_URL}/jobs/3"): [_json({"state": "running"})],
            ("PUT", f"{API_URL}/jobs/3/cancel"): [_json({})],
        }
        return _ScriptedSession(routes)

    monkeypatch.setattr(async_provider, "_sleep", cancelling_sleep)
    session = provider_session(build_session())
    with pytest.raises(asyncio.CancelledError):
        _run_provider(session, V1_CONFIG, should_cancel_remote=deny_cancel)
    assert ("PUT", f"{API_URL}/jobs/3/cancel") not in session.methods()

    session = provider_session(build_session())
    with pytest.raises(asyncio.CancelledError):
        _run_provider(session, V1_CONFIG, should_cancel_remote=allow_cancel)
    assert ("PUT", f"{API_URL}/jobs/3/cancel") in session.methods()


def test_diagnostics_capture_missing_task_id(provider_session):
    session = provider_session(
        _ScriptedSession({("POST", f"{API_URL}/submit"): [_json({"nope": 1})]})
    )
    diagnostics = UnitDiagnostics()
    with pytest.raises(async_provider.UpstreamApiError, match="task id"):
        _run_provider(session, V2_CONFIG, diagnostics=diagnostics)
    payload = diagnostics.payload()
    assert payload["code"] == "task_id_missing"
    assert payload["stages"][0]["snapshot"] == {"nope": 1}


def test_diagnostics_redact_resolved_request_key_in_arbitrary_fields(provider_session):
    session = provider_session(_ScriptedSession({
        ("POST", f"{API_URL}/submit"): [_json({"message": "rejected test-key", "test-key": "echo"})],
    }))
    diagnostics = UnitDiagnostics()
    with pytest.raises(async_provider.UpstreamApiError, match="task id"):
        _run_provider(session, V2_CONFIG, diagnostics=diagnostics)
    assert "test-key" not in json.dumps(diagnostics.payload())


def test_diagnostics_redact_snapshot_keys_without_changing_record_schema():
    diagnostics = UnitDiagnostics()
    diagnostics.add_secrets("phase")
    diagnostics.record_event("submit", "rejected phase", snapshot={"phase": "echo"})
    stage = diagnostics.payload()["stages"][0]
    assert stage["phase"] == "submit"
    assert stage["snapshot"] == {"[REDACTED]": "echo"}
    assert stage["message"] == "rejected [REDACTED]"


@pytest.mark.parametrize("max_bytes", [1024, 64 * 1024])
def test_diagnostics_cap_covers_metadata_and_utf8_bytes(max_bytes):
    diagnostics = UnitDiagnostics(max_bytes=max_bytes)
    large = "界" * 20_000
    diagnostics.set_code(large)
    diagnostics.set_recovery(**{f"field-{index}": large for index in range(20)})
    for _ in range(12):
        diagnostics.record_http(large, method=large, url="https://example.com/" + large,
                                snapshot={large: large}, extra={"field": large})
    assert len(json.dumps(diagnostics.payload(), ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")) <= max_bytes


# ── diagnostics bounding / redaction ─────────────────────────────────────────


def test_diagnostics_snapshots_are_redacted_and_bounded():
    diagnostics = UnitDiagnostics()
    diagnostics.record_http(
        "submit",
        method="POST",
        url="https://api.example.com/submit?token=signature",
        status=200,
        snapshot={
            "authorization": "Bearer sekret-value",
            "image": "data:image/png;base64," + "A" * 200_000,
            "items": list(range(500)),
        },
    )
    payload = diagnostics.payload()
    encoded = json.dumps(payload)
    assert len(encoded.encode("utf-8")) <= 64 * 1024
    assert "sekret-value" not in encoded
    assert "token=signature" not in encoded
    stage = payload["stages"][0]
    assert stage["snapshot"]["authorization"] == "[REDACTED]"
    assert stage["snapshot"]["image"] == "[image data omitted]"
    assert "more items omitted" in json.dumps(stage["snapshot"]["items"])


# ── persistence: migration, fencing, recovery bounds ─────────────────────────


def _enqueue(parent_id: str, *, preset_id: str = "default", preset_name: str = "Default", request: dict | None = None):
    return image_jobs_repo.enqueue_image_job(
        parent_job={"job_id": parent_id, "status": "queued"},
        operation="generation",
        request=request or {"prompt": "checkpoint", "n": 1},
        image_units=1,
        api_preset_id=preset_id,
        api_preset_name=preset_name,
        api_path="/v1/images/generations",
        max_active_generate_jobs=4,
        max_queued_generate_jobs=4,
        max_pending_edit_source_bytes=1024 * 1024,
    )


def _mark_running_with_expired_lease(unit_id, *, attempts, claim_token, recovery_count=None):
    with db_repo._connect() as conn:
        with db_repo._transaction(conn):
            conn.execute(
                """
                UPDATE image_job_units
                SET status = 'running',
                    claimed_by = 'old-worker',
                    claim_token = ?,
                    attempts = ?,
                    recovery_count = COALESCE(?, recovery_count),
                    claim_expires_at = '2026-01-01T00:00:00+00:00',
                    updated_at = '2026-01-01T00:00:00+00:00'
                WHERE unit_id = ?
                """,
                (claim_token, attempts, recovery_count, unit_id),
            )


def test_recovery_migration_adds_checkpoint_columns(tmp_path):
    _configure_runtime(tmp_path)
    db_repo.verify_storage_writable()
    with db_repo._connect() as conn:
        columns = db_repo._table_columns(conn, "image_job_units")
    assert {"remote_json", "checkpointed", "recovery_count", "diagnostics_json"}.issubset(
        columns
    )


def test_checkpoint_write_requires_the_current_claim_token(tmp_path):
    _configure_runtime(tmp_path)
    _parent, units = _enqueue(f"ckpt-{uuid.uuid4().hex}")
    unit_id = str(units[0]["unit_id"])
    claimed = image_jobs_repo.claim_next_image_job_unit(
        worker_id="worker-a",
        claim_token="token-a",
        lease_expires_at="2099-01-01T00:00:00+00:00",
        now="2026-01-01T00:00:01+00:00",
        running_limit=4,
        max_attempts=2,
    )
    assert claimed is not None

    assert (
        image_jobs_repo.write_image_job_unit_remote(
            unit_id,
            claim_token="stale-token",
            remote={"phase": "submitting"},
            checkpointed=True,
        )
        is False
    )
    assert image_jobs_repo.write_image_job_unit_remote(
        unit_id,
        claim_token="token-a",
        remote={"phase": "submitted", "task_id": "t-1"},
        checkpointed=True,
    )
    row = image_jobs_repo.get_image_job_unit(unit_id)
    assert row["remote"]["task_id"] == "t-1"
    assert row["checkpointed"] == 1
    assert row["recovery_count"] == 0


def test_checkpointed_unit_reclaims_past_attempt_limit_until_recovery_bound(tmp_path):
    _configure_runtime(tmp_path)
    _parent, units = _enqueue(f"recover-{uuid.uuid4().hex}")
    unit_id = str(units[0]["unit_id"])
    image_jobs_repo.claim_next_image_job_unit(
        worker_id="worker-a",
        claim_token="token-a",
        lease_expires_at="2099-01-01T00:00:00+00:00",
        now="2026-01-01T00:00:01+00:00",
        running_limit=4,
        max_attempts=2,
    )
    image_jobs_repo.write_image_job_unit_remote(
        unit_id,
        claim_token="token-a",
        remote={"phase": "submitted", "status_url": "https://queue.example.com/jobs/1"},
        checkpointed=True,
    )
    _mark_running_with_expired_lease(unit_id, attempts=2, claim_token="token-a")

    # attempts == max_attempts, but a checkpoint earns an independent takeover.
    reclaimed = image_jobs_repo.claim_next_image_job_unit(
        worker_id="worker-b",
        claim_token="token-b",
        lease_expires_at="2099-01-01T00:00:00+00:00",
        now="2026-01-01T00:00:02+00:00",
        running_limit=4,
        max_attempts=2,
        max_recoveries=2,
    )
    assert reclaimed is not None
    assert reclaimed["attempts"] == 3
    assert reclaimed["recovery_count"] == 1

    _mark_running_with_expired_lease(
        unit_id, attempts=3, claim_token="token-b", recovery_count=2
    )
    assert (
        image_jobs_repo.claim_next_image_job_unit(
            worker_id="worker-c",
            claim_token="token-c",
            lease_expires_at="2099-01-01T00:00:00+00:00",
            now="2026-01-01T00:00:03+00:00",
            running_limit=4,
            max_attempts=2,
            max_recoveries=2,
        )
        is None
    )
    expired = image_jobs_repo.expire_exhausted_image_job_units(
        "2026-01-01T00:00:04+00:00", 2, 2
    )
    assert [unit["unit_id"] for unit in expired] == [unit_id]
    row = image_jobs_repo.get_image_job_unit(unit_id)
    assert row["status"] == "interrupted"
    assert "recovery" in row["error"].lower()


def test_legacy_running_unit_still_uses_attempt_bound(tmp_path):
    _configure_runtime(tmp_path)
    _parent, units = _enqueue(f"legacy-{uuid.uuid4().hex}")
    unit_id = str(units[0]["unit_id"])
    _mark_running_with_expired_lease(unit_id, attempts=2, claim_token=None)
    expired = image_jobs_repo.expire_exhausted_image_job_units(
        "2026-01-01T00:00:04+00:00", 2, 2
    )
    assert [unit["unit_id"] for unit in expired] == [unit_id]
    row = image_jobs_repo.get_image_job_unit(unit_id)
    assert row["status"] == "interrupted"
    assert "multiple attempts" in row["error"]


# ── executor recovery paths ──────────────────────────────────────────────────

V2_B64_CONFIG = {
    **V2_CONFIG,
    "result": {
        **V2_CONFIG["result"],
        "images_path": "$.images[*].b64",
        "image_kind": "b64_json",
    },
}


class _Pool:
    def __init__(self, session):
        self.session = session

    def get(self, **kwargs):
        return self.session


def _use_session(monkeypatch, session):
    pool = _Pool(session)
    monkeypatch.setattr(async_provider, "get_pool", lambda: pool)
    monkeypatch.setattr(job_executor.proxy, "get_pool", lambda: pool)
    return session


def _install_async_preset(monkeypatch, *, provider_config):
    monkeypatch.setenv("RECOVERY_TEST_KEY", "recovery-secret")
    presets_service.load_api_settings()
    preset = {
        "id": "recovery-preset",
        "name": "recovery",
        "api_url": API_URL,
        "api_key": "${RECOVERY_TEST_KEY}",
        "api_path": "/v1/images/generations",
        "default_model": MODEL,
        "default_response_format": "url",
        "supports_mask": False,
        "prompt_guard": False,
        "provider_kind": "async_json",
        "provider_config": provider_config,
    }
    runtime_state.state.api_presets = [preset]
    presets_service.apply_api_preset(preset)
    presets_service.persist_api_settings()
    return preset


def test_results_ready_checkpoint_completes_without_upstream(tmp_path, monkeypatch):
    _configure_runtime(tmp_path)
    _install_async_preset(monkeypatch, provider_config=V1_CONFIG)
    _enqueue(f"ready-{uuid.uuid4().hex}", preset_id="recovery-preset", preset_name="recovery")
    claimed = image_jobs_repo.claim_next_image_job_unit(
        worker_id="worker-a",
        claim_token="token-a",
        lease_expires_at="2099-01-01T00:00:00+00:00",
        now="2026-01-01T00:00:01+00:00",
        running_limit=4,
        max_attempts=2,
    )
    unit_id = str(claimed["unit_id"])
    completion = {
        "result": {"images": [{"image_id": "img-1", "image_url": "/api/gallery/img-1"}]},
        "stage_timings": {"upstream_wait": 1.0},
        "duration": "1.00s",
        "completed_at": "2026-01-01T00:00:05+00:00",
        "usage": None,
        "cost": None,
    }
    assert image_jobs_repo.write_image_job_unit_remote(
        unit_id,
        claim_token="token-a",
        remote={"phase": "results_ready", "completion": completion},
        checkpointed=True,
    )
    resumed = image_jobs_repo.get_image_job_unit(unit_id)
    assert resumed is not None and resumed["remote"]["phase"] == "results_ready"

    called = []

    async def fail_if_called(*_args, **_kwargs):
        called.append(True)
        raise AssertionError("results_ready recovery must not call upstream")

    monkeypatch.setattr(job_executor.proxy, "call_image_generation_api", fail_if_called)
    asyncio.run(job_executor.run_claimed_image_unit(resumed, "worker-a"))

    row = image_jobs_repo.get_image_job_unit(unit_id)
    assert row["status"] == "success"
    assert row["result"]["images"][0]["image_id"] == "img-1"
    assert called == []


@pytest.mark.parametrize("origin_changed", [False, True])
def test_queued_provider_snapshot_survives_preset_changes(tmp_path, monkeypatch, origin_changed):
    _configure_runtime(tmp_path)
    preset = _install_async_preset(monkeypatch, provider_config=V1_CONFIG)
    snapshot = {key: preset.get(key) for key in ("api_url", "provider_kind", "provider_config", "prompt_guard", "supports_mask")}
    parent_id = f"queued-{uuid.uuid4().hex}"
    _enqueue(parent_id, preset_id=preset["id"], preset_name=preset["name"], request={"prompt": "checkpoint", "n": 1, "_provider_snapshot": snapshot})
    claimed = image_jobs_repo.claim_next_image_job_unit(worker_id="worker-a", claim_token="token-a", lease_expires_at="2099-01-01T00:00:00+00:00", now="2026-01-01T00:00:01+00:00", running_limit=4, max_attempts=2)
    assert "recovery-secret" not in json.dumps(claimed["request"])
    preset["provider_config"] = V2_CONFIG
    if origin_changed:
        preset["api_url"] = "https://another.example.com"
    runtime_state.state.api_presets = [preset]
    presets_service.persist_api_settings()
    calls = []

    async def captured(*args, **kwargs):
        calls.append(kwargs["provider_config"])
        raise job_executor.proxy.UpstreamApiError("intentional fixture stop")

    monkeypatch.setattr(job_executor.proxy, "call_image_generation_api", captured)
    asyncio.run(job_executor.run_claimed_image_unit(claimed, "worker-a"))
    row = image_jobs_repo.get_image_job_unit(claimed["unit_id"])
    assert row["status"] == "upstream_error"
    if origin_changed:
        assert calls == []
        assert "address changed" in row["error"]
    else:
        assert calls == [V1_CONFIG]


def test_reclaimed_async_unit_without_checkpoint_is_interrupted(tmp_path, monkeypatch):
    _configure_runtime(tmp_path)
    _install_async_preset(monkeypatch, provider_config=V1_CONFIG)
    _enqueue(f"unknown-{uuid.uuid4().hex}", preset_id="recovery-preset", preset_name="recovery")
    first = image_jobs_repo.claim_next_image_job_unit(
        worker_id="worker-a",
        claim_token="token-a",
        lease_expires_at="2026-01-01T00:00:01+00:00",
        now="2026-01-01T00:00:00+00:00",
        running_limit=4,
        max_attempts=4,
    )
    unit_id = str(first["unit_id"])
    _mark_running_with_expired_lease(unit_id, attempts=1, claim_token="token-a")
    second = image_jobs_repo.claim_next_image_job_unit(
        worker_id="worker-b",
        claim_token="token-b",
        lease_expires_at="2099-01-01T00:00:00+00:00",
        now="2026-01-01T00:00:02+00:00",
        running_limit=4,
        max_attempts=4,
    )
    assert second is not None and second["attempts"] == 2

    called = []

    async def fail_if_called(*_args, **_kwargs):
        called.append(True)
        raise AssertionError("unknown submit must not call upstream")

    monkeypatch.setattr(job_executor.proxy, "call_image_generation_api", fail_if_called)
    asyncio.run(job_executor.run_claimed_image_unit(second, "worker-b"))

    row = image_jobs_repo.get_image_job_unit(unit_id)
    assert row["status"] == "interrupted"
    assert row["diagnostics"]["code"] == "submit_unknown"
    assert called == []


def test_executor_replays_unknown_submit_with_persisted_idempotency_key(tmp_path, monkeypatch, provider_session):
    _configure_runtime(tmp_path)
    _install_async_preset(monkeypatch, provider_config=V2_B64_CONFIG)
    _enqueue(f"same-key-{uuid.uuid4().hex}", preset_id="recovery-preset", preset_name="recovery")
    claimed = image_jobs_repo.claim_next_image_job_unit(
        worker_id="worker-a", claim_token="token-a", lease_expires_at="2099-01-01T00:00:00+00:00",
        now="2026-01-01T00:00:01+00:00", running_limit=4, max_attempts=2,
    )
    image_jobs_repo.write_image_job_unit_remote(
        claimed["unit_id"], claim_token="token-a", checkpointed=True,
        remote={"phase": "submitting", "api_url": API_URL, "provider_config": V2_B64_CONFIG,
                "idempotency_key": "original-paid-request-key"},
    )
    resumed = image_jobs_repo.get_image_job_unit(claimed["unit_id"])
    monkeypatch.setattr(job_executor.proxy, "call_image_generation_api", ORIGINAL_CALL_IMAGE_GENERATION_API)
    session = _use_session(monkeypatch, _ScriptedSession({
        ("POST", f"{API_URL}/submit"): [_json({"id": "job-1"})],
        ("GET", V2_STATUS_URL): [_json({"state": "done"})],
        ("GET", V2_RESULT_URL): [_json({"images": [{"b64": base64.b64encode(PNG_BYTES).decode("ascii")}]})],
    }))
    asyncio.run(job_executor.run_claimed_image_unit(resumed, "worker-a"))
    assert image_jobs_repo.get_image_job_unit(claimed["unit_id"])["status"] == "success"
    assert session.calls[0][2]["headers"]["Idempotency-Key"] == "original-paid-request-key"


def test_executor_redacts_resolved_preset_key_from_errors_and_logs(tmp_path, monkeypatch, caplog):
    _configure_runtime(tmp_path)
    _install_async_preset(monkeypatch, provider_config=V1_CONFIG)
    _enqueue(f"error-secret-{uuid.uuid4().hex}", preset_id="recovery-preset", preset_name="recovery")
    claimed = image_jobs_repo.claim_next_image_job_unit(
        worker_id="worker-a", claim_token="token-a", lease_expires_at="2099-01-01T00:00:00+00:00",
        now="2026-01-01T00:00:01+00:00", running_limit=4, max_attempts=2,
    )

    async def rejected(*args, **kwargs):
        raise job_executor.proxy.UpstreamApiError("The submitted recovery-secret was rejected")

    monkeypatch.setattr(job_executor.proxy, "call_image_generation_api", rejected)
    asyncio.run(job_executor.run_claimed_image_unit(claimed, "worker-a"))
    unit = image_jobs_repo.get_image_job_unit(claimed["unit_id"])
    assert unit["status"] == "upstream_error"
    assert "recovery-secret" not in unit["error"]
    assert "[REDACTED]" in unit["error"]
    assert "recovery-secret" not in caplog.text
    assert "UpstreamApiError" in caplog.text


def test_worker_killed_after_submit_resumes_without_second_submit(tmp_path, monkeypatch):
    """The headline recovery contract: one upstream submit across a takeover."""
    _configure_runtime(tmp_path)
    _install_async_preset(monkeypatch, provider_config=V2_B64_CONFIG)
    _enqueue(f"takeover-{uuid.uuid4().hex}", preset_id="recovery-preset", preset_name="recovery")
    first_claim = image_jobs_repo.claim_next_image_job_unit(
        worker_id="worker-a",
        claim_token="token-a",
        lease_expires_at="2099-01-01T00:00:00+00:00",
        now="2026-01-01T00:00:01+00:00",
        running_limit=4,
        max_attempts=2,
    )
    unit_id = str(first_claim["unit_id"])
    # The autouse fixture replaces the generation entry point; run the real one.
    monkeypatch.setattr(
        job_executor.proxy,
        "call_image_generation_api",
        ORIGINAL_CALL_IMAGE_GENERATION_API,
    )

    session_a = _ScriptedSession(
        {
            ("POST", f"{API_URL}/submit"): [_json({"id": "job-e2e"})],
            # Simulate the worker being killed while polling.
            ("GET", f"{API_URL}/jobs/job-e2e"): [asyncio.CancelledError()],
        }
    )
    _use_session(monkeypatch, session_a)
    asyncio.run(job_executor.run_claimed_image_unit(first_claim, "worker-a"))

    checkpointed = image_jobs_repo.get_image_job_unit(unit_id)
    assert checkpointed["status"] == "running"
    assert checkpointed["remote"]["phase"] == "submitted"
    assert [method for method, _url, _kwargs in session_a.calls].count("POST") == 1

    # A different worker reclaims the expired lease and continues the poll.
    _mark_running_with_expired_lease(unit_id, attempts=1, claim_token="token-a")
    reclaimed = image_jobs_repo.claim_next_image_job_unit(
        worker_id="worker-b",
        claim_token="token-b",
        lease_expires_at="2099-01-01T00:00:00+00:00",
        now="2026-01-01T00:00:02+00:00",
        running_limit=4,
        max_attempts=2,
    )
    assert reclaimed is not None
    assert reclaimed["remote"]["phase"] == "submitted"

    session_b = _ScriptedSession(
        {
            ("GET", f"{API_URL}/jobs/job-e2e"): [_json({"state": "done"})],
            ("GET", f"{API_URL}/jobs/job-e2e/result"): [
                _json({"images": [{"b64": base64.b64encode(PNG_BYTES).decode("ascii")}]})
            ],
        }
    )
    _use_session(monkeypatch, session_b)
    asyncio.run(job_executor.run_claimed_image_unit(reclaimed, "worker-b"))

    row = image_jobs_repo.get_image_job_unit(unit_id)
    assert row["status"] == "success"
    assert row["result"]["images"]
    assert [method for method, _url, _kwargs in session_b.calls].count("POST") == 0
    with db_repo._connect() as conn:
        gallery_count = conn.execute(
            "SELECT COUNT(*) FROM gallery_entries WHERE prompt = 'checkpoint'"
        ).fetchone()[0]
    assert gallery_count == 1


# ── stable result identity ───────────────────────────────────────────────────


def test_stable_image_id_is_deterministic_per_unit_and_index():
    assert media.generate_stable_image_id("unit-1", 0) == media.generate_stable_image_id(
        "unit-1", 0
    )
    assert media.generate_stable_image_id("unit-1", 0) != media.generate_stable_image_id(
        "unit-1", 1
    )
    assert media.generate_stable_image_id("unit-1", 0) != media.generate_stable_image_id(
        "unit-2", 0
    )


def test_replayed_result_save_upserts_the_same_gallery_row(client):
    payload = GenerateRequest(prompt="stable-idempotent", model=MODEL, size="1024x1024")

    async def save_once():
        # extract_image_bytes pops the b64 payload, so each replay needs its own copy.
        data = [{"b64_json": base64.b64encode(PNG_BYTES).decode("ascii")}]
        with media.use_image_id_factory(
            lambda index: media.generate_stable_image_id("unit-stable", index)
        ):
            return await image_jobs_repo_save(payload, data)

    first = asyncio.run(save_once())
    second = asyncio.run(save_once())
    assert first[0].id == second[0].id
    with db_repo._connect() as conn:
        count = conn.execute(
            "SELECT COUNT(*) FROM gallery_entries WHERE prompt = ?",
            ("stable-idempotent",),
        ).fetchone()[0]
    assert count == 1


async def image_jobs_repo_save(payload, data):
    from backend.app.integrations.upstream.generation import (
        save_gallery_entries_from_upstream_data,
    )

    return await save_gallery_entries_from_upstream_data(
        download_session=None,
        data=data,
        response_preview="",
        payload=payload,
        format_extension="png",
        gallery_metadata={},
        save_message="Saving generated images",
        progress=None,
        persist_gallery_entry=gallery_mutations.add_to_gallery_async,
    )
