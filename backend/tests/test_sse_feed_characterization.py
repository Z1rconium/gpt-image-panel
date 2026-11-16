"""Characterization tests for the four SSE feeds through HTTP.

Pins the pre-refactor behavior of:
- jobs-list stream: ``GET /api/generate/jobs/events``
- per-job stream: ``GET /api/generate/{job_id}/events``
- gallery job stream: ``GET /api/gallery/export-jobs/{job_id}/events``
- agent turn stream: ``GET /api/agent/turns/{turn_id}/events``
"""

import json
import types
import uuid

import pytest
from starlette.requests import Request

from backend.app.api.routers import generate as generate_router
from backend.app.core import settings as config
from backend.app.core.utils import utc_now
from backend.app.repositories import agent as agent_repo
from backend.app.repositories import coordination as coordination_repo
from backend.app.repositories import image_jobs as image_jobs_repo
from backend.app.repositories import sse_limiter as sse_limiter_mod
from backend.app.repositories.sse_limiter import sse_limiter
from backend.app.runtime.state import utc_lease_expires_at
from backend.app.services import agent_stream, gallery_job_sse, job_events


class _FakeClock:
    """Deterministic monotonic clock scoped to SSE modules only."""

    def __init__(self, initial: float = 100.0) -> None:
        self.now = initial

    def monotonic(self) -> float:
        return self.now

    def advance(self, seconds: float) -> float:
        self.now += seconds
        return self.now


def _parse_sse_frames(body: str) -> list[dict]:
    frames: list[dict] = []
    for block in body.split("\n\n"):
        if not block.strip():
            continue
        lines = block.splitlines()
        if all(line.startswith(":") for line in lines):
            frames.append({"comment": "\n".join(line[1:].lstrip() for line in lines)})
            continue
        fields: dict[str, str] = {}
        for line in lines:
            if ":" in line:
                key, value = line.split(":", 1)
                fields[key.strip()] = value.lstrip()
        frames.append(
            {
                "id": int(fields["id"]) if "id" in fields else None,
                "event": fields.get("event"),
                "data": json.loads(fields["data"]) if "data" in fields else None,
            }
        )
    return frames


def _patch_sse_runtime(
    monkeypatch,
    *,
    ttl_seconds: float,
    keepalive_seconds: float | None = None,
) -> _FakeClock:
    clock = _FakeClock(100.0)
    time_stub = types.SimpleNamespace(monotonic=clock.monotonic)
    monkeypatch.setattr(config, "SSE_CONNECTION_TTL_SECONDS", ttl_seconds)
    monkeypatch.setattr(generate_router, "SSE_IDLE_CHECK_SECONDS", 0.01)
    monkeypatch.setattr(gallery_job_sse, "GALLERY_JOB_SSE_IDLE_CHECK_SECONDS", 0.01)
    monkeypatch.setattr(agent_stream, "EVENT_POLL_SECONDS", 0.01)
    if keepalive_seconds is not None:
        monkeypatch.setattr(agent_stream, "KEEP_ALIVE_SECONDS", keepalive_seconds)
    for mod in (generate_router, gallery_job_sse, agent_stream, sse_limiter_mod):
        monkeypatch.setattr(mod, "time", time_stub)
    return clock


def _seed_running_generate_job(job_id: str) -> dict:
    now = utc_now()
    image_jobs_repo.upsert_generate_job(
        {
            "job_id": job_id,
            "status": "running",
            "stage": "generating",
            "message": "Generating",
            "n": 1,
            "created_at": now,
            "updated_at": now,
        }
    )
    job = image_jobs_repo.get_generate_job(job_id)
    assert job is not None
    return job


def _seed_running_gallery_export_job(
    job_id: str,
    *,
    updated_at: str | None = None,
) -> dict:
    ts = updated_at or utc_now()
    return coordination_repo.create_gallery_job(
        job_id=job_id,
        kind="export",
        status="running",
        stage="zipping",
        message="Exporting",
        progress=10,
        requested_count=1,
        exported_count=0,
        missing_count=0,
        created_at=ts,
        updated_at=ts,
    )


def _seed_running_agent_turn() -> str:
    conversation = agent_repo.create_conversation("SSE characterization")
    created = agent_repo.create_turn(
        conversation["id"],
        client_turn_id=f"turn-{uuid.uuid4().hex[:8]}",
        text="hello",
        attachment_image_ids=(),
        model="gpt-5.4",
        image_params={"size": "1024x1024", "quality": "auto", "output_format": "png", "n": 1},
        lease_expires_at=utc_lease_expires_at(3600),
    )
    assert created is not None
    turn, _ = created
    return turn["id"]


