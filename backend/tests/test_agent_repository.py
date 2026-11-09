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


def test_batched_events_scoped_images_and_idle_sweep(tmp_path):
    _configure_runtime(tmp_path)
    db_repo.verify_storage_writable()
    conversation = agent_repo.create_conversation()
    turn, _ = _turn(conversation["id"])

    listed = agent_repo.list_conversations()
    assert [row["active_turn_id"] for row in listed] == [turn["id"]]

    assert agent_repo.read_event_cursor(turn["id"]) == 0
    assert agent_repo.append_turn_events(
        turn["id"],
        [("turn.started", {"n": 1}), ("block.text", {"n": 2}), ("block.text", {"n": 3})],
        start_seq=1,
    ) == 3
    assert agent_repo.read_event_cursor(turn["id"]) == 3
    assert agent_repo.append_turn_events(turn["id"], [], start_seq=4) == 3
    events = agent_repo.list_turn_events(turn["id"], after_seq=1)
    assert [event["seq"] for event in events] == [2, 3]

    pending = agent_repo.insert_pending_output_image(
        conversation_id=conversation["id"], turn_id=turn["id"],
        message_id=turn["assistant_message_id"], round_no=1, item_id="a", prompt="p", mode="generate",
    )
    scoped = agent_repo.list_conversation_images(
        conversation["id"], message_ids=[turn["assistant_message_id"]]
    )
    assert [row["ref_label"] for row in scoped] == [pending["ref_label"]]
    assert agent_repo.list_conversation_images(
        conversation["id"], message_ids=[turn["user_message_id"]]
    ) == []
    assert agent_repo.list_conversation_images(conversation["id"], message_ids=[]) == []

    # A queued turn with a future lease is not stale, so the sweep is a read-only no-op.
    assert agent_repo.sweep_stale_turns() == []


def test_sweep_rechecks_expiry_after_candidate_read(tmp_path, monkeypatch):
    _configure_runtime(tmp_path)
    db_repo.verify_storage_writable()
    conversation = agent_repo.create_conversation()
    turn, _ = _turn(conversation["id"], lease_expires_at=_future(-5))
    agent_repo.claim_turn(turn["id"], owner="worker", lease_expires_at=_future(-5))
    original = agent_repo.finish_turn

    def renew_before_write(turn_id, status, **kwargs):
        # Simulate a renewal committed after candidate selection and before the
        # sweep acquires its write lock (an expired row cannot renew normally).
        with agent_repo._connect() as conn:
            with agent_repo._transaction(conn):
                conn.execute("UPDATE agent_turns SET lease_expires_at = ? WHERE id = ?", (_future(), turn_id))
        return original(turn_id, status, **kwargs)

    monkeypatch.setattr(agent_repo.turns, "finish_turn", renew_before_write)
    assert agent_repo.sweep_stale_turns() == []
    assert agent_repo.get_turn(turn["id"])["status"] == "running"
    assert agent_repo.get_message(turn["assistant_message_id"])["status"] == "streaming"


def test_lost_owner_cannot_write_events_snapshots_or_images(tmp_path):
    _configure_runtime(tmp_path)
    db_repo.verify_storage_writable()
    conversation = agent_repo.create_conversation()
    turn, _ = _turn(conversation["id"])
    agent_repo.claim_turn(turn["id"], owner="new-owner", lease_expires_at=_future())
    with pytest.raises(agent_repo.AgentLeaseLostError):
        agent_repo.append_turn_events(turn["id"], [("block.text", {"delta": "stale"})], start_seq=1, owner="old-owner")
    with pytest.raises(agent_repo.AgentLeaseLostError):
        agent_repo.update_message_content(turn["assistant_message_id"], text="stale", blocks=[], owner="old-owner")
    with pytest.raises(agent_repo.AgentLeaseLostError):
        agent_repo.insert_pending_output_image(conversation_id=conversation["id"], turn_id=turn["id"],
            message_id=turn["assistant_message_id"], round_no=1, item_id="image", prompt="fox", mode="generate", owner="old-owner")
    assert agent_repo.finish_turn(turn["id"], "failed", owner="old-owner")["status"] == "running"
    assert agent_repo.list_turn_events(turn["id"]) == []


