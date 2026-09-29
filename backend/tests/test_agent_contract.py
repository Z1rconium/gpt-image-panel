import json
import threading
import time

from backend.app.integrations import agent_client
from backend.app.integrations.agent_client import (
    AgentClientError,
    AgentToolsUnsupportedError,
    AssistantTextItem,
    Finish,
    TextDelta,
    ToolCallComplete,
    ToolCallItem,
    ToolCallStarted,
    ToolResultItem,
    UserItem,
)
from backend.app.repositories import agent as agent_repo
from backend.tests.support.contract import *  # noqa: F403

TERMINAL = {"completed", "failed", "cancelled", "interrupted"}


class FakeModel:
    """Scripted stand-in for agent_client.stream_agent_response.

    Each script entry answers one model call: a list of stream events, or a
    callable taking the call kwargs and returning an async iterator of events.
    """

    def __init__(self, script):
        self.script = list(script)
        self.calls: list[dict] = []

    async def __call__(self, **kwargs):
        # Snapshot the items: the runner keeps appending to the same list.
        self.calls.append({**kwargs, "items": list(kwargs["items"])})
        step = self.script.pop(0)
        if callable(step):
            async for event in step(kwargs):
                yield event
            return
        for event in step:
            if isinstance(event, Exception):
                raise event
            yield event


def batch_call(call_id, *images):
    args = json.dumps({"images": [{"id": image_id, "prompt": prompt} for image_id, prompt in images]})
    return [
        ToolCallStarted(call_id, "generate_image_batch"),
        ToolCallComplete(call_id, "generate_image_batch", args),
        Finish(),
    ]


def text_round(*chunks):
    return [*(TextDelta(chunk) for chunk in chunks), Finish()]


def enable_agent(client, **agent_settings):
    settings = client.get("/api/settings").json()
    payload = _assistant_runtime_payload(settings)  # noqa: F405
    payload["ai_assistant"].update({"agent_enabled": True, **agent_settings})
    response = client.post("/api/settings", json=payload)
    assert response.status_code == 200, response.text
    return response.json()


def install_model(monkeypatch, script):
    model = FakeModel(script)
    monkeypatch.setattr(agent_client, "stream_agent_response", model)
    return model


def new_conversation(client, title=""):
    response = client.post("/api/agent/conversations", json={"title": title})
    assert response.status_code == 201, response.text
    return response.json()["id"]


_turn_counter = 0


def start_turn(client, conversation_id, text, *, client_turn_id=None, expect=202, **extra):
    global _turn_counter
    _turn_counter += 1
    response = client.post(
        f"/api/agent/conversations/{conversation_id}/turns",
        json={"client_turn_id": client_turn_id or f"turn-{_turn_counter:06d}", "text": text, **extra},
    )
    assert response.status_code == expect, response.text
    return response.json()


def wait_turn(client, turn_id, timeout=15.0):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        response = client.get(f"/api/agent/turns/{turn_id}")
        assert response.status_code == 200
        last = response.json()
        if last["status"] in TERMINAL:
            return last
        time.sleep(0.05)
    raise AssertionError(f"agent turn {turn_id} did not finish: {last}")


def run_turn(client, conversation_id, text, **extra):
    accepted = start_turn(client, conversation_id, text, **extra)
    return accepted, wait_turn(client, accepted["turn_id"])


def detail(client, conversation_id):
    response = client.get(f"/api/agent/conversations/{conversation_id}")
    assert response.status_code == 200, response.text
    return response.json()


def assistant_blocks(payload, round_no):
    message = next(m for m in payload["messages"] if m["role"] == "assistant" and m["round_no"] == round_no)
    return message["blocks"]


def parse_sse(text):
    events = []
    for frame in text.strip().split("\n\n"):
        fields = dict(line.split(": ", 1) for line in frame.splitlines() if ": " in line)
        if "event" in fields:
            events.append((int(fields["id"]), fields["event"], json.loads(fields["data"])))
    return events


