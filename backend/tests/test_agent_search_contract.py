import asyncio
import threading

import pytest

from backend.app.integrations import agent_client
from backend.app.integrations.agent_client import AgentClientError, Finish, TextDelta
from backend.app.integrations.agent_search import SearchStatus, SourceCitation, safe_source_url
from backend.tests.support.contract import _assistant_runtime_payload
from backend.tests.test_agent_contract import detail, enable_agent, install_model, new_conversation, parse_sse, run_turn, start_turn, text_round, wait_turn


def enable_search(client, *, supported=True, enabled=True, responses=True, max_rounds=4):
    settings = client.get("/api/settings").json()
    payload = _assistant_runtime_payload(settings, optimizer_api_url="https://example.com/v1/responses" if responses else "https://example.com/v1/chat/completions")
    payload["ai_assistant"].update(agent_enabled=True, agent_web_search_supported=supported, agent_web_search_enabled=enabled, agent_max_tool_rounds=max_rounds)
    response = client.post("/api/settings", json=payload)
    assert response.status_code == 200, response.text
    return response.json()


def test_search_is_opt_in_and_admission_checks_declared_capabilities(client, monkeypatch):
    settings = enable_search(client, supported=False, enabled=False)
    assert not settings["ai_assistant"]["agent_web_search_enabled"]
    model = install_model(monkeypatch, [text_round("old behavior")])
    conversation = new_conversation(client)
    run_turn(client, conversation, "hello")
    assert "web_search" not in model.calls[0]
    for supported, responses in [(False, True), (True, False)]:
        enable_search(client, supported=supported, responses=responses)
        start_turn(client, conversation, "search please", expect=422)
    assert len(model.calls) == 1


def test_search_citations_persist_replay_and_follow_only_selected_branch(client, monkeypatch):
    enable_search(client)
    raw = '🌲<ref id="round-1-image-1"/>Current facts.'
    start = raw.index("Current")
    script = [SearchStatus("search-1", "in_progress"), SearchStatus("search-1", "searching", queries=("current facts",)), TextDelta(raw, "message-1"), SourceCitation("message-1", 0, "Official source", "https://example.com/facts", start, len(raw)), SearchStatus("search-1", "completed"), Finish()]
    model = install_model(monkeypatch, [script, text_round("forked reply"), text_round("continued reply")])
    conversation = new_conversation(client)
    accepted, status = run_turn(client, conversation, "search current facts")
    assert status["status"] == "completed" and status["rounds_used"] == 1
    assert model.calls[0]["web_search"] is True and model.calls[0]["max_tool_calls"] == 4
    original = detail(client, conversation)
    message = next(message for message in original["messages"] if message["role"] == "assistant")
    source = next(block for block in message["blocks"] if block["type"] == "sources")["sources"][0]
    assert source["start_index"] == 1 and source["end_index"] == len("🌲Current facts.")
    assert source["excerpt"] == "Current facts."
    assert source["text_block_id"] == next(block for block in message["blocks"] if block["type"] == "text")["id"]
    assert next(block for block in message["blocks"] if block["type"] == "search")["queries"] == ["current facts"]
    events = parse_sse(client.get(f"/api/agent/turns/{accepted['turn_id']}/events").text)
    assert any(kind == "block.upsert" and data["block"]["type"] == "sources" for _, kind, data in events)
    resumed = parse_sse(client.get(f"/api/agent/turns/{accepted['turn_id']}/events", headers={"Last-Event-ID": str(events[1][0])}).text)
    assert all(seq > events[1][0] for seq, _, _ in resumed)
    run_turn(client, conversation, "forked", action="edit", source_turn_id=accepted["turn_id"], branch_revision=original["conversation"]["branch_revision"])
    fork = detail(client, conversation)
    assert not any(block["type"] == "sources" for message in fork["messages"] for block in message["blocks"])
    selected = client.patch(f"/api/agent/conversations/{conversation}/branch", json={"selected_turn_id": accepted["turn_id"], "expected_revision": fork["conversation"]["branch_revision"]})
    assert selected.status_code == 200
    assert source in next(block for message in selected.json()["messages"] for block in message["blocks"] if block["type"] == "sources")["sources"]
    run_turn(client, conversation, "continue")
    context = " ".join(getattr(item, "text", "") for item in model.calls[-1]["items"])
    assert "https://example.com/facts" in context and "forked reply" not in context


@pytest.mark.parametrize("url", ["javascript:alert(1)", "data:text/html,x", "https://user:secret@example.com", "https://example.com/\npath", "http://[broken", "https://example.com/" + "x" * 2048])
def test_invalid_source_urls_are_rejected(url):
    assert safe_source_url(url) == ""