def _prepare_stream_target(stream_kind: str) -> tuple[str, str]:
    suffix = uuid.uuid4().hex[:8]
    if stream_kind == "jobs_list":
        _seed_running_generate_job(f"gen-list-{suffix}")
        return "/api/generate/jobs/events", "jobs"
    if stream_kind == "per_job":
        job_id = f"gen-job-{suffix}"
        _seed_running_generate_job(job_id)
        return f"/api/generate/{job_id}/events", "job"
    if stream_kind == "gallery":
        job_id = f"exp-job-{suffix}"
        _seed_running_gallery_export_job(job_id)
        return f"/api/gallery/export-jobs/{job_id}/events", "export"
    if stream_kind == "agent":
        turn_id = _seed_running_agent_turn()
        return f"/api/agent/turns/{turn_id}/events", "turn"
    raise AssertionError(f"Unknown stream_kind: {stream_kind}")


STREAM_KINDS = ("jobs_list", "per_job", "gallery", "agent")


@pytest.mark.parametrize("stream_kind", STREAM_KINDS)
def test_sse_keepalive_frame_emitted_and_ttl_closes_stream(
    client, monkeypatch, stream_kind
):
    clock = _patch_sse_runtime(
        monkeypatch,
        ttl_seconds=25.0,
        keepalive_seconds=15.0,
    )
    path, _target_kind = _prepare_stream_target(stream_kind)
    loop_checks = 0

    async def fake_is_disconnected(self) -> bool:
        nonlocal loop_checks
        loop_checks += 1
        if loop_checks == 1:
            clock.advance(16.0)
        else:
            clock.advance(16.0)
        return False

    monkeypatch.setattr(Request, "is_disconnected", fake_is_disconnected)

    response = client.get(path)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["x-accel-buffering"] == "no"
    assert "content-encoding" not in response.headers
    assert ": keep-alive\n\n" in response.text

    frames = _parse_sse_frames(response.text)
    assert {"comment": "keep-alive"} in frames
    assert loop_checks >= 2
    assert sse_limiter.global_count == 0


@pytest.mark.parametrize("stream_kind", STREAM_KINDS)
def test_sse_ttl_closes_idle_stream_before_keepalive(
    client, monkeypatch, stream_kind
):
    clock = _patch_sse_runtime(
        monkeypatch,
        ttl_seconds=0.05,
        keepalive_seconds=15.0,
    )
    path, _target_kind = _prepare_stream_target(stream_kind)
    loop_checks = 0

    async def fake_is_disconnected(self) -> bool:
        nonlocal loop_checks
        loop_checks += 1
        clock.advance(0.1)
        return False

    monkeypatch.setattr(Request, "is_disconnected", fake_is_disconnected)

    response = client.get(path)
    assert response.status_code == 200
    assert ": keep-alive\n\n" not in response.text
    assert loop_checks == 1
    assert sse_limiter.global_count == 0


@pytest.mark.parametrize("stream_kind", STREAM_KINDS)
def test_sse_limiter_refresh_failure_closes_stream(
    client, monkeypatch, stream_kind
):
    clock = _patch_sse_runtime(
        monkeypatch,
        ttl_seconds=15.0,
        keepalive_seconds=15.0,
    )
    path, _target_kind = _prepare_stream_target(stream_kind)
    loop_checks = 0

    monkeypatch.setattr(sse_limiter_mod, "refresh_sse_slot", lambda **_kw: False)

    async def fake_is_disconnected(self) -> bool:
        nonlocal loop_checks
        loop_checks += 1
        # Exceed refresh_interval_seconds (7.5s for 15s TTL) while staying below keep-alive/TTL (15s).
        clock.advance(8.0)
        return False

    monkeypatch.setattr(Request, "is_disconnected", fake_is_disconnected)

    response = client.get(path)
    assert response.status_code == 200
    assert ": keep-alive\n\n" not in response.text
    assert loop_checks == 1
    assert sse_limiter.global_count == 0


