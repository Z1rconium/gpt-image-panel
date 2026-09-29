from datetime import datetime, timedelta, timezone

from backend.app.repositories import agent as agent_repo
from backend.tests.support.contract import *  # noqa: F403


def _future(seconds: int = 60) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat()


def _turn(conversation_id: str, client_turn_id: str = "turn-0001", **overrides):
    kwargs = dict(
        client_turn_id=client_turn_id,
        text="draw a fox",
        attachment_image_ids=[],
        model="test-model",
        image_params={"size": "auto"},
        lease_expires_at=_future(),
    )
    kwargs.update(overrides)
    return agent_repo.create_turn(conversation_id, **kwargs)


def test_create_turn_is_idempotent_and_single_active(tmp_path):
    _configure_runtime(tmp_path)
    db_repo.verify_storage_writable()
    conversation = agent_repo.create_conversation()

    turn, created = _turn(conversation["id"])
    assert created is True
    assert turn["round_no"] == 1 and turn["status"] == "queued"

    replay, created_again = _turn(conversation["id"])
    assert created_again is False and replay["id"] == turn["id"]

    with pytest.raises(agent_repo.AgentTurnConflictError):
        _turn(conversation["id"], "turn-0002")

    agent_repo.finish_turn(turn["id"], "completed")
    second, created = _turn(conversation["id"], "turn-0002")
    assert created is True and second["round_no"] == 2

    messages = agent_repo.list_messages(conversation["id"])
    assert [message["seq"] for message in messages] == [1, 2, 3, 4]
    assert messages[0]["role"] == "user" and messages[1]["status"] == "complete"
    detail = agent_repo.get_conversation(conversation["id"])
    assert detail["title"] == "draw a fox"
    assert detail["turn_count"] == 2 and detail["active_turn_id"] == second["id"]
    assert agent_repo.create_turn("missing", **{
        "client_turn_id": "turn-0009", "text": "x", "attachment_image_ids": [],
        "model": "m", "image_params": {}, "lease_expires_at": _future(),
    }) is None


def test_attachments_and_gallery_deletion_keep_refs(tmp_path):
    _configure_runtime(tmp_path)
    db_repo.verify_storage_writable()
    _fake_gallery_entry("agent-src-1", "source", "1024x1024", "agent-src-1.png")
    _fake_gallery_entry("agent-out-1", "output", "1024x1024", "agent-out-1.png")
    conversation = agent_repo.create_conversation("My chat")

    with pytest.raises(agent_repo.AgentAttachmentError):
        _turn(conversation["id"], attachment_image_ids=["nope"])

    turn, _ = _turn(conversation["id"], attachment_image_ids=["agent-src-1"])
    refs = agent_repo.list_conversation_images(conversation["id"])
    assert [ref["ref_label"] for ref in refs] == ["round-1-input-1"]

    pending = agent_repo.insert_pending_output_image(
        conversation_id=conversation["id"], turn_id=turn["id"],
        message_id=turn["assistant_message_id"], round_no=1, item_id="a", prompt="p", mode="generate",
    )
    second = agent_repo.insert_pending_output_image(
        conversation_id=conversation["id"], turn_id=turn["id"],
        message_id=turn["assistant_message_id"], round_no=1, item_id="b", prompt="q", mode="edit",
    )
    assert (pending["ref_label"], second["ref_label"]) == ("round-1-image-1", "round-1-image-2")
    assert len(agent_repo.list_pending_images_for_turn(turn["id"])) == 2

    settled = agent_repo.settle_image(pending["id"], status="succeeded", image_id="agent-out-1")
    assert settled["image_id"] == "agent-out-1"
    # A gallery image deleted before the outcome is recorded is stored as NULL.
    gone = agent_repo.settle_image(second["id"], status="succeeded", image_id="already-deleted")
    assert gone["status"] == "succeeded" and gone["image_id"] is None

    deleted, _ = gallery_mutations.delete_gallery_image("agent-out-1")
    assert deleted is True
    refs_after = {ref["ref_label"]: ref for ref in agent_repo.list_conversation_images(conversation["id"])}
    assert refs_after["round-1-image-1"]["status"] == "succeeded"
    assert refs_after["round-1-image-1"]["image_id"] is None
    assert refs_after["round-1-input-1"]["image_id"] == "agent-src-1"

    assert agent_repo.delete_conversation(conversation["id"]) is True
    assert agent_repo.list_conversation_images(conversation["id"]) == []
    assert gallery_queries.get_gallery_entry("agent-src-1") is not None


def test_stale_sweep_and_events(tmp_path):
    _configure_runtime(tmp_path)
    db_repo.verify_storage_writable()
    conversation = agent_repo.create_conversation()
    turn, _ = _turn(conversation["id"], lease_expires_at=_future(-5))

    assert agent_repo.claim_turn(turn["id"], owner="w1", lease_expires_at=_future(-5)) is True
    assert agent_repo.claim_turn(turn["id"], owner="w2", lease_expires_at=_future()) is False
    assert agent_repo.renew_turn_lease(turn["id"], owner="w2", lease_expires_at=_future()) is False

    assert agent_repo.append_turn_event(turn["id"], "turn.started", {"n": 1}) == 1
    assert agent_repo.append_turn_event(turn["id"], "block.text", {"n": 2}) == 2
    events = agent_repo.list_turn_events(turn["id"], after_seq=1)
    assert [event["seq"] for event in events] == [2] and events[0]["data"] == {"n": 2}

    swept = agent_repo.sweep_stale_turns()
    assert [item["id"] for item in swept] == [turn["id"]]
    assert swept[0]["status"] == "interrupted"
    assert agent_repo.get_message(turn["assistant_message_id"])["status"] == "interrupted"
    # Terminal turns stay terminal.
    assert agent_repo.finish_turn(turn["id"], "completed")["status"] == "interrupted"
    assert agent_repo.purge_turn_events(1) == 0
