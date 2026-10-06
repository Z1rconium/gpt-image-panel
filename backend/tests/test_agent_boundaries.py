"""Admission, slot deadlines, snapshots and execution-lease regressions."""

import asyncio
import json
import time
from types import SimpleNamespace

import pytest

from backend.app.core import settings as config
from backend.app.core.errors import DomainError
from backend.app.repositories import agent as repo
from backend.app.runtime.state import state
from backend.app.schemas.agent import AgentTurnRequest
from backend.app.services import agent_conversations as conversations, agent_tool_executor as executor, agent_turns as turns, assistant_runtime as assistant
from backend.app.services import agent_run_context as context, agent_run_lifecycle as lifecycle
from backend.app.services.agent_tools import BatchImage


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
def reset_runtime():
    state.agent_turn_tasks = {}
    state.agent_turn_reservations = 0
    state.agent_turn_admissions = set()
    state.assistant_slot_cleanup_tasks = set()
    state.agent_turn_wakeups = {}
    state.agent_turn_cancel_events = {}


def turn_row():
    return {"id": "turn", "conversation_id": "conversation", "round_no": 1, "status": "queued",
            "user_message_id": "user", "assistant_message_id": "assistant", "model": "model"}


@pytest.mark.anyio
async def test_batch_reuses_execution_path_and_rejects_another_branch(monkeypatch):
    run = context.AgentRunContext(turn_row())
    path_reads = []
    planned = []
    refs = {
        "round-1-image-1": {"turn_id": "ancestor", "status": "succeeded", "image_id": "image-a", "ref_label": "round-1-image-1"},
        "round-2-image-1": {"turn_id": "sibling", "status": "succeeded", "image_id": "image-b", "ref_label": "round-2-image-1"},
    }

    async def read(callback, *args, **kwargs):
        if callback is repo.path_turn_ids:
            path_reads.append(args)
            return ["ancestor", "turn"]
        if callback is repo.get_image_by_label:
            return refs[args[1]]
        if callback is repo.insert_pending_output_image:
            planned.append(kwargs["item_id"])
            return {"ref_label": f"round-3-image-{len(planned)}", "round_no": 3, "image_index": len(planned)}
        raise AssertionError(callback)

    async def upsert(block):
        pass

    async def execute(image, row, ref_rows, block):
        return {"id": image.id, "status": "created"}, None

    monkeypatch.setattr(run, "_db", read)
    monkeypatch.setattr(run.events, "upsert_block", upsert)
    monkeypatch.setattr(run.tools, "_run_image", execute)
    output, _ = await run.tools._run_batch("call", [
        BatchImage("one", '<ref id="round-1-image-1"/> edit one'),
        BatchImage("two", '<ref id="round-1-image-1"/> edit two'),
        BatchImage("other", '<ref id="round-2-image-1"/> inaccessible'),
    ])
    results = json.loads(output)
    assert [result["status"] for result in results] == ["created", "created", "error"]
    assert planned == ["one", "two"]
    assert path_reads == [("conversation", "turn")]


@pytest.mark.anyio
async def test_eight_simultaneous_admissions_reserve_single_slot_and_replay(monkeypatch):
    monkeypatch.setattr(config, "AGENT_MAX_ACTIVE_TURNS", 1)
    entered, release = asyncio.Event(), asyncio.Event()
    calls = []
    created = {}

    async def create(conversation_id, req):
        entered.set()
        await release.wait()
        calls.append(conversation_id)
        row = turn_row()
        created[req.client_turn_id] = row
        state.agent_turn_tasks[row["id"]] = object()
        return conversations._accepted(row, True)

    async def lookup(callback, conversation_id, client_id, **kwargs):
        assert callback is repo.get_turn_by_client_id
        return created.get(client_id)

    monkeypatch.setattr(conversations, "_create_reserved_turn", create)
    monkeypatch.setattr(conversations, "run_db_operation", lookup)
    requests = [AgentTurnRequest(client_turn_id=f"request-{i}", text="hello") for i in range(8)]
    first = asyncio.create_task(conversations.start_turn("conversation-0", requests[0]))
    await entered.wait()
    others = await asyncio.gather(*(conversations.start_turn(f"conversation-{i}", req) for i, req in enumerate(requests[1:], 1)), return_exceptions=True)
    assert all(isinstance(error, DomainError) and error.status_code == 429 for error in others)
    assert state.agent_turn_reservations == 1
    release.set()
    accepted = await first
    assert len(calls) == 1 and state.agent_turn_reservations == 0
    replay = await conversations.start_turn("conversation-0", requests[0])
    assert replay.replayed and replay.turn_id == accepted.turn_id