def test_sse_jobs_list_dedupes_identical_payload(client, monkeypatch):
    _patch_sse_runtime(monkeypatch, ttl_seconds=60.0)
    job_id = f"gen-dedupe-list-{uuid.uuid4().hex[:8]}"
    job = _seed_running_generate_job(job_id)
    pushed = False

    async def fake_is_disconnected(self) -> bool:
        nonlocal pushed
        if not pushed:
            pushed = True
            # Two identical snapshots should both be deduped against initial frame.
            job_events.publish_generate_jobs(debounce=False, reconcile=False)
            job_events.publish_generate_jobs(debounce=False, reconcile=False)
            updated = {
                **job,
                "message": "Step 2",
                "updated_at": "2099-01-01T00:00:01+00:00",
            }
            image_jobs_repo.upsert_generate_job(updated)
            job_events.remember_generate_job_memory(job_id, updated)
            job_events.publish_generate_jobs(debounce=False, reconcile=False)
            return False
        if any(not q.empty() for q in job_events.get_jobs_subscribers()):
            return False
        return True

    monkeypatch.setattr(Request, "is_disconnected", fake_is_disconnected)

    response = client.get("/api/generate/jobs/events")
    assert response.status_code == 200

    frames = [f for f in _parse_sse_frames(response.text) if f.get("event") == "jobs"]
    assert len(frames) == 2
    assert frames[0]["data"][0]["message"] == "Generating"
    assert frames[1]["data"][0]["message"] == "Step 2"
    assert not job_events.get_jobs_subscribers()
    assert sse_limiter.global_count == 0


def test_sse_per_job_dedupes_identical_payload(client, monkeypatch):
    _patch_sse_runtime(monkeypatch, ttl_seconds=60.0)
    job_id = f"gen-dedupe-job-{uuid.uuid4().hex[:8]}"
    job = _seed_running_generate_job(job_id)
    pushed = False

    async def fake_is_disconnected(self) -> bool:
        nonlocal pushed
        if not pushed:
            pushed = True
            # Same payload (and reversed key order) must be deduped by canonical JSON.
            job_events.publish_generate_job(dict(job), list_debounce=False)
            reordered = {k: job[k] for k in reversed(list(job.keys()))}
            job_events.publish_generate_job(reordered, list_debounce=False)
            terminal = {
                **job,
                "status": "success",
                "stage": "completed",
                "message": "Done",
                "updated_at": "2099-01-01T00:00:01+00:00",
            }
            image_jobs_repo.upsert_generate_job(terminal)
            job_events.publish_generate_job(terminal, list_debounce=False)
            return False
        return False

    monkeypatch.setattr(Request, "is_disconnected", fake_is_disconnected)

    response = client.get(f"/api/generate/{job_id}/events")
    assert response.status_code == 200

    frames = [f for f in _parse_sse_frames(response.text) if f.get("event") == "job"]
    assert len(frames) == 2
    assert frames[0]["data"]["status"] == "running"
    assert frames[1]["data"]["status"] == "success"
    assert job_id not in job_events.get_job_subscribers()
    assert sse_limiter.global_count == 0


def test_sse_gallery_dedupes_identical_updated_at(client, monkeypatch):
    _patch_sse_runtime(monkeypatch, ttl_seconds=60.0)
    job_id = f"exp-dedupe-{uuid.uuid4().hex[:8]}"
    t0 = "2026-11-16T10:00:00+00:00"
    t1 = "2026-11-16T10:00:05+00:00"
    job = _seed_running_gallery_export_job(job_id, updated_at=t0)
    pushed = False

    async def fake_is_disconnected(self) -> bool:
        nonlocal pushed
        if not pushed:
            pushed = True
            # Gallery dedupes strictly by updated_at equality.
            gallery_job_sse._publish_gallery_job_sse(dict(job))
            gallery_job_sse._publish_gallery_job_sse({**job, "progress": 50})
            terminal = {
                **job,
                "status": "success",
                "stage": "completed",
                "message": "Exported",
                "progress": 100,
                "updated_at": t1,
            }
            coordination_repo.update_gallery_job(
                job_id,
                {
                    "status": "success",
                    "stage": "completed",
                    "message": "Exported",
                    "progress": 100,
                    "updated_at": t1,
                },
            )
            gallery_job_sse._publish_gallery_job_sse(terminal)
            return False
        return False

    monkeypatch.setattr(Request, "is_disconnected", fake_is_disconnected)

    response = client.get(f"/api/gallery/export-jobs/{job_id}/events")
    assert response.status_code == 200

    frames = [f for f in _parse_sse_frames(response.text) if f.get("event") == "export"]
    assert len(frames) == 2
    assert frames[0]["data"]["status"] == "running"
    assert frames[1]["data"]["status"] == "success"
    assert frames[1]["data"]["updated_at"] == t1
    assert job_id not in gallery_job_sse._get_gallery_job_subscribers("export")
    assert sse_limiter.global_count == 0