def test_parser_deduplicates_annotations_and_ignores_malformed_sources():
    parser = agent_client.ResponsesStreamParser()
    annotation = {"type": "url_citation", "title": "Source", "url": "https://example.com", "start_index": 0, "end_index": 4}
    assert parser.feed({"type": "response.output_text.delta", "item_id": "m", "content_index": 0, "delta": "Text"}) == [TextDelta("Text", "m", 0)]
    out = parser.feed({"type": "response.output_text.annotation.added", "item_id": "m", "content_index": 0, "annotation": annotation})
    assert len(out) == 1 and isinstance(out[0], SourceCitation)
    item = {"type": "message", "id": "m", "content": [{"type": "output_text", "text": "Text", "annotations": [annotation]}]}
    assert parser.feed({"type": "response.output_item.done", "item": item}) == []
    assert parser.feed({"type": "response.completed", "response": {"output": [item], "usage": {"input_tokens": 10}}}) == []
    assert parser.finish()[-1].usage == {"input_tokens": 10}
    for broken in [{**annotation, "url": "javascript:x"}, {**annotation, "start_index": -1}, {**annotation, "end_index": "four"}]:
        assert parser.feed({"type": "response.output_text.annotation.added", "item_id": "m", "annotation": broken}) == []
    assert parser.feed({"type": "response.output_text.annotation.added", "item_id": "m", "content_index": {}, "annotation": annotation}) == []
    complete = agent_client.parse_complete_responses_response({"output": [{"type": "web_search_call", "id": "s", "status": "completed", "action": {"type": "open_page", "url": "https://example.com"}}, item]})
    assert isinstance(complete[0], SearchStatus) and any(isinstance(event, SourceCitation) for event in complete)
    assert agent_client.parse_complete_responses_response({"output": {"unexpected": "shape"}}) == [Finish()]
    assert parser.feed({"type": "response.completed", "response": {"output": {}}}) == []
    assert agent_client.parse_complete_responses_response({"output": [{"type": "message", "content": [{"type": "output_text", "annotations": {}}]}]}) == [Finish()]


def test_search_budget_and_failure_do_not_retry_without_search(client, monkeypatch):
    enable_search(client, max_rounds=1)
    model = install_model(monkeypatch, [[SearchStatus("s1", "searching"), SearchStatus("s2", "searching")]])
    conversation = new_conversation(client)
    _, status = run_turn(client, conversation, "search")
    assert status["status"] == "failed" and "budget" in status["error_message"]
    assert len(model.calls) == 1 and model.calls[0]["web_search"]
    current = detail(client, conversation)
    assert next(block for message in current["messages"] for block in message["blocks"] if block["type"] == "search")["status"] == "failed"


def test_cancel_stops_search_and_records_terminal_status(client, monkeypatch):
    enable_search(client)
    started = threading.Event()

    async def searching(_kwargs):
        yield SearchStatus("active-search", "searching")
        started.set()
        await asyncio.Event().wait()

    install_model(monkeypatch, [searching])
    conversation = new_conversation(client)
    accepted = start_turn(client, conversation, "search")
    assert started.wait(5)
    assert client.post(f"/api/agent/turns/{accepted['turn_id']}/cancel").status_code == 202
    assert wait_turn(client, accepted["turn_id"])["status"] == "cancelled"
    blocks = detail(client, conversation)["messages"][-1]["blocks"]
    assert next(block for block in blocks if block["type"] == "search")["status"] == "cancelled"


def test_interleaved_search_text_and_image_tools_keep_citations_on_their_text_blocks(client, monkeypatch):
    from backend.tests.test_agent_contract import batch_call

    enable_search(client)
    install_model(monkeypatch, [
        [SearchStatus("search", "searching", queries=("current facts",)), TextDelta("First. ", "message"),
         *batch_call("image", ("hero", "a fox"))[:-1], TextDelta("🌲Current facts.", "message"),
         SourceCitation("message", 0, "Facts", "https://example.com/facts", 8, 22), SearchStatus("search", "completed"), Finish()],
        [TextDelta("Done.", "final"), Finish()],
    ])
    conversation = new_conversation(client)
    _, status = run_turn(client, conversation, "search and draw")
    assert status["status"] == "completed" and status["rounds_used"] == 2
    blocks = detail(client, conversation)["messages"][-1]["blocks"]
    source = next(block for block in blocks if block["type"] == "sources")["sources"][0]
    block = next(block for block in blocks if block["id"] == source["text_block_id"])
    assert block["text"] == "🌲Current facts." and source["start_index"] == 1 and source["end_index"] == 15
    assert next(block for block in blocks if block["type"] == "image_task")["status"] == "succeeded"


def test_search_upstream_failure_preserves_partial_text_and_does_not_resubmit(client, monkeypatch):
    enable_search(client)
    model = install_model(monkeypatch, [[SearchStatus("s", "searching"), TextDelta("Partial."), AgentClientError("search transport failed")]])
    conversation = new_conversation(client)
    _, status = run_turn(client, conversation, "search")
    assert status["status"] == "failed" and "search transport failed" in status["error_message"]
    blocks = detail(client, conversation)["messages"][-1]["blocks"]
    assert next(block for block in blocks if block["type"] == "text")["text"] == "Partial."
    assert next(block for block in blocks if block["type"] == "search")["status"] == "failed"
    assert len(model.calls) == 1


def test_admission_replay_remains_available_after_search_settings_change(client, monkeypatch):
    enable_search(client)
    model = install_model(monkeypatch, [text_round("answer")])
    conversation = new_conversation(client)
    accepted, _ = run_turn(client, conversation, "hello", client_turn_id="stable-search-request")
    enable_search(client, supported=False)
    replay = start_turn(client, conversation, "hello", client_turn_id="stable-search-request", branch_revision=0, expect=200)
    assert replay["replayed"] and replay["turn_id"] == accepted["turn_id"] and len(model.calls) == 1


def test_abandoned_search_status_is_settled_by_durable_turn_finalization(tmp_path):
    from backend.app.repositories import agent as repo
    from backend.tests.support.contract import _configure_runtime, db_repo
    from backend.tests.test_agent_repository import _turn

    _configure_runtime(tmp_path)
    db_repo.verify_storage_writable()
    conversation = repo.create_conversation()["id"]
    turn, _ = _turn(conversation)
    blocks = [{"id": "s", "type": "search", "status": "searching", "call_id": "s", "queries": [], "action": "search", "url": ""}]
    repo.update_message_content(turn["assistant_message_id"], text="Partial", blocks=blocks)
    repo.finish_turn(turn["id"], "interrupted")
    assert repo.get_message(turn["assistant_message_id"])["blocks"][0]["status"] == "interrupted"