def test_asset_snapshot_and_regenerate(tmp_path):
    from backend.app.repositories.gallery import collections
    from backend.app.services.agent_context import build_history_items

    _configure_runtime(tmp_path)
    db_repo.verify_storage_writable()
    for image in ('asset-a', 'asset-b'):
        _fake_gallery_entry(image, 'source', '1024x1024', f'{image}.png')
    pack = collections.create_gallery_collection('Pack', asset_alias='fox')
    collections.add_gallery_collection_items(pack['id'], ['asset-a', 'asset-b'])
    collections.reorder_gallery_collection_items(pack['id'], ['asset-b', 'asset-a'])
    conversation = agent_repo.create_conversation()
    turn, _ = _turn(conversation['id'], text='Use @fox', asset_collection_ids=[pack['id']])
    refs = agent_repo.list_conversation_images(conversation['id'])
    assert [r['image_id'] for r in refs] == ['asset-b', 'asset-a']
    messages = agent_repo.list_messages(conversation['id'])
    assert messages[0]['blocks'][0]['refs'] == ['round-1-input-1', 'round-1-input-2']
    history = build_history_items(messages=messages, image_refs=refs, current_round=1)
    assert '[asset @fox: <ref id="round-1-input-1"/> <ref id="round-1-input-2"/>]' in history[0].text
    agent_repo.finish_turn(turn['id'], 'completed')
    collections.reorder_gallery_collection_items(pack['id'], ['asset-a', 'asset-b'])
    revision = agent_repo.get_conversation(conversation['id'])['branch_revision']
    regenerated, _ = _turn(conversation['id'], 'turn-0002', action='regenerate', source_turn_id=turn['id'], branch_revision=revision)
    refs = [r for r in agent_repo.list_conversation_images(conversation['id']) if r['turn_id'] == regenerated['id']]
    assert [r['image_id'] for r in refs] == ['asset-b', 'asset-a']
    messages = agent_repo.list_messages(conversation['id'])
    assert messages[-2]['blocks'][0]['refs'] == ['round-2-input-1', 'round-2-input-2']
    agent_repo.finish_turn(regenerated['id'], 'completed')
    revision = agent_repo.get_conversation(conversation['id'])['branch_revision']
    edited, _ = _turn(conversation['id'], 'turn-0003', text='Change @fox', action='edit', source_turn_id=regenerated['id'], branch_revision=revision, attachment_image_ids=['asset-b', 'asset-a'])
    messages = agent_repo.list_messages(conversation['id'])
    assert messages[-2]['blocks'][0]['refs'] == ['round-3-input-1', 'round-3-input-2']


def test_asset_invalid_and_expansion_limit_are_atomic(tmp_path, monkeypatch):
    from backend.app.repositories.gallery import collections
    from backend.app.core import settings

    _configure_runtime(tmp_path)
    db_repo.verify_storage_writable()
    for image in ('cap-a', 'cap-b'):
        _fake_gallery_entry(image, 'source', '1024x1024', f'{image}.png')
    pack = collections.create_gallery_collection('Pack', asset_alias='cap')
    collections.add_gallery_collection_items(pack['id'], ['cap-a', 'cap-b'])
    conversation = agent_repo.create_conversation()
    monkeypatch.setattr(settings, 'AGENT_MAX_ATTACHMENTS_PER_MESSAGE', 1)
    with pytest.raises(agent_repo.AgentAttachmentError, match='At most'):
        _turn(conversation['id'], asset_collection_ids=[pack['id']])
    with pytest.raises(agent_repo.AgentAttachmentError, match='no longer exists'):
        _turn(conversation['id'], asset_collection_ids=['missing'])
    assert agent_repo.list_messages(conversation['id']) == []


def test_asset_excludes_whitespace_padded_unsupported_formats(tmp_path):
    from backend.app.repositories.gallery import collections
    from backend.app.repositories.db import _connect
    _configure_runtime(tmp_path)
    db_repo.verify_storage_writable()
    for image in ('valid-a', 'invalid-a', 'invalid-b'):
        _fake_gallery_entry(image, 'source', '1024x1024', f'{image}.png')
    with _connect() as conn:
        conn.execute("UPDATE gallery_entries SET output_format = ' GIF ' WHERE id = 'invalid-a'")
        conn.execute("UPDATE gallery_entries SET filename = 'invalid-b.svg ' WHERE id = 'invalid-b'")
        conn.commit()
    pack = collections.create_gallery_collection('Filter', asset_alias='filter')
    collections.add_gallery_collection_items(pack['id'], ['valid-a', 'invalid-a', 'invalid-b'])
    conversation = agent_repo.create_conversation()
    _turn(conversation['id'], asset_collection_ids=[pack['id']])
    assert [item['image_id'] for item in agent_repo.list_conversation_images(conversation['id'])] == ['valid-a']


def test_asset_snapshot_tracks_explicit_gallery_overlap(tmp_path):
    from backend.app.repositories.gallery import collections
    _configure_runtime(tmp_path)
    db_repo.verify_storage_writable()
    _fake_gallery_entry('overlap', 'source', '1024x1024', 'overlap.png')
    pack = collections.create_gallery_collection('Overlap', asset_alias='overlap')
    collections.add_gallery_collection_items(pack['id'], ['overlap'])
    conversation = agent_repo.create_conversation()
    turn, _ = _turn(conversation['id'], attachment_image_ids=['overlap'], asset_collection_ids=[pack['id']])
    assert agent_repo.list_messages(conversation['id'])[0]['blocks'][0]['explicit_refs'] == ['round-1-input-1']
    agent_repo.finish_turn(turn['id'], 'completed')
    revision = agent_repo.get_conversation(conversation['id'])['branch_revision']
    _turn(conversation['id'], 'turn-0002', action='regenerate', source_turn_id=turn['id'], branch_revision=revision)
    assert agent_repo.list_messages(conversation['id'])[-2]['blocks'][0]['explicit_refs'] == ['round-2-input-1']