@pytest.mark.anyio
async def test_cancel_during_creation_finishes_admission_and_releases_reservation(monkeypatch):
    entered, release = asyncio.Event(), asyncio.Event()
    started = []

    async def create(conversation_id, req):
        entered.set()
        await release.wait()
        started.append(turn_row()["id"])
        return conversations._accepted(turn_row(), True)

    monkeypatch.setattr(conversations, "_create_reserved_turn", create)
    request = AgentTurnRequest(client_turn_id="request-one", text="hi")
    task = asyncio.create_task(conversations.start_turn("conversation", request))
    await entered.wait()
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done() and state.agent_turn_reservations == 1
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert started == ["turn"] and state.agent_turn_reservations == 0


@pytest.mark.anyio
async def test_slot_deadline_includes_local_semaphore(monkeypatch):
    semaphore = asyncio.Semaphore(1)
    await semaphore.acquire()
    monkeypatch.setattr(assistant, "_assistant_request_semaphore", lambda: semaphore)
    started = time.monotonic()
    with pytest.raises(DomainError) as error:
        async with assistant._assistant_request_limit(30, wait_for_slot=True, max_wait_seconds=0.03):
            pytest.fail("must not enter saturated slot")
    assert error.value.status_code == 429
    assert time.monotonic() - started < 0.5
    semaphore.release()


@pytest.mark.anyio
@pytest.mark.parametrize("cancel", [False, True])
async def test_late_database_slot_is_released_after_timeout_or_cancel(monkeypatch, cancel):
    semaphore = asyncio.Semaphore(1)
    entered, release = asyncio.Event(), asyncio.Event()
    released = []

    async def acquire(timeout):
        entered.set()
        await release.wait()
        return "slot", "owner"

    async def free(*slot):
        released.append(slot)

    monkeypatch.setattr(assistant, "_assistant_request_semaphore", lambda: semaphore)
    monkeypatch.setattr(assistant, "_acquire_assistant_slot", acquire)
    monkeypatch.setattr(assistant, "_release_assistant_slot", free)

    async def request():
        async with assistant._assistant_request_limit(30, wait_for_slot=True, max_wait_seconds=0.03):
            pytest.fail("must not enter a late slot")

    task = asyncio.create_task(request())
    await entered.wait()
    if cancel:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    else:
        with pytest.raises(DomainError) as error:
            await task
        assert error.value.status_code == 429
    assert not semaphore.locked()
    release.set()
    for _ in range(10):
        await asyncio.sleep(0)
        if released:
            break
    assert released == [("slot", "owner")]


@pytest.mark.anyio
async def test_slot_deadline_does_not_limit_model_request(monkeypatch):
    semaphore = asyncio.Semaphore(1)
    released = []

    async def acquire(timeout):
        return "slot", "owner"

    async def free(*slot):
        released.append(slot)

    monkeypatch.setattr(assistant, "_assistant_request_semaphore", lambda: semaphore)
    monkeypatch.setattr(assistant, "_acquire_assistant_slot", acquire)
    monkeypatch.setattr(assistant, "_release_assistant_slot", free)
    async with assistant._assistant_request_limit(30, wait_for_slot=True, max_wait_seconds=0.01):
        await asyncio.sleep(0.04)
    assert released == [("slot", "owner")] and not semaphore.locked()


@pytest.mark.anyio
async def test_text_pause_is_dirty_and_failed_snapshot_retries(monkeypatch):
    run = context.AgentRunContext(turn_row())
    saved = []
    fail = False

    async def write(callback, *args, **kwargs):
        nonlocal fail
        if callback is repo.update_message_content:
            if fail:
                fail = False
                raise RuntimeError("transient write failure")
            saved.append(kwargs["text"])

    monkeypatch.setattr(context, "run_db_operation", write)
    await run.events.add_text("hello")
    run.events._last_persist = 0
    fail = True
    with pytest.raises(RuntimeError):
        await run.events.maybe_persist()
    assert run.events._saved_revision != run.events._revision
    await run.events.maybe_persist()
    assert saved[-1] == "hello"
    assert run.events._saved_revision == run.events._revision
    await run.events.add_text(" world")
    run.events._last_persist = 0
    await run.events.maybe_persist()
    assert saved[-1] == "hello world"


