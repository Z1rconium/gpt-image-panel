"""System prompt and tool definitions for the Agent."""

from ..core import settings as config
from ..integrations.agent_client import ToolSpec

TOOL_GENERATE_IMAGE_BATCH = "generate_image_batch"
TOOL_CONTINUE_GENERATION = "continue_generation"
IMAGE_ID_PATTERN = r"^[A-Za-z0-9_-]{1,64}$"
MAX_CONTINUE_REASON_CHARS = 300

_INSTRUCTIONS = """\
You are an image-creation agent inside an image workspace. You talk with the user and create or edit images by calling tools. Reply in the user's language.

Tools:
- {generate}: creates images. Put every image that does not depend on another new image in ONE call (at most {max_batch} images per call). To edit or build on an existing image, put <ref id="..."/> for it inside that image's prompt; the system attaches the actual picture for you. A prompt with no <ref/> creates a new image from scratch.
- {continue_}: call it after a batch when the next images depend on images that did not exist before (for example a character sheet first, then scenes that reuse the character). Then call {generate} again with prompts that use the new <ref/> tags. Do not call it when nothing else is left to create.

Rules:
- Each image prompt must be complete and self-contained: subject, style, composition, text to render. Do not rely on earlier prompts.
- Only use <ref id="..."/> ids that appear in the conversation. Ignore images marked <removed_ref .../>; they are deleted.
- Never write <ref .../> or image ids in your visible reply. In prose, describe images naturally ("the second image from round 2").
- At most {max_images_per_turn} images can be created per user turn, and at most {max_rounds} tool rounds. Stop calling tools once the request is satisfied, then give a short summary of what you made.
- If a tool result reports an error for an image, fix the prompt or references and retry once, or explain the problem briefly.
- Ask a short clarifying question instead of generating when the request is genuinely ambiguous.\
"""


def build_instructions(*, max_rounds: int, user_preferences: str = "") -> str:
    text = _INSTRUCTIONS.format(
        generate=TOOL_GENERATE_IMAGE_BATCH,
        continue_=TOOL_CONTINUE_GENERATION,
        max_batch=config.AGENT_MAX_IMAGES_PER_BATCH,
        max_images_per_turn=config.AGENT_MAX_IMAGES_PER_TURN,
        max_rounds=max_rounds,
    )
    preferences = str(user_preferences or "").strip()
    if preferences:
        text += (
            "\n\nUser preferences (follow them unless they conflict with the rules above):\n"
            + preferences
        )
    return text


def build_tools() -> list[ToolSpec]:
    return [
        ToolSpec(
            name=TOOL_GENERATE_IMAGE_BATCH,
            description=(
                "Create one or more images concurrently. Use <ref id=\"...\"/> inside an "
                "image prompt to edit or build on an existing image."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "images": {
                        "type": "array",
                        "minItems": 1,
                        "maxItems": config.AGENT_MAX_IMAGES_PER_BATCH,
                        "items": {
                            "type": "object",
                            "properties": {
                                "id": {
                                    "type": "string",
                                    "description": "Short unique id for this image within the call, e.g. hero or scene_1.",
                                },
                                "prompt": {
                                    "type": "string",
                                    "description": "Complete image prompt.",
                                },
                            },
                            "required": ["id", "prompt"],
                            "additionalProperties": False,
                        },
                    }
                },
                "required": ["images"],
                "additionalProperties": False,
            },
        ),
        ToolSpec(
            name=TOOL_CONTINUE_GENERATION,
            description=(
                "Start another tool round because the next images depend on images "
                "created in this one."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "reason": {
                        "type": "string",
                        "description": "One short sentence: what depends on what.",
                    }
                },
                "required": ["reason"],
                "additionalProperties": False,
            },
        ),
    ]
