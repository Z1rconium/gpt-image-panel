import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest

from backend.app.repositories import agent as repo
from backend.app.repositories.db import schema
from backend.tests.support.contract import _configure_runtime, _fake_gallery_entry, db_repo
from backend.tests.test_agent_repository import _turn
from backend.tests.test_agent_contract import detail, enable_agent, install_model, new_conversation, run_turn, start_turn, text_round, wait_turn


def test_model_cannot_import_another_branch_image_without_an_explicit_attachment(client, monkeypatch):
    import json
    from backend.app.integrations.agent_client import ToolResultItem
    from backend.tests.test_agent_contract import batch_call

    enable_agent(client)
    model = install_model(monkeypatch, [batch_call("original", ("hero", "fox")), text_round("original answer"), batch_call("cross", ("copy", 'edit <ref id="round-1-image-1"/>')), text_round("cannot edit"), text_round("attached")])
    conversation = new_conversation(client)
    original, _ = run_turn(client, conversation, "draw")
    current = detail(client, conversation)
    image = current["image_refs"][0]["image_id"]
    assert client.patch(f"/api/agent/conversations/{conversation}/branch", json={"selected_turn_id": None, "expected_revision": current["conversation"]["branch_revision"]}).status_code == 200
    run_turn(client, conversation, "try to edit")
    results = [json.loads(item.output) for item in model.calls[-1]["items"] if isinstance(item, ToolResultItem)]
    assert results[0][0]["status"] == "error" and "not available" in results[0][0]["error"]
    assert not detail(client, conversation)["image_refs"]
    # Explicit attachments create a reference on the new path and remain
    # invalidated when Gallery removes the image, including on the old path.
    run_turn(client, conversation, "attached", attachments=[{"kind": "gallery", "image_id": image}])
    assert detail(client, conversation)["image_refs"][0]["image_id"] == image
    assert client.delete(f"/api/gallery/{image}").status_code == 200
    assert detail(client, conversation)["image_refs"][0]["deleted"]
    current = detail(client, conversation)
    restored = client.patch(f"/api/agent/conversations/{conversation}/branch", json={"selected_turn_id": original["turn_id"], "expected_revision": current["conversation"]["branch_revision"]})
    assert restored.json()["image_refs"][0]["deleted"]


def test_linear_migration_preserves_running_turn_and_is_repeatable():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    schema._migration_agent_conversations(conn)
    conn.execute("INSERT INTO agent_conversations(id, created_at, updated_at) VALUES ('c', 'now', 'now')")
    for index, status in enumerate(["completed", "running"], 1):
        conn.execute("INSERT INTO agent_turns(id, conversation_id, round_no, client_turn_id, status, created_at) VALUES (?, 'c', ?, ?, ?, 'now')", (f"t{index}", index, f"client-{index}", status))
    schema._migration_agent_branches(conn)
    schema._migration_agent_search(conn)
    assert [(row["parent_turn_id"], row["status"], row["web_search_enabled"]) for row in conn.execute("SELECT * FROM agent_turns ORDER BY round_no")] == [(None, "completed", 0), ("t1", "running", 0)]
    assert conn.execute("SELECT selected_turn_id FROM agent_conversations").fetchone()[0] == "t2"
    conn.execute("UPDATE agent_turns SET parent_turn_id = NULL WHERE id = 't2'")
    schema._migration_agent_branches(conn)
    schema._migration_agent_search(conn)
    assert conn.execute("SELECT parent_turn_id FROM agent_turns WHERE id = 't2'").fetchone()[0] is None
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    conn.close()


def test_edit_preserves_original_descendants_and_isolates_model_context(client, monkeypatch):
    enable_agent(client)
    model = install_model(monkeypatch, [text_round("reply one"), text_round("reply two"), text_round("reply three"), text_round("new reply two"), text_round("new reply three")])
    conversation = new_conversation(client)
    first, _ = run_turn(client, conversation, "original one")
    second, _ = run_turn(client, conversation, "original two")
    third, _ = run_turn(client, conversation, "original three")
    before = detail(client, conversation)
    edited, _ = run_turn(client, conversation, "changed two", action="edit", source_turn_id=second["turn_id"], branch_revision=before["conversation"]["branch_revision"])
    run_turn(client, conversation, "changed three")
    current = detail(client, conversation)
    assert [message["text"] for message in current["messages"] if message["role"] == "user"] == ["original one", "changed two", "changed three"]
    assert [message["path_round_no"] for message in current["messages"] if message["role"] == "user"] == [1, 2, 3]
    assert len(current["branches"]) == 5
    assert repo.get_turn(edited["turn_id"])["parent_turn_id"] == first["turn_id"]
    for call in model.calls[3:]:
        text = " ".join(getattr(item, "text", "") for item in call["items"])
        assert "original two" not in text and "original three" not in text and "reply three" not in text
    count = len(model.calls)
    switched = client.patch(f"/api/agent/conversations/{conversation}/branch", json={"selected_turn_id": third["turn_id"], "expected_revision": current["conversation"]["branch_revision"]})
    assert switched.status_code == 200
    assert len(model.calls) == count
    assert [message["text"] for message in switched.json()["messages"] if message["role"] == "user"] == ["original one", "original two", "original three"]
    earlier = client.get(f"/api/agent/conversations/{conversation}?limit=2&before_seq=5").json()
    assert [message["turn_id"] for message in earlier["messages"]] == [second["turn_id"]] * 2


