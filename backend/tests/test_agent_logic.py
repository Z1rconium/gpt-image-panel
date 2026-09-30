import json
from pathlib import Path

from backend.app.integrations.agent_client import AssistantTextItem, UserItem
from backend.app.services import agent_context, agent_prompt, agent_refs, agent_tools, vision_previews
from backend.tests.support.contract import *  # noqa: F403


# ── agent_refs ──────────────────────────────────────────────────


def test_mentions_parse_both_syntaxes_and_rewrite_to_tags():
    text = "Use @round-1-image-2 and @第3轮图1, again @round-1-image-2 and @round-2-input-1."
    assert agent_refs.parse_user_mentions(text) == [
        "round-1-image-2",
        "round-3-image-1",
        "round-2-input-1",
    ]
    rewritten = agent_refs.rewrite_mentions_to_ref_tags(text)
    assert '<ref id="round-1-image-2"/>' in rewritten and "@" not in rewritten
    assert agent_refs.extract_ref_tags(rewritten) == ["round-1-image-2", "round-3-image-1", "round-2-input-1"]
    assert agent_refs.parse_user_mentions("email me@example.com or @round-x-image-1") == []


def test_ref_tag_helpers():
    assert agent_refs.is_valid_label("round-12-image-3") and agent_refs.is_valid_label("round-1-input-1")
    assert not agent_refs.is_valid_label("round-1-video-1") and not agent_refs.is_valid_label("../x")
    assert agent_refs.strip_ref_tags('a <ref id="round-1-image-1"/> b <removed_ref id="round-1-image-2"/>c') == "a  b c"
    assert agent_refs.extract_ref_tags('<ref id="bad id"/><ref id="round-1-image-1" />') == ["round-1-image-1"]
    assert agent_refs.truncate_prompt("a  b\nc", 100) == "a b c"
    assert len(agent_refs.truncate_prompt("x" * 5000, 1200)) == 1200


@pytest.mark.parametrize(
    "deltas",
    [
        ['Here you go <ref id="round-1-image-1"/> enjoy'],
        ["Here you go <re", 'f id="round-1-image-1"/> enjoy'],
        ["Here you go <", 'ref id="round-1-image-1"', "/> enjoy"],
        ["Here you go ", "<", "r", "e", "f", ' id="round-1-image-1"/', "> enjoy"],
    ],
)
def test_ref_tag_stripper_across_delta_boundaries(deltas):
    stripper = agent_refs.RefTagStripper()
    out = "".join(stripper.feed(delta) for delta in deltas) + stripper.flush()
    assert out == "Here you go  enjoy"


def test_ref_tag_stripper_keeps_ordinary_angle_brackets():
    stripper = agent_refs.RefTagStripper()
    out = stripper.feed("1 < 2 and <b>bold</b> <removed_ref id=\"round-1-image-1\"/>done <")
    out += stripper.feed(" end")
    out += stripper.flush()
    assert out == "1 < 2 and <b>bold</b> done < end"
    stripper = agent_refs.RefTagStripper()
    assert stripper.feed("trailing <ref") + stripper.flush() == "trailing <ref"


# ── agent_tools ─────────────────────────────────────────────────


def test_batch_arguments_validation(tmp_path):
    _configure_runtime(tmp_path)  # noqa: F405
    ok = agent_tools.parse_batch_arguments(json.dumps({"images": [{"id": "hero", "prompt": " A fox "}]}))
    assert ok == [agent_tools.BatchImage("hero", "A fox")]
    bad_payloads = [
        "not json",
        "[]",
        "{}",
        json.dumps({"images": []}),
        json.dumps({"images": [{"id": "bad id", "prompt": "x"}]}),
        json.dumps({"images": [{"id": "a", "prompt": "x"}, {"id": "a", "prompt": "y"}]}),
        json.dumps({"images": [{"id": "a", "prompt": "  "}]}),
        json.dumps({"images": [{"id": "a", "prompt": "x" * (config.AGENT_MAX_IMAGE_PROMPT_CHARS + 1)}]}),
        json.dumps({"images": [{"id": f"i{n}", "prompt": "x"} for n in range(config.AGENT_MAX_IMAGES_PER_BATCH + 1)]}),
    ]
    for payload in bad_payloads:
        with pytest.raises(agent_tools.ToolArgumentError):
            agent_tools.parse_batch_arguments(payload)


