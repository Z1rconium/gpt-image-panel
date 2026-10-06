"""Image settings stay frozen across an Agent turn; credentials stay live."""

import json
from types import SimpleNamespace

import pytest

from backend.app.core.errors import UnprocessableRequestError
from backend.app.schemas.agent import AgentTurnRequest
from backend.app.schemas.generation import EditRequest, GenerateRequest
from backend.app.services import agent_conversations, job_queue
from backend.app.services.image_preset_snapshot import (
    apply_image_preset_snapshot,
    image_preset_snapshot,
)


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def preset():
    return {
        "id": "pinned", "name": "Accepted preset",
        "api_url": "https://images.example.com/old", "api_path": "/v1/responses",
        "default_model": "gpt-image-2", "default_response_format": "b64_json",
        "provider_kind": "openai", "provider_config": {"nested": {"path": "/old"}},
        "prompt_guard": True, "supports_mask": True, "api_key": "original-secret",
    }


@pytest.mark.anyio
async def test_acceptance_deep_copies_image_configuration_without_credentials(monkeypatch, preset):
    async def read(*args, **kwargs):
        return None

    monkeypatch.setattr(agent_conversations, "run_db_operation", read)
    monkeypatch.setattr(agent_conversations.presets, "get_active_preset", lambda: preset)
    monkeypatch.setattr(agent_conversations.assistant_runtime, "agent_execution_snapshot", lambda agent: {})
    snapshot = await agent_conversations._execution_snapshot(
        SimpleNamespace(web_search_enabled=False), AgentTurnRequest(text="draw", client_turn_id="request-1"),
    )
    preset["provider_config"]["nested"]["path"] = "/edited"
    preset["default_model"] = "changed-model"
    assert snapshot["image"]["preset"]["provider_config"]["nested"]["path"] == "/old"
    assert snapshot["image"]["preset"]["default_model"] == "gpt-image-2"
    assert "original-secret" not in json.dumps(snapshot)
    assert snapshot["capabilities"] == {"image_tools": True}


@pytest.mark.anyio
@pytest.mark.parametrize("operation", ["generation", "edit"])
async def test_queue_uses_full_accepted_configuration_and_live_key(monkeypatch, preset, operation):
    frozen = image_preset_snapshot(preset)
    preset.update({
        "name": "Changed preset", "api_url": "https://images.example.com/new",
        "api_path": "/v1/images/generations", "default_model": "different-model",
        "provider_kind": "async_json", "provider_config": {}, "prompt_guard": False,
        "supports_mask": False, "api_key": "rotated-secret", "default_response_format": "url",
    })
    stored, resolved = {}, []

    async def db(callback, *args, **kwargs):
        if callback is job_queue.enqueue_image_job:
            stored.update(kwargs)
            return kwargs["parent_job"], []
        assert callback is job_queue.load_api_settings

    monkeypatch.setattr(job_queue, "state", SimpleNamespace(generate_job_last_persist_at={}))
    monkeypatch.setattr(job_queue, "run_db_operation", db)
    monkeypatch.setattr(job_queue, "get_active_preset", lambda: preset)
    monkeypatch.setattr(job_queue, "get_api_presets", lambda: [preset])
    monkeypatch.setattr(job_queue, "get_effective_preset_api_key", lambda p: resolved.append(p["api_key"]) or p["api_key"])
    monkeypatch.setattr(job_queue, "get_upstream_socks5_proxy", lambda: None)
    monkeypatch.setattr(job_queue, "get_webhook_url", lambda: None)
    monkeypatch.setattr(job_queue, "remember_generate_job_memory", lambda *args: None)
    monkeypatch.setattr(job_queue, "publish_generate_job", lambda *args, **kwargs: None)
    monkeypatch.setattr(job_queue, "kick_image_unit_dispatcher", lambda: None)
    req = (EditRequest if operation == "edit" else GenerateRequest)(prompt="draw", api_preset_id="pinned")
    if operation == "edit":
        await job_queue.queue_edit_job(req, [], preset_snapshot=frozen)
    else:
        await job_queue.queue_image_job(
            req=req, operation="generation", api_path=lambda p: p["api_path"],
            queued_message="Queued", preset_snapshot=frozen,
        )
    assert stored["api_path"] == ("/v1/images/edits" if operation == "edit" else "/v1/responses")
    assert stored["api_preset_name"] == "Accepted preset"
    assert stored["request"]["model"] == "gpt-image-2"
    assert stored["request"]["response_format"] == "b64_json"
    assert stored["request"]["_provider_snapshot"] == {
        "api_url": "https://images.example.com/old", "provider_kind": "openai",
        "provider_config": {"nested": {"path": "/old"}}, "prompt_guard": True, "supports_mask": True,
    }
    assert resolved == ["rotated-secret"]
    assert "secret" not in json.dumps(stored)


