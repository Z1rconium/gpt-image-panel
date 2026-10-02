"""Builds the model input for one Agent turn from stored conversation state."""

import asyncio
import logging
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from ..core import settings as config
from ..core.media import safe_image_path
from ..integrations import assistant_client
from ..integrations.agent_client import AgentItem, AssistantTextItem, UserItem
from ..repositories.gallery.queries import get_gallery_entry
from ..runtime.blocking import run_db_operation, run_file_operation
from . import vision_previews
from .agent_refs import (
    parse_user_mentions,
    rewrite_mentions_to_ref_tags,
    to_ref_tag,
    to_removed_ref_tag,
    truncate_prompt,
)

logger = logging.getLogger(__name__)

MAX_HISTORY_CHARS = 24000
MAX_INPUT_IMAGE_BYTES = 6 * 1024 * 1024
MAX_RESULT_IMAGES_PER_ROUND = 4
PREVIEW_LOAD_CONCURRENCY = 3
_ERROR_SNIPPET_CHARS = 200


def _is_live(image: dict[str, Any]) -> bool:
    return image["status"] == "succeeded" and bool(image.get("image_id"))


def format_user_text(text: str, attachments: Sequence[dict[str, Any]]) -> str:
    lines = [rewrite_mentions_to_ref_tags(text)]
    for image in sorted(attachments, key=lambda item: item["image_index"]):
        tag = to_ref_tag(image["ref_label"]) if _is_live(image) else to_removed_ref_tag(image["ref_label"])
        lines.append(f"[attached: {tag}]")
    return "\n".join(line for line in lines if line)


def format_assistant_text(text: str, outputs: Sequence[dict[str, Any]]) -> str:
    lines = [str(text or "").strip()]
    for image in sorted(outputs, key=lambda item: item["image_index"]):
        label = image["ref_label"]
        if _is_live(image):
            lines.append(f"Generated {to_ref_tag(label)} (prompt: {truncate_prompt(image['prompt'])})")
        elif image["status"] == "succeeded":
            lines.append(f"{to_removed_ref_tag(label)} (this image was deleted)")
        elif image["status"] == "failed":
            error = truncate_prompt(image.get("error") or "unknown error", _ERROR_SNIPPET_CHARS)
            lines.append(f"[{label} failed: {error}]")
        else:
            lines.append(f"[{label} was not completed]")
    return "\n".join(line for line in lines if line)


def build_history_items(
    *,
    messages: Sequence[dict[str, Any]],
    image_refs: Sequence[dict[str, Any]],
    current_round: int,
    max_chars: int = MAX_HISTORY_CHARS,
) -> list[AgentItem]:
    """Text history: earlier rounds (newest kept within budget) plus the current user message."""
    rounds: dict[int, dict[str, dict[str, Any]]] = {}
    for message in messages:
        if int(message["round_no"]) > current_round:
            continue
        rounds.setdefault(int(message["round_no"]), {})[message["role"]] = message

    per_round: list[tuple[int, list[AgentItem]]] = []
    for round_no in sorted(rounds):
        pair = rounds[round_no]
        items: list[AgentItem] = []
        user = pair.get("user")
        if user is not None:
            attachments = [
                image for image in image_refs
                if image["role"] == "input" and image["message_id"] == user["id"]
            ]
            items.append(UserItem(text=format_user_text(user["text"], attachments)))
        assistant = pair.get("assistant")
        if assistant is not None and round_no < current_round:
            outputs = [
                image for image in image_refs
                if image["role"] == "output" and image["message_id"] == assistant["id"]
            ]
            text = format_assistant_text(assistant["text"], outputs)
            sources = [source for block in assistant.get("blocks", []) if block.get("type") == "sources" for source in block.get("sources", [])][:20]
            if sources:
                text += "\nSources used in that reply:\n" + "\n".join(f"{str(source.get('title') or '')[:300]}: {str(source.get('url') or '')[:2048]}" for source in sources)
            if text:
                items.append(AssistantTextItem(text=text))
        per_round.append((round_no, items))

    kept: list[list[AgentItem]] = []
    used = 0
    for round_no, items in reversed(per_round):
        size = sum(len(getattr(item, "text", "")) for item in items)
        if round_no != current_round and kept and used + size > max_chars:
            break
        kept.append(items)
        used += size
    result: list[AgentItem] = []
    for items in reversed(kept):
        result.extend(items)
    return result