@pytest.mark.anyio
async def test_modification_during_snapshot_stays_dirty(monkeypatch):
    run = context.AgentRunContext(turn_row())
    entered, release = asyncio.Event(), asyncio.Event()
    snapshots = []

    async def write(callback, *args, **kwargs):
        snapshots.append(kwargs)
        entered.set()
        await release.wait()

    monkeypatch.setattr(context, "run_db_operation", write)
    run.events._last_persist = time.monotonic()
    await run.events.add_text("first")
    pending = asyncio.create_task(run.events.persist())
    await entered.wait()
    await run.events.add_text(" second")
    release.set()
    await pending
    assert snapshots[0]["text"] == "first"
    assert snapshots[0]["blocks"][0]["text"] == "first"
    assert run.events._saved_revision != run.events._revision
    run.events._last_persist = 0
    await run.events.maybe_persist()
    assert snapshots[-1]["text"] == "first second"
    assert run.events._saved_revision == run.events._revision


@pytest.mark.anyio
@pytest.mark.parametrize("failure", ["lost", "errors", "hung"])
async def test_lease_loss_stops_main_and_suppresses_business_events(monkeypatch, failure):
    run = context.AgentRunContext(turn_row())
    monkeypatch.setattr(config, "AGENT_TURN_LEASE_SECONDS", 1)

    async def renew(*args, **kwargs):
        if failure == "errors":
            raise RuntimeError("database unavailable")
        if failure == "hung":
            await asyncio.sleep(10)
        return False

    monkeypatch.setattr(context, "run_db_operation", renew)
    main = asyncio.create_task(asyncio.sleep(10))
    renewal = asyncio.create_task(lifecycle._renew_lease_loop(run, "owner", main))
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(main, 3)
    await renewal
    assert run._lease_lost and not run._cancelled
    await run.events.emit("block.upsert", {"block": {"type": "text"}})
    assert run.events._pending_events == []


@pytest.mark.anyio
async def test_cancelled_queue_cleans_late_created_job(monkeypatch):
    run = context.AgentRunContext(turn_row())
    entered, release = asyncio.Event(), asyncio.Event()
    cancelled = []

    async def queue():
        entered.set()
        await release.wait()
        return SimpleNamespace(job_id="late-job")

    async def cancel(job_id):
        cancelled.append(job_id)

    monkeypatch.setattr(executor.job_cancel, "cancel_image_job", cancel)
    task = asyncio.create_task(run.tools.queue_owned(queue()))
    await entered.wait()
    run._lease_lost = True
    task.cancel()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert cancelled == ["late-job"]


@pytest.mark.anyio
async def test_final_write_failure_still_cleans_created_jobs_and_finishes(monkeypatch):
    run = context.AgentRunContext(turn_row())
    run.tools.queued_jobs.add("queued-job")
    cancelled, finished = [], []

    async def view(job_id):
        return {"status": "queued"}

    async def cancel(job_id):
        cancelled.append(job_id)

    async def write(callback, *args, **kwargs):
        if callback is repo.list_pending_images_for_turn:
            return []
        if callback is repo.update_message_content:
            raise RuntimeError("snapshot unavailable")
        if callback is repo.finish_turn:
            finished.append(args[1])
            return {"status": args[1]}
        if callback is repo.read_event_cursor:
            return 0
        if callback is repo.append_turn_events:
            return kwargs["start_seq"] + len(args[1]) - 1

    monkeypatch.setattr(executor, "resolve_generate_job_view", view)
    monkeypatch.setattr(executor.job_cancel, "cancel_image_job", cancel)
    monkeypatch.setattr(context, "run_db_operation", write)
    monkeypatch.setattr(executor, "run_db_operation", write)
    await run.lifecycle.finalize("failed", "write failed")
    assert cancelled == ["queued-job"] and finished == ["failed"]