def test_agent_requires_enablement_and_settings_roundtrip(client):
    conversation_id = new_conversation(client)
    response = client.post(
        f"/api/agent/conversations/{conversation_id}/turns",
        json={"client_turn_id": "turn-disabled-1", "text": "hello"},
    )
    assert response.status_code == 400 and "not enabled" in response.text

    updated = enable_agent(client, agent_model="agent-model", agent_max_tool_rounds=3, agent_system_prompt=" Be brief. ")
    assistant = updated["ai_assistant"]
    assert assistant["agent_enabled"] is True and assistant["agent_model"] == "agent-model"
    assert assistant["agent_max_tool_rounds"] == 3 and assistant["agent_system_prompt"] == "Be brief."
    reloaded = client.get("/api/settings").json()["ai_assistant"]
    assert reloaded["agent_enabled"] is True and reloaded["agent_max_tool_rounds"] == 3

    too_many = client.post(
        "/api/settings",
        json={**_settings_payload(updated), "ai_assistant": {"agent_max_tool_rounds": 500}},  # noqa: F405
    )
    assert too_many.status_code == 422


def test_agent_happy_path_generates_batch_and_summarizes(client, monkeypatch):
    enable_agent(client, agent_model="agent-model")
    model = install_model(
        monkeypatch,
        [
            [TextDelta("Sure, "), TextDelta("making two.")] + batch_call("call-1", ("fox", "a red fox"), ("owl", "a gray owl")),
            text_round("Made <re", 'f id="round-1-image-1"/> and both are ready.'),
        ],
    )
    conversation_id = new_conversation(client)
    accepted, status = run_turn(client, conversation_id, "draw a fox and an owl")
    assert status["status"] == "completed" and status["rounds_used"] == 1
    assert accepted["round_no"] == 1

    payload = detail(client, conversation_id)
    assert [m["role"] for m in payload["messages"]] == ["user", "assistant"]
    assert payload["conversation"]["title"] == "draw a fox and an owl"
    assert payload["active_turn"] is None
    blocks = assistant_blocks(payload, 1)
    assert [b["type"] for b in blocks] == ["text", "batch_params", "image_task", "image_task", "text"]
    assert blocks[0]["text"] == "Sure, making two."
    assert blocks[1]["status"] == "ready" and [i["id"] for i in blocks[1]["items"]] == ["fox", "owl"]
    tasks = blocks[2:4]
    assert [t["ref_label"] for t in tasks] == ["round-1-image-1", "round-1-image-2"]
    assert all(t["status"] == "succeeded" and t["image_id"] and not t["deleted"] for t in tasks)
    assert blocks[4]["text"] == "Made  and both are ready."
    assistant = payload["messages"][1]
    assert assistant["status"] == "complete" and "<ref" not in assistant["text"]
    assert {ref["ref_label"] for ref in payload["image_refs"]} == {"round-1-image-1", "round-1-image-2"}
    for task in tasks:
        gallery = client.get(f"/api/gallery/{task['image_id']}")
        assert gallery.status_code == 200

    first, second = model.calls
    assert first["model"] == "assistant-model" or first["model"] == "agent-model"
    assert first["tool_choice"] == "auto" and {t.name for t in first["tools"]} == {"generate_image_batch", "continue_generation"}
    assert isinstance(first["items"][0], UserItem) and first["items"][0].text == "draw a fox and an owl"
    kinds = [type(item) for item in second["items"]]
    assert kinds == [UserItem, AssistantTextItem, ToolCallItem, ToolResultItem, UserItem]
    results = json.loads(second["items"][3].output)
    assert [r["status"] for r in results] == ["created", "created"] and results[0]["ref"] == "round-1-image-1"
    assert len(second["items"][4].images) == 2

    stream = client.get(f"/api/agent/turns/{accepted['turn_id']}/events")
    assert stream.status_code == 200 and stream.headers["content-type"].startswith("text/event-stream")
    events = parse_sse(stream.text)
    names = [name for _seq, name, _data in events]
    assert names[0] == "turn.started" and names[-1] == "turn.completed"
    assert "block.text" in names and "block.upsert" in names
    assert [seq for seq, _n, _d in events] == sorted(seq for seq, _n, _d in events)
    later = parse_sse(client.get(f"/api/agent/turns/{accepted['turn_id']}/events?after={events[2][0]}").text)
    assert [seq for seq, _n, _d in later] == [seq for seq, _n, _d in events[3:]]
    resumed = parse_sse(
        client.get(
            f"/api/agent/turns/{accepted['turn_id']}/events", headers={"Last-Event-ID": str(events[-2][0])}
        ).text
    )
    assert [name for _s, name, _d in resumed] == ["turn.completed"]