def test_continue_arguments_and_dispatch(tmp_path):
    _configure_runtime(tmp_path)  # noqa: F405
    request = agent_tools.parse_tool_arguments(agent_prompt.TOOL_CONTINUE_GENERATION, '{"reason": "needs  the\\nhero"}')
    assert request == agent_tools.ContinueRequest("needs the hero")
    with pytest.raises(agent_tools.ToolArgumentError):
        agent_tools.parse_tool_arguments(agent_prompt.TOOL_CONTINUE_GENERATION, "{}")
    with pytest.raises(agent_tools.ToolArgumentError, match="Unknown tool"):
        agent_tools.parse_tool_arguments("delete_everything", "{}")


def test_prompt_and_tool_schemas(tmp_path):
    _configure_runtime(tmp_path)  # noqa: F405
    instructions = agent_prompt.build_instructions(max_rounds=3, user_preferences="Prefer watercolor.")
    assert "at most 3 tool rounds" in instructions and "Prefer watercolor." in instructions
    assert "User preferences" not in agent_prompt.build_instructions(max_rounds=3)
    tools = {tool.name: tool for tool in agent_prompt.build_tools()}
    assert set(tools) == {"generate_image_batch", "continue_generation"}
    batch = tools["generate_image_batch"].parameters
    assert batch["properties"]["images"]["maxItems"] == config.AGENT_MAX_IMAGES_PER_BATCH
    assert batch["additionalProperties"] is False


# ── agent_context ───────────────────────────────────────────────


def _message(message_id, round_no, role, text=""):
    return {"id": message_id, "round_no": round_no, "role": role, "text": text}


def _image(round_no, index, role, message_id, *, status="succeeded", image_id="g", prompt="p", error=None):
    label = f"round-{round_no}-{'image' if role == 'output' else 'input'}-{index}"
    return {
        "ref_label": label, "round_no": round_no, "image_index": index, "role": role,
        "message_id": message_id, "status": status,
        "image_id": image_id if status == "succeeded" else None, "prompt": prompt, "error": error,
    }


def _conversation():
    messages = [
        _message("u1", 1, "user", "draw a fox"),
        _message("a1", 1, "assistant", "Here are two foxes."),
        _message("u2", 2, "user", "@round-1-image-1 in snow"),
        _message("a2", 2, "assistant", ""),
    ]
    images = [
        _image(1, 1, "output", "a1", image_id="g1", prompt="a red fox"),
        # Succeeded but the gallery image was deleted afterwards: no image_id.
        {**_image(1, 2, "output", "a1", prompt="a gray fox"), "image_id": None},
        _image(1, 3, "output", "a1", status="failed", error="blocked by safety"),
        _image(2, 1, "input", "u2", image_id="g9"),
    ]
    return messages, images


def test_history_rewrites_mentions_and_marks_removed_images():
    messages, images = _conversation()
    items = agent_context.build_history_items(messages=messages, image_refs=images, current_round=2)
    assert [type(item) for item in items] == [UserItem, AssistantTextItem, UserItem]
    assert items[0].text == "draw a fox"
    assistant = items[1].text
    assert "Here are two foxes." in assistant
    assert 'Generated <ref id="round-1-image-1"/> (prompt: a red fox)' in assistant
    assert '<removed_ref id="round-1-image-2"/>' in assistant
    assert "[round-1-image-3 failed: blocked by safety]" in assistant
    assert items[2].text == '<ref id="round-1-image-1"/> in snow\n[attached: <ref id="round-2-input-1"/>]'