def test_before_seq_is_bounded_to_sqlite_integers(client, monkeypatch):
    enable_agent(client)
    conversation = new_conversation(client)
    base = f"/api/agent/conversations/{conversation}"
    assert client.get(f"{base}?before_seq={2**63 - 1}").status_code == 200
    for value in (2**63, -1, 0, "9" * 40):
        assert client.get(f"{base}?before_seq={value}").status_code == 422


def test_regenerate_replays_original_input_and_keeps_original_answer(client, monkeypatch):
    enable_agent(client)
    model = install_model(monkeypatch, [text_round("old answer"), text_round("new answer")])
    conversation = new_conversation(client)
    original, _ = run_turn(client, conversation, "actual prompt", image_params={"size": "1024x1024", "quality": "high", "output_format": "webp"})
    current = detail(client, conversation)
    regenerated, _ = run_turn(client, conversation, "ignored replacement", action="regenerate", source_turn_id=original["turn_id"], branch_revision=current["conversation"]["branch_revision"], client_turn_id="regenerate-stable")
    assert repo.get_turn(regenerated["turn_id"])["image_params"]["quality"] == "high"
    assert model.calls[-1]["items"][-1].text == "actual prompt"
    assert repo.get_message(original["assistant_message_id"])["text"] == "old answer"
    replay = start_turn(client, conversation, "ignored", action="regenerate", source_turn_id=original["turn_id"], branch_revision=0, client_turn_id="regenerate-stable", expect=200)
    assert replay["turn_id"] == regenerated["turn_id"] and len(model.calls) == 2


@pytest.mark.parametrize("mention", ["@第" + "9" * 5000 + "轮图1", "@第1轮图" + "9" * 5000])
def test_oversized_reference_numbers_return_validation_error(client, monkeypatch, mention):
    enable_agent(client)
    model = install_model(monkeypatch, [])
    conversation = new_conversation(client)
    start_turn(client, conversation, mention, expect=422)
    assert model.calls == []
    assert detail(client, conversation)["conversation"]["turn_count"] == 0


def test_reference_isolation_aliases_and_selection_conflicts(tmp_path):
    _configure_runtime(tmp_path)
    db_repo.verify_storage_writable()
    _fake_gallery_entry("branch-image", "p", "1024x1024", "branch.png")
    conversation = repo.create_conversation()["id"]
    original, _ = _turn(conversation)
    output = repo.insert_pending_output_image(conversation_id=conversation, turn_id=original["id"], message_id=original["assistant_message_id"], round_no=1, item_id="image", prompt="p", mode="generate")
    repo.settle_image(output["id"], status="succeeded", image_id="branch-image")
    repo.finish_turn(original["id"], "completed")
    repo.select_branch(conversation, None, 1)
    with pytest.raises(repo.AgentAttachmentError, match="not on this branch"):
        _turn(conversation, "cross-branch-mention", text="change @round-1-image-1")
    # Explicit gallery attachments import the image into this path.
    new, _ = _turn(conversation, "explicit-attachment", attachment_image_ids=["branch-image"])
    assert repo.path_turn_ids(conversation, new["id"]) == [new["id"]]
    repo.finish_turn(new["id"], "completed")
    revision = repo.get_conversation(conversation)["branch_revision"]
    with ThreadPoolExecutor(max_workers=2) as executor:
        def select(head):
            try:
                repo.select_branch(conversation, head, revision)
                return "selected"
            except repo.AgentTurnConflictError:
                return "conflict"
        outcomes = list(executor.map(select, [original["id"], new["id"]]))
    assert sorted(outcomes) == ["conflict", "selected"]
    revision = repo.get_conversation(conversation)["branch_revision"]
    repo.select_branch(conversation, original["id"], revision)
    continued, _ = _turn(conversation, "chinese-alias", text="修改 @第1轮图1")
    assert repo.get_message(continued["user_message_id"])["text"] == "修改 @round-1-image-1"
    # Selection never cancels the active turn, and another admission is refused.
    revision = repo.get_conversation(conversation)["branch_revision"]
    repo.select_branch(conversation, new["id"], revision)
    assert repo.get_turn(continued["id"])["cancel_requested"] is False
    with pytest.raises(repo.AgentTurnConflictError):
        _turn(conversation, "parallel-turn")
    other = repo.create_conversation()["id"]
    with pytest.raises(repo.AgentTurnConflictError):
        repo.select_branch(other, original["id"], 0)
    assert repo.delete_conversation(conversation)