def select_visual_context(
    *,
    current_text: str,
    current_round: int,
    image_refs: Sequence[dict[str, Any]],
    max_images: int | None = None,
) -> list[dict[str, Any]]:
    """Pick the images to show the model: mentioned, attached now, then newest outputs."""
    limit = config.AGENT_MAX_HISTORY_IMAGES if max_images is None else max_images
    if limit <= 0:
        return []
    live = {image["ref_label"]: image for image in image_refs if _is_live(image)}
    chosen: list[dict[str, Any]] = []

    def add(image: dict[str, Any] | None) -> None:
        if image is not None and image not in chosen and len(chosen) < limit:
            chosen.append(image)

    for label in parse_user_mentions(current_text):
        add(live.get(label))
    current_inputs = sorted(
        (image for image in live.values() if image["role"] == "input" and image["round_no"] == current_round),
        key=lambda image: image["image_index"],
    )
    for image in current_inputs:
        add(image)
    outputs = sorted(
        (image for image in live.values() if image["role"] == "output"),
        key=lambda image: (image["round_no"], image["image_index"]),
        reverse=True,
    )
    for image in outputs:
        add(image)
    return chosen


def describe_images(images: Sequence[dict[str, Any]], *, intro: str) -> str:
    entries = "; ".join(
        f"image {index} = {to_ref_tag(image['ref_label'])}" for index, image in enumerate(images, start=1)
    )
    return f"{intro} {entries}"


async def load_preview_data_urls(
    images: Sequence[dict[str, Any]],
    *,
    max_total_bytes: int = MAX_INPUT_IMAGE_BYTES,
    max_images: int | None = None,
) -> list[tuple[dict[str, Any], str]]:
    """Downscaled data URLs for gallery images; unreadable or over-budget ones are skipped."""
    async def load(image: dict[str, Any]) -> tuple[str, int] | None:
        image_id = image.get("image_id")
        if not image_id:
            return None
        try:
            entry = await run_db_operation(
                get_gallery_entry, image_id, metric_name="agent_get_gallery_entry"
            )
            if entry is None:
                return None
            path = await run_file_operation(safe_image_path, entry.filename)
            if not path:
                return None
            return await vision_previews.load_preview_data_url(Path(path))
        except (assistant_client.AssistantError, OSError, ValueError):
            logger.warning("Agent could not load image %s as visual context", image.get("ref_label"))
            return None

    loaded: list[tuple[dict[str, Any], str]] = []
    total = 0
    if max_total_bytes <= 0 or (max_images is not None and max_images <= 0):
        return loaded
    pending: dict[int, asyncio.Task] = {}
    next_index = 0
    try:
        for index, image in enumerate(images):
            window = PREVIEW_LOAD_CONCURRENCY
            if max_images is not None:
                window = min(window, max_images - len(loaded))
            while next_index < len(images) and len(pending) < window:
                pending[next_index] = asyncio.create_task(load(images[next_index]))
                next_index += 1
            preview = await pending.pop(index)
            if preview is None:
                continue
            data_url, size = preview
            if total + size > max_total_bytes:
                break
            total += size
            loaded.append((image, data_url))
            if total >= max_total_bytes or (max_images is not None and len(loaded) >= max_images):
                break
    finally:
        for task in pending.values():
            task.cancel()
        await asyncio.gather(*pending.values(), return_exceptions=True)
    return loaded


def build_current_user_item(
    *,
    history_item: UserItem,
    visuals: Sequence[tuple[dict[str, Any], str]],
) -> UserItem:
    """Merge the visual context into the current user message so the model sees one user turn."""
    if not visuals:
        return history_item
    intro = describe_images(
        [image for image, _url in visuals],
        intro="Images shown with this message, in order:",
    )
    return UserItem(text=f"{history_item.text}\n\n{intro}", images=tuple(url for _image, url in visuals))


def build_result_images_item(visuals: Sequence[tuple[dict[str, Any], str]]) -> UserItem | None:
    if not visuals:
        return None
    shown = list(visuals)[:MAX_RESULT_IMAGES_PER_ROUND]
    intro = describe_images(
        [image for image, _url in shown],
        intro="These are the images the tool just created, in order:",
    )
    return UserItem(text=intro, images=tuple(url for _image, url in shown))