def test_history_budget_drops_oldest_rounds_but_keeps_current():
    messages = [
        _message("u1", 1, "user", "x" * 500), _message("a1", 1, "assistant", "y" * 500),
        _message("u2", 2, "user", "x" * 500), _message("a2", 2, "assistant", "y" * 500),
        _message("u3", 3, "user", "current"), _message("a3", 3, "assistant", ""),
    ]
    items = agent_context.build_history_items(messages=messages, image_refs=[], current_round=3, max_chars=1200)
    texts = [item.text for item in items]
    assert texts[-1] == "current" and len(items) == 3 and texts[0] == "x" * 500
    only_current = agent_context.build_history_items(messages=messages, image_refs=[], current_round=3, max_chars=1)
    assert [item.text for item in only_current] == ["current"]


def test_select_visual_context_priority_and_limit():
    messages, images = _conversation()
    picked = agent_context.select_visual_context(
        current_text="@round-1-image-1 in snow", current_round=2, image_refs=images, max_images=8
    )
    assert [image["ref_label"] for image in picked] == ["round-1-image-1", "round-2-input-1"]
    assert agent_context.select_visual_context(current_text="", current_round=2, image_refs=images, max_images=1)[0][
        "ref_label"
    ] == "round-2-input-1"
    assert agent_context.select_visual_context(current_text="", current_round=2, image_refs=images, max_images=0) == []
    # Mentions of deleted or unknown images are ignored.
    assert agent_context.select_visual_context(
        current_text="@round-1-image-2 @round-9-image-9", current_round=2, image_refs=[images[1]], max_images=4
    ) == []


def test_current_user_item_merges_visuals_and_result_item_caps_images():
    _messages, images = _conversation()
    base = UserItem(text="hello")
    assert agent_context.build_current_user_item(history_item=base, visuals=[]) is base
    merged = agent_context.build_current_user_item(
        history_item=base, visuals=[(images[0], "data:image/png;base64,AAA")]
    )
    assert merged.images == ("data:image/png;base64,AAA",)
    assert 'image 1 = <ref id="round-1-image-1"/>' in merged.text and merged.text.startswith("hello")
    assert agent_context.build_result_images_item([]) is None
    many = [(_image(1, n, "output", "a1"), f"data:image/png;base64,{n}") for n in range(1, 8)]
    assert len(agent_context.build_result_images_item(many).images) == agent_context.MAX_RESULT_IMAGES_PER_ROUND


@pytest.mark.anyio
async def test_load_preview_data_urls_reads_gallery_images(tmp_path):
    _configure_runtime(tmp_path)  # noqa: F405
    db_repo.verify_storage_writable()  # noqa: F405
    _fake_gallery_entry("ctx-1", "p", "1024x1024", "ctx-1.png")  # noqa: F405
    rows = [
        _image(1, 1, "output", "a1", image_id="ctx-1"),
        _image(1, 2, "output", "a1", image_id="missing-in-gallery"),
        _image(1, 3, "output", "a1", image_id=None),
    ]
    loaded = await agent_context.load_preview_data_urls(rows)
    assert [row["ref_label"] for row, _url in loaded] == ["round-1-image-1"]
    assert loaded[0][1].startswith("data:image/")
    assert await agent_context.load_preview_data_urls(rows, max_total_bytes=1) == []


@pytest.mark.anyio
async def test_load_preview_data_urls_reuses_cached_previews(tmp_path, monkeypatch):
    _configure_runtime(tmp_path)  # noqa: F405
    db_repo.verify_storage_writable()  # noqa: F405
    entry = _fake_gallery_entry("ctx-cache-1", "p", "1024x1024", "ctx-cache-1.png")  # noqa: F405
    rows = [_image(1, 1, "output", "a1", image_id="ctx-cache-1")]

    first = await agent_context.load_preview_data_urls(rows)
    assert len(first) == 1
    image_path = (Path(config.IMAGES_DIR) / entry.filename).resolve()
    assert (await vision_previews.load_preview_data_url(image_path))[0] == first[0][1]

    calls = {"count": 0}
    real_prepare = vision_previews.assistant_client.prepare_vision_preview

    def counting_prepare(path):
        calls["count"] += 1
        return real_prepare(path)

    monkeypatch.setattr(vision_previews.assistant_client, "prepare_vision_preview", counting_prepare)
    second = await agent_context.load_preview_data_urls(rows)
    assert second == first
    assert calls["count"] == 0