def test_agent_dependent_image_uses_edit_path_with_ref(client, monkeypatch):
    enable_agent(client)
    model = install_model(
        monkeypatch,
        [
            batch_call("c1", ("hero", "a knight character sheet")),
            [ToolCallStarted("c2", "continue_generation"), ToolCallComplete("c2", "continue_generation", '{"reason":"scene needs the knight"}'), Finish()],
            batch_call("c3", ("scene", 'the knight <ref id="round-1-image-1"/> in a forest')),
            text_round("Done."),
        ],
    )
    conversation_id = new_conversation(client)
    _accepted, status = run_turn(client, conversation_id, "make a knight, then a forest scene")
    assert status["status"] == "completed" and status["rounds_used"] == 3
    blocks = assistant_blocks(detail(client, conversation_id), 1)
    tasks = [b for b in blocks if b["type"] == "image_task"]
    assert [t["mode"] for t in tasks] == ["generate", "edit"]
    assert tasks[1]["source_refs"] == ["round-1-image-1"] and tasks[1]["status"] == "succeeded"
    assert tasks[1]["ref_label"] == "round-1-image-2"
    third = model.calls[2]["items"]
    assert [type(item) for item in third] == [
        UserItem, ToolCallItem, ToolResultItem, UserItem, ToolCallItem, ToolResultItem,
    ]
    assert json.loads(third[-1].output) == {"ok": True}
    assert third[3].images and 'image 1 = <ref id="round-1-image-1"/>' in third[3].text


def test_agent_unknown_or_same_call_refs_return_errors_to_the_model(client, monkeypatch):
    enable_agent(client)
    model = install_model(
        monkeypatch,
        [
            batch_call("c1", ("a", 'edit <ref id="round-9-image-1"/>'), ("b", "fresh image")),
            text_round("One image made."),
        ],
    )
    conversation_id = new_conversation(client)
    _accepted, status = run_turn(client, conversation_id, "go")
    assert status["status"] == "completed"
    results = json.loads(model.calls[1]["items"][-2].output)
    assert results[0]["status"] == "error" and "round-9-image-1" in results[0]["error"]
    assert results[1]["status"] == "created"
    refs = detail(client, conversation_id)["image_refs"]
    assert [ref["ref_label"] for ref in refs] == ["round-1-image-1"]


def test_agent_invalid_tool_arguments_are_fed_back(client, monkeypatch):
    enable_agent(client)
    bad = [ToolCallStarted("c1", "generate_image_batch"), ToolCallComplete("c1", "generate_image_batch", '{"images": []}'), Finish()]
    model = install_model(monkeypatch, [bad, text_round("Sorry, let me explain instead.")])
    conversation_id = new_conversation(client)
    _accepted, status = run_turn(client, conversation_id, "go")
    assert status["status"] == "completed"
    assert "non-empty array" in json.loads(model.calls[1]["items"][-1].output)["error"]
    blocks = assistant_blocks(detail(client, conversation_id), 1)
    assert [b["type"] for b in blocks] == ["batch_params", "text"] and blocks[0]["status"] == "invalid"


def test_agent_repeated_invalid_tool_arguments_fail_the_turn(client, monkeypatch):
    enable_agent(client)
    bad = [ToolCallStarted("c", "nope"), ToolCallComplete("c", "nope", "{}"), Finish()]
    install_model(monkeypatch, [bad, bad])
    conversation_id = new_conversation(client)
    _accepted, status = run_turn(client, conversation_id, "go")
    assert status["status"] == "failed" and "invalid tool arguments" in status["error_message"]
    payload = detail(client, conversation_id)
    assert assistant_blocks(payload, 1)[-1]["type"] == "error"
    assert payload["messages"][1]["status"] == "failed"