@pytest.mark.anyio
async def test_cancelled_edit_source_collects_and_deletes_late_temp(monkeypatch, tmp_path):
    from backend.app.services import edit_sources

    entered, release = asyncio.Event(), asyncio.Event()
    path = tmp_path / "late-edit.png"

    async def get_entry(*args, **kwargs):
        return SimpleNamespace(filename="original.png")

    async def copy(*args, **kwargs):
        entered.set()
        await release.wait()
        path.write_bytes(b"temporary source")
        return SimpleNamespace(temp_path=path)

    original = tmp_path / "original.png"
    original.write_bytes(b"original")
    monkeypatch.setattr(edit_sources, "run_db_operation", get_entry)
    monkeypatch.setattr(edit_sources, "safe_image_path", lambda _: original)
    monkeypatch.setattr(edit_sources, "run_image_operation", copy)
    task = asyncio.create_task(edit_sources.read_gallery_edit_source("original"))
    await entered.wait()
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not path.exists()


@pytest.mark.anyio
async def test_shutdown_drains_agent_finalization_before_closing_executors(monkeypatch):
    from backend.app.services import dispatchers

    started, stopping, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
    order = []

    async def agent():
        started.set()
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            stopping.set()
            await release.wait()
            order.append("agent-finalized")

    async def close_pool():
        order.append("pool-closed")

    async def drain_previews():
        order.append("previews-drained")

    async def close_executors():
        order.append("executors-closed")

    monkeypatch.setattr(dispatchers, "DISPATCHER_TASKS", [])
    monkeypatch.setattr(dispatchers, "SHUTDOWN_GRACE_SECONDS", 0.01)
    monkeypatch.setattr(dispatchers, "close_pool", close_pool)
    monkeypatch.setattr(dispatchers, "drain_preview_tasks", drain_previews)
    monkeypatch.setattr(dispatchers, "close_blocking_executors", close_executors)
    monkeypatch.setattr(dispatchers, "close_database_connections", lambda: order.append("database-closed"))
    task = asyncio.create_task(agent(), name="agent-test-shutdown")
    state.agent_turn_tasks["turn"] = task
    await started.wait()
    shutdown = asyncio.create_task(dispatchers.shutdown())
    await stopping.wait()
    await asyncio.sleep(0.03)
    assert not shutdown.done() and order == []
    release.set()
    await shutdown
    assert order == ["agent-finalized", "pool-closed", "previews-drained", "executors-closed", "database-closed"]


def test_assistant_slot_lease_covers_total_request_timeout():
    for timeout in (10, 60, 300):
        total = assistant.agent_client.request_total_timeout_seconds(timeout)
        assert assistant._assistant_slot_lease_seconds(timeout) >= total + assistant.ASSISTANT_SLOT_CLEANUP_MARGIN_SECONDS


def test_slot_renewal_is_owner_fenced_and_blocks_takeover(client):
    from datetime import datetime, timedelta, timezone

    from backend.app.repositories import coordination

    def at(seconds):
        return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat()

    name = coordination.acquire_background_slot(
        name_prefix="renew_test", owner="a", slot_count=1, lease_expires_at=at(5)
    )
    assert name
    # Another owner cannot renew, nor take the slot while the lease is live.
    assert not coordination.renew_background_slot(name=name, owner="b", lease_expires_at=at(500))
    assert coordination.renew_background_slot(name=name, owner="a", lease_expires_at=at(500))
    assert coordination.acquire_background_slot(
        name_prefix="renew_test", owner="b", slot_count=1, lease_expires_at=at(500), now=at(91)
    ) is None
    # A stale owner cannot release the new owner's slot, nor renew an expired one.
    assert not coordination.renew_background_slot(name=name, owner="a", lease_expires_at=at(900), now=at(600))
    assert coordination.acquire_background_slot(
        name_prefix="renew_test", owner="b", slot_count=1, lease_expires_at=at(1500), now=at(600)
    ) == name
    assert not coordination.release_background_slot(name=name, owner="a")


@pytest.mark.anyio
async def test_lost_slot_aborts_request_with_503(monkeypatch):
    semaphore = asyncio.Semaphore(1)

    async def acquire(timeout):
        return "slot", "owner"

    async def free(*slot):
        pass

    monkeypatch.setattr(assistant, "_assistant_request_semaphore", lambda: semaphore)
    monkeypatch.setattr(assistant, "_acquire_assistant_slot", acquire)
    monkeypatch.setattr(assistant, "_release_assistant_slot", free)
    monkeypatch.setattr(assistant, "_assistant_slot_lease_seconds", lambda timeout: 3)
    monkeypatch.setattr(assistant, "renew_background_slot", lambda **kwargs: False)
    with pytest.raises(DomainError) as error:
        async with assistant._assistant_request_limit(30):
            await asyncio.sleep(5)
    assert error.value.status_code == 503 and not semaphore.locked()