@pytest.mark.parametrize("change", [
    {"id": "other"}, {"api_url": "https://other.example.com"},
    {"api_url": "http://images.example.com/old"}, {"api_url": "https://images.example.com:8443/old"},
])
def test_different_preset_or_origin_is_rejected(preset, change):
    frozen = image_preset_snapshot(preset)
    with pytest.raises(UnprocessableRequestError):
        apply_image_preset_snapshot({**preset, **change}, frozen)


@pytest.mark.anyio
async def test_older_preset_id_only_turn_fails_before_queueing(monkeypatch):
    from backend.app.services.agent_tool_executor import AgentToolExecutor
    from backend.app.services.agent_tools import BatchImage

    async def check_cancel():
        pass

    run = SimpleNamespace(
        turn={"execution_snapshot": {"image": {"preset_id": "pinned"}}},
        check_cancel=check_cancel,
    )
    executor = AgentToolExecutor(run)
    failures = []

    async def fail(image, row, block, message):
        failures.append(message)
        return {"status": "error"}, None

    async def unexpected_queue(**kwargs):
        pytest.fail("Older ID-only snapshots must not queue paid work")

    monkeypatch.setattr(executor, "_fail_image", fail)
    monkeypatch.setattr(job_queue, "queue_image_job", unexpected_queue)
    result, created = await executor._run_image(BatchImage("one", "draw"), {}, [], {})
    assert result["status"] == "error" and created is None
    assert "Start a new turn" in failures[0]


@pytest.mark.anyio
@pytest.mark.parametrize("live_url, allowed", [
    ("https://images.example.com/new", True), ("https://different.example.com/old", False),
])
async def test_worker_keeps_queued_endpoint_and_rotated_credentials(monkeypatch, preset, live_url, allowed):
    from backend.app.services import image_unit_execution as execution

    frozen = image_preset_snapshot(preset)
    live = {**preset, "api_url": live_url, "api_key": "rotated-secret"}

    async def db(callback, *args, **kwargs):
        if callback is execution.get_generate_job:
            return {"status": "queued"}
        assert callback is execution.get_preset_for_unit
        return live

    monkeypatch.setattr(execution, "run_db_operation", db)
    monkeypatch.setattr(execution, "get_effective_preset_api_key", lambda p: p["api_key"])
    monkeypatch.setattr(execution, "get_upstream_socks5_proxy", lambda: None)
    context = execution.ImageUnitExecutionContext({
        "unit_id": "unit", "parent_job_id": "parent", "claim_token": "owner",
        "request": {"prompt": "draw", "_provider_snapshot": frozen},
    }, "worker")
    await context.prepare()
    assert context.snapshot_origin_changed is not allowed
    assert context.api_url == "https://images.example.com/old"
    assert context.api_key == "rotated-secret"


@pytest.mark.anyio
async def test_preparation_failure_terminalizes_claim_instead_of_leaving_it_running(monkeypatch):
    from backend.app.services import image_unit_execution as execution

    writes, aggregates = [], []

    async def db(callback, *args, **kwargs):
        if callback is execution.get_generate_job:
            return {"status": "running"}
        if callback is execution.get_preset_for_unit:
            return None
        assert callback is execution.fail_image_job_unit
        writes.append(kwargs)

    async def aggregate(parent_id, **kwargs):
        aggregates.append(parent_id)

    monkeypatch.setattr(execution, "run_db_operation", db)
    monkeypatch.setattr(execution, "aggregate_parent_image_job", aggregate)
    context = execution.ImageUnitExecutionContext({
        "unit_id": "unit", "parent_job_id": "parent", "claim_token": "owner",
        "request": {"prompt": "draw"},
    }, "worker")
    await context.execute()
    assert writes[0]["status"] == "error" and writes[0]["claim_token"] == "owner"
    assert "API preset not found" in writes[0]["message"]
    assert aggregates == ["parent"]
    assert context.session.upstream_task is None and context.session.lease_task is None