@pytest.mark.parametrize("stream_kind", STREAM_KINDS)
def test_sse_client_disconnect_releases_slot_and_subscriber(
    client, monkeypatch, stream_kind
):
    _patch_sse_runtime(monkeypatch, ttl_seconds=60.0)
    path, _target_kind = _prepare_stream_target(stream_kind)
    observed_while_open: dict[str, int] = {}

    async def fake_is_disconnected(self) -> bool:
        observed_while_open["global_slots"] = sse_limiter.global_count
        if stream_kind == "jobs_list":
            observed_while_open["subscribers"] = len(job_events.get_jobs_subscribers())
        elif stream_kind == "per_job":
            job_id = path.split("/")[3]
            observed_while_open["subscribers"] = len(
                job_events.get_job_subscribers().get(job_id, set())
            )
        elif stream_kind == "gallery":
            job_id = path.split("/")[4]
            observed_while_open["subscribers"] = len(
                gallery_job_sse._get_gallery_job_subscribers("export").get(job_id, set())
            )
        return True

    monkeypatch.setattr(Request, "is_disconnected", fake_is_disconnected)

    response = client.get(path)
    assert response.status_code == 200
    assert observed_while_open["global_slots"] == 1
    if stream_kind != "agent":
        assert observed_while_open["subscribers"] == 1

    assert sse_limiter.global_count == 0
    assert not job_events.get_jobs_subscribers()
    assert not job_events.get_job_subscribers()
    assert not gallery_job_sse._get_gallery_job_subscribers("export")


def test_sse_slots_exhausted_returns_429_and_body_parity(client, monkeypatch):
    monkeypatch.setattr(config, "MAX_SSE_SUBSCRIBERS_GLOBAL", 1)
    monkeypatch.setattr(config, "MAX_SSE_SUBSCRIBERS_PER_IP", 10)

    gen_job_id = f"gen-429-{uuid.uuid4().hex[:8]}"
    exp_job_id = f"exp-429-{uuid.uuid4().hex[:8]}"
    _seed_running_generate_job(gen_job_id)
    _seed_running_gallery_export_job(exp_job_id)
    turn_id = _seed_running_agent_turn()

    conn_id = f"held-{uuid.uuid4().hex[:8]}"
    acquired, _reason = coordination_repo.acquire_sse_slot(
        client_ip="198.51.100.10",
        connection_id=conn_id,
        lease_expires_at=utc_lease_expires_at(60),
        max_global=1,
        max_per_ip=10,
    )
    assert acquired is True

    try:
        # HTTPException(429) in generate.py vs RateLimitedError(429) in gallery/agent services.
        endpoints = {
            "jobs_list_http_exc": "/api/generate/jobs/events",
            "per_job_http_exc": f"/api/generate/{gen_job_id}/events",
            "gallery_domain_err": f"/api/gallery/export-jobs/{exp_job_id}/events",
            "agent_domain_err": f"/api/agent/turns/{turn_id}/events",
        }
        envelopes: dict[str, dict] = {}
        for name, path in endpoints.items():
            resp = client.get(path)
            assert resp.status_code == 429, f"{name}: {resp.status_code} {resp.text}"
            assert resp.headers["content-type"].startswith("application/json")
            assert resp.headers["cache-control"] == "private, no-store"
            body = resp.json()
            assert body["correlation_id"] == resp.headers["x-correlation-id"]
            assert body["status"] == "error"
            assert body["error_code"] == "rate_limited"
            assert body["detail"] == "Too many SSE connections"
            assert body["message"] == "Too many SSE connections"
            assert body["error"] == "Too many SSE connections"
            envelopes[name] = {k: v for k, v in body.items() if k != "correlation_id"}

        baseline = envelopes["jobs_list_http_exc"]
        for name, envelope in envelopes.items():
            assert envelope == baseline, f"429 envelope mismatch for {name}: {envelope}"
            assert set(envelope.keys()) == {"status", "error_code", "detail", "message", "error"}
    finally:
        coordination_repo.release_sse_slot(conn_id)