def _asset_turn_with_overlap(tmp_path):
    from backend.app.repositories.gallery import collections

    _configure_runtime(tmp_path)
    db_repo.verify_storage_writable()
    for image in ('ref-a', 'ref-b', 'ref-c'):
        _fake_gallery_entry(image, 'source', '1024x1024', f'{image}.png')
    pack = collections.create_gallery_collection('Pack', asset_alias='fox')
    collections.add_gallery_collection_items(pack['id'], ['ref-a', 'ref-b'])
    conversation = agent_repo.create_conversation()
    turn, _ = _turn(conversation['id'], text='Use @fox', attachment_image_ids=['ref-b', 'ref-c'], asset_collection_ids=[pack['id']])
    return conversation, turn, pack


def test_asset_refs_are_stored_relationally_and_blocks_are_synthesized(tmp_path):
    conversation, turn, pack = _asset_turn_with_overlap(tmp_path)
    with db_repo._connect() as conn:
        stored = conn.execute("SELECT blocks_json FROM agent_messages WHERE id = ?", (turn['user_message_id'],)).fetchone()[0]
        members = conn.execute(
            """
            SELECT i.ref_label, a.explicit FROM agent_message_asset_ref_images a
            JOIN agent_message_images i ON i.id = a.input_image_row_id ORDER BY a.position
            """
        ).fetchall()
    assert stored == '[]'
    # Explicit attachments come first (ref-b, ref-c); the pack then appends ref-a.
    assert [(row[0], row[1]) for row in members] == [('round-1-input-3', 0), ('round-1-input-1', 1)]
    block = agent_repo.list_messages(conversation['id'])[0]['blocks'][0]
    assert block['type'] == 'asset_refs' and block['alias'] == 'fox' and block['collection_id'] == pack['id']
    assert block['refs'] == ['round-1-input-3', 'round-1-input-1']
    assert block['explicit_refs'] == ['round-1-input-1']


def test_legacy_asset_blocks_in_blocks_json_are_not_duplicated(tmp_path):
    import json

    conversation, turn, _ = _asset_turn_with_overlap(tmp_path)
    block = agent_repo.list_messages(conversation['id'])[0]['blocks'][0]
    with db_repo._connect() as conn:
        conn.execute("UPDATE agent_messages SET blocks_json = ? WHERE id = ?", (json.dumps([block]), turn['user_message_id']))
        conn.commit()
    assert agent_repo.list_messages(conversation['id'])[0]['blocks'] == [block]
    assert agent_repo.get_message(turn['user_message_id'])['blocks'] == [block]
    assert agent_repo.branch_snapshot(conversation['id'])['messages'][0]['blocks'] == [block]


def test_asset_backfill_restores_legacy_blocks_and_is_idempotent(tmp_path):
    import json
    from backend.app.repositories.db import migrations

    conversation, turn, _ = _asset_turn_with_overlap(tmp_path)
    expected = agent_repo.list_messages(conversation['id'])[0]['blocks'][0]
    legacy = {**expected, 'refs': [*expected['refs'], 'round-9-input-9']}
    with db_repo._connect() as conn:
        conn.execute("DELETE FROM agent_message_asset_refs")
        conn.execute("UPDATE agent_messages SET blocks_json = ? WHERE id = ?", (json.dumps([legacy]), turn['user_message_id']))
        conn.commit()
        for _ in range(2):
            migrations._backfill_agent_asset_refs(conn)
        conn.commit()
        counts = (
            conn.execute("SELECT COUNT(*) FROM agent_message_asset_refs").fetchone()[0],
            conn.execute("SELECT COUNT(*) FROM agent_message_asset_ref_images").fetchone()[0],
        )
    assert counts == (1, 2)
    assert agent_repo.list_messages(conversation['id'])[0]['blocks'] == [expected]


def test_asset_edit_drops_members_whose_gallery_image_was_deleted(tmp_path):
    conversation, turn, _ = _asset_turn_with_overlap(tmp_path)
    agent_repo.finish_turn(turn['id'], 'completed')
    with db_repo._connect() as conn:
        conn.execute("DELETE FROM gallery_entries WHERE id = 'ref-a'")
        conn.commit()
    revision = agent_repo.get_conversation(conversation['id'])['branch_revision']
    _turn(conversation['id'], 'turn-0002', text='Change @fox', action='edit', source_turn_id=turn['id'], branch_revision=revision, attachment_image_ids=['ref-b', 'ref-c'])
    block = agent_repo.list_messages(conversation['id'])[-2]['blocks'][0]
    assert block['refs'] == ['round-2-input-1']
    assert block['explicit_refs'] == ['round-2-input-1']


def test_deleting_a_conversation_removes_asset_ref_rows(tmp_path):
    conversation, _, _ = _asset_turn_with_overlap(tmp_path)
    assert agent_repo.delete_conversation(conversation['id']) is True
    with db_repo._connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM agent_message_asset_refs").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM agent_message_asset_ref_images").fetchone()[0] == 0