def test_agent_round_limit_switches_tool_choice_to_none(client, monkeypatch):
    enable_agent(client, agent_max_tool_rounds=1)
    model = install_model(monkeypatch, [batch_call("c1", ("a", "one")), text_round("All done.")])
    conversation_id = new_conversation(client)
    _accepted, status = run_turn(client, conversation_id, "go")
    assert status["status"] == "completed" and status["rounds_used"] == 1
    assert [call["tool_choice"] for call in model.calls] == ["auto", "none"]


def test_agent_empty_model_response_fails(client, monkeypatch):
    enable_agent(client)
    install_model(monkeypatch, [[Finish()]])
    conversation_id = new_conversation(client)
    _accepted, status = run_turn(client, conversation_id, "go")
    assert status["status"] == "failed" and "empty response" in status["error_message"]


def test_agent_model_errors_become_failed_turns_and_are_redacted(client, monkeypatch):
    enable_agent(client)
    install_model(monkeypatch, [[TextDelta("partial "), AgentToolsUnsupportedError()]])
    conversation_id = new_conversation(client)
    _accepted, status = run_turn(client, conversation_id, "go")
    assert status["status"] == "failed" and "does not support tool calling" in status["error_message"]
    blocks = assistant_blocks(detail(client, conversation_id), 1)
    assert blocks[0]["text"] == "partial " and blocks[-1]["type"] == "error"

    install_model(monkeypatch, [[AgentClientError("upstream said Bearer sk-abcdefghijklmnop1234 is invalid")]])
    _accepted, status = run_turn(client, conversation_id, "again")
    assert status["status"] == "failed" and "sk-abcdefghijklmnop1234" not in status["error_message"]


def test_agent_idempotent_replay_and_one_active_turn(client, monkeypatch):
    enable_agent(client)
    release = threading.Event()

    async def blocked(kwargs):
        while not release.is_set():
            await asyncio.sleep(0.02)
        yield TextDelta("finally")
        yield Finish()

    install_model(monkeypatch, [blocked])
    conversation_id = new_conversation(client)
    accepted = start_turn(client, conversation_id, "first", client_turn_id="same-turn-1")
    replay = start_turn(client, conversation_id, "first", client_turn_id="same-turn-1", expect=200)
    assert replay["turn_id"] == accepted["turn_id"] and replay["replayed"] is True
    conflict = client.post(
        f"/api/agent/conversations/{conversation_id}/turns",
        json={"client_turn_id": "other-turn-1", "text": "second"},
    )
    assert conflict.status_code == 409
    assert detail(client, conversation_id)["active_turn"]["id"] == accepted["turn_id"]
    assert client.get("/api/agent/conversations").json()["items"][0]["active_turn_id"] == accepted["turn_id"]
    release.set()
    assert wait_turn(client, accepted["turn_id"])["status"] == "completed"
    assert detail(client, conversation_id)["active_turn"] is None


def test_agent_cancel_stops_a_silent_model_call(client, monkeypatch):
    enable_agent(client)
    started = threading.Event()

    async def silent(kwargs):
        started.set()
        while True:
            await asyncio.sleep(0.05)
        yield  # pragma: no cover

    install_model(monkeypatch, [silent])
    conversation_id = new_conversation(client)
    accepted = start_turn(client, conversation_id, "go")
    assert started.wait(5)
    cancel = client.post(f"/api/agent/turns/{accepted['turn_id']}/cancel")
    assert cancel.status_code == 202
    assert wait_turn(client, accepted["turn_id"])["status"] == "cancelled"
    payload = detail(client, conversation_id)
    assert payload["messages"][1]["status"] == "cancelled"
    names = [name for _s, name, _d in parse_sse(client.get(f"/api/agent/turns/{accepted['turn_id']}/events").text)]
    assert names[-1] == "turn.cancelled"
    again = client.post(f"/api/agent/turns/{accepted['turn_id']}/cancel")
    assert again.status_code == 202 and again.json()["status"] == "cancelled"
    assert client.post("/api/agent/turns/missing/cancel").status_code == 404


