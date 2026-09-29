"""Validation of tool-call arguments the model produced."""

import json
import re
from dataclasses import dataclass
from typing import Any

from ..core import settings as config
from .agent_prompt import (
    IMAGE_ID_PATTERN,
    MAX_CONTINUE_REASON_CHARS,
    TOOL_CONTINUE_GENERATION,
    TOOL_GENERATE_IMAGE_BATCH,
)

_IMAGE_ID_RE = re.compile(IMAGE_ID_PATTERN)


class ToolArgumentError(ValueError):
    """The model sent arguments the tool cannot use; the message is fed back to it."""


@dataclass(frozen=True)
class BatchImage:
    id: str
    prompt: str


@dataclass(frozen=True)
class ContinueRequest:
    reason: str


def _load_object(arguments_json: str) -> dict[str, Any]:
    try:
        data = json.loads(arguments_json or "{}")
    except (TypeError, ValueError) as e:
        raise ToolArgumentError("Arguments are not valid JSON") from e
    if not isinstance(data, dict):
        raise ToolArgumentError("Arguments must be a JSON object")
    return data


def parse_batch_arguments(arguments_json: str) -> list[BatchImage]:
    data = _load_object(arguments_json)
    raw_images = data.get("images")
    if not isinstance(raw_images, list) or not raw_images:
        raise ToolArgumentError("'images' must be a non-empty array")
    if len(raw_images) > config.AGENT_MAX_IMAGES_PER_BATCH:
        raise ToolArgumentError(
            f"At most {config.AGENT_MAX_IMAGES_PER_BATCH} images per call; split the rest into a later round"
        )
    images: list[BatchImage] = []
    seen: set[str] = set()
    for index, raw in enumerate(raw_images, start=1):
        if not isinstance(raw, dict):
            raise ToolArgumentError(f"images[{index}] must be an object")
        image_id = raw.get("id")
        prompt = raw.get("prompt")
        if not isinstance(image_id, str) or not _IMAGE_ID_RE.match(image_id):
            raise ToolArgumentError(
                f"images[{index}].id must be 1-64 characters of letters, digits, '_' or '-'"
            )
        if image_id in seen:
            raise ToolArgumentError(f"Duplicate image id '{image_id}'")
        if not isinstance(prompt, str) or not prompt.strip():
            raise ToolArgumentError(f"images[{index}].prompt must be a non-empty string")
        if len(prompt) > config.AGENT_MAX_IMAGE_PROMPT_CHARS:
            raise ToolArgumentError(
                f"images[{index}].prompt exceeds {config.AGENT_MAX_IMAGE_PROMPT_CHARS} characters"
            )
        seen.add(image_id)
        images.append(BatchImage(id=image_id, prompt=prompt.strip()))
    return images


def parse_continue_arguments(arguments_json: str) -> ContinueRequest:
    data = _load_object(arguments_json)
    reason = data.get("reason")
    if not isinstance(reason, str):
        raise ToolArgumentError("'reason' must be a string")
    return ContinueRequest(reason=" ".join(reason.split())[:MAX_CONTINUE_REASON_CHARS])


def parse_tool_arguments(name: str, arguments_json: str) -> list[BatchImage] | ContinueRequest:
    if name == TOOL_GENERATE_IMAGE_BATCH:
        return parse_batch_arguments(arguments_json)
    if name == TOOL_CONTINUE_GENERATION:
        return parse_continue_arguments(arguments_json)
    raise ToolArgumentError(f"Unknown tool '{name}'")