def test_agent_cancel_cancels_pending_image_jobs(client, monkeypatch):
    enable_agent(client)
    gate = threading.Event()

    async def slow_generation(*args, **kwargs):
        while not gate.is_set():
            await asyncio.sleep(0.02)
        return []

    monkeypatch.setattr(backend_main.proxy, "call_image_generation_api", slow_generation)  # noqa: F405
    install_model(monkeypatch, [batch_call("c1", ("a", "slow one"))])
    conversation_id = new_conversation(client)
    accepted = start_turn(client, conversation_id, "go")
    deadline = time.time() + 5
    while time.time() < deadline:
        blocks = assistant_blocks(detail(client, conversation_id), 1)
        tasks = [b for b in blocks if b["type"] == "image_task" and b["job_id"]]
        if tasks:
            break
        time.sleep(0.05)
    assert tasks, "image job was never queued"
    client.post(f"/api/agent/turns/{accepted['turn_id']}/cancel")
    assert wait_turn(client, accepted["turn_id"])["status"] == "cancelled"
    task = [b for b in assistant_blocks(detail(client, conversation_id), 1) if b["type"] == "image_task"][0]
    assert task["status"] == "cancelled"
    # The image job itself was cancelled, not left running in the background.
    assert client.get(f"/api/generate/{task['job_id']}").json()["status"] == "cancelled"
    gate.set()


def test_agent_attachments_and_history_context(client, monkeypatch):
    enable_agent(client)
    _fake_gallery_entry("agent-attach-1", "source", "1024x1024", "agent-attach-1.png")  # noqa: F405
    model = install_model(
        monkeypatch,
        [
            text_round("I see a picture."),
            text_round("Second answer."),
        ],
    )
    conversation_id = new_conversation(client)
    missing = client.post(
        f"/api/agent/conversations/{conversation_id}/turns",
        json={"client_turn_id": "turn-missing-1", "text": "x", "attachments": [{"kind": "gallery", "image_id": "nope"}]},
    )
    assert missing.status_code == 422
    _accepted, status = run_turn(
        client, conversation_id, "describe this",
        attachments=[{"kind": "gallery", "image_id": "agent-attach-1"}],
    )
    assert status["status"] == "completed"
    first_user = model.calls[0]["items"][0]
    assert '[attached: <ref id="round-1-input-1"/>]' in first_user.text
    assert len(first_user.images) == 1 and first_user.images[0].startswith("data:image/")
    assert 'image 1 = <ref id="round-1-input-1"/>' in first_user.text

    _accepted, status = run_turn(client, conversation_id, "and now @round-1-input-1 again")
    assert status["status"] == "completed"
    items = model.calls[1]["items"]
    assert [type(item) for item in items] == [UserItem, AssistantTextItem, UserItem]
    assert items[1].text == "I see a picture."
    assert '<ref id="round-1-input-1"/> again' in items[2].text and len(items[2].images) == 1
    payload = detail(client, conversation_id)
    assert [ref["ref_label"] for ref in payload["image_refs"]] == ["round-1-input-1"]
    assert payload["image_refs"][0]["image_id"] == "agent-attach-1"


def test_agent_gallery_deletion_marks_refs_removed(client, monkeypatch):
    enable_agent(client)
    model = install_model(
        monkeypatch,
        [
            batch_call("c1", ("a", "first")),
            text_round("Made one."),
            text_round("It was deleted."),
        ],
    )
    conversation_id = new_conversation(client)
    run_turn(client, conversation_id, "make one")
    payload = detail(client, conversation_id)
    image_id = payload["image_refs"][0]["image_id"]
    assert image_id
    assert client.delete(f"/api/gallery/{image_id}").status_code == 200

    payload = detail(client, conversation_id)
    ref = payload["image_refs"][0]
    assert ref["deleted"] is True and ref["image_id"] is None and ref["status"] == "succeeded"
    task = [b for b in assistant_blocks(payload, 1) if b["type"] == "image_task"][0]
    assert task["deleted"] is True and task["image_id"] is None

    run_turn(client, conversation_id, "what about @round-1-image-1?")
    history = model.calls[2]["items"]
    assert '<removed_ref id="round-1-image-1"/>' in history[1].text
    assert history[-1].images == ()


def test_agent_delete_conversation_keeps_gallery_and_cascades(client, monkeypatch):
    enable_agent(client)
    install_model(monkeypatch, [batch_call("c1", ("a", "keep me")), text_round("ok")])
    conversation_id = new_conversation(client)
    _accepted, _status = run_turn(client, conversation_id, "go")
    image_id = detail(client, conversation_id)["image_refs"][0]["image_id"]

    renamed = client.patch(f"/api/agent/conversations/{conversation_id}", json={"title": "Renamed"})
    assert renamed.status_code == 200 and renamed.json()["title"] == "Renamed"
    assert client.patch(f"/api/agent/conversations/{conversation_id}", json={"title": "  "}).status_code == 422
    assert client.delete(f"/api/agent/conversations/{conversation_id}").status_code == 200
    assert client.get(f"/api/agent/conversations/{conversation_id}").status_code == 404
    assert client.delete(f"/api/agent/conversations/{conversation_id}").status_code == 404
    assert client.get(f"/api/gallery/{image_id}").status_code == 200
    assert agent_repo.list_conversation_images(conversation_id) == []


def test_agent_delete_waits_for_active_turn_to_cancel(client, monkeypatch):
    enable_agent(client)

    async def silent(kwargs):
        while True:
            await asyncio.sleep(0.05)
        yield  # pragma: no cover

    install_model(monkeypatch, [silent])
    conversation_id = new_conversation(client)
    accepted = start_turn(client, conversation_id, "go")
    assert client.delete(f"/api/agent/conversations/{conversation_id}").status_code == 200
    assert client.get(f"/api/agent/turns/{accepted['turn_id']}").status_code == 404


def test_agent_stale_turn_is_interrupted_on_read(client):
    enable_agent(client)
    conversation_id = new_conversation(client)
    turn, _created = agent_repo.create_turn(
        conversation_id,
        client_turn_id="stale-turn-01",
        text="orphaned",
        attachment_image_ids=[],
        model="m",
        image_params={},
        lease_expires_at="2000-01-01T00:00:00+00:00",
    )
    payload = detail(client, conversation_id)
    assert payload["active_turn"] is None
    assert payload["messages"][1]["status"] == "interrupted"
    events = parse_sse(client.get(f"/api/agent/turns/{turn['id']}/events").text)
    assert [name for _s, name, _d in events] == ["turn.failed"]
    assert client.get("/api/agent/turns/missing/events").status_code == 404


def test_agent_limits_and_validation(client, monkeypatch):
    enable_agent(client)
    install_model(monkeypatch, [text_round("hi")])
    conversation_id = new_conversation(client)
    long_text = "x" * (config.AGENT_MAX_USER_TEXT_CHARS + 1)  # noqa: F405
    assert client.post(
        f"/api/agent/conversations/{conversation_id}/turns",
        json={"client_turn_id": "turn-long-0001", "text": long_text},
    ).status_code == 422
    assert client.post(
        f"/api/agent/conversations/{conversation_id}/turns",
        json={"client_turn_id": "bad id!", "text": "x"},
    ).status_code == 422
    assert client.post(
        f"/api/agent/conversations/{conversation_id}/turns",
        json={"client_turn_id": "turn-image-0001", "text": "x", "image_params": {"size": "10x10"}},
    ).status_code == 422
    assert client.post("/api/agent/conversations/missing/turns", json={"client_turn_id": "turn-x-000001", "text": "x"}).status_code == 404

    monkeypatch.setattr(config, "AGENT_MAX_CONVERSATIONS", 1)  # noqa: F405
    assert client.post("/api/agent/conversations", json={}).status_code == 409
    monkeypatch.setattr(config, "AGENT_MAX_TURNS_PER_CONVERSATION", 1)  # noqa: F405
    run_turn(client, conversation_id, "one")
    limited = client.post(
        f"/api/agent/conversations/{conversation_id}/turns",
        json={"client_turn_id": "turn-limit-0002", "text": "two"},
    )
    assert limited.status_code == 409

