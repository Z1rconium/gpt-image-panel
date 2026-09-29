"""Image reference syntax shared by the Agent prompt, context and tools.

A reference names an image inside one conversation: ``round-N-image-M`` for
the M-th image the Agent produced in round N, ``round-N-input-K`` for the K-th
image the user attached in round N. Users type ``@round-N-image-M`` (or the
Chinese ``@第N轮图M``); the model sees and writes ``<ref id="..."/>`` tags.
"""

import re

LABEL_PATTERN = r"round-\d+-(?:image|input)-\d+"
LABEL_RE = re.compile(rf"^{LABEL_PATTERN}$")
MENTION_RE = re.compile(rf"@({LABEL_PATTERN})|@第?(\d+)轮图(\d+)")
REF_TAG_RE = re.compile(r"<ref\s+id=\"(" + LABEL_PATTERN + r")\"\s*/>")
_ANY_REF_TAG_RE = re.compile(r"<(?:removed_)?ref\b[^>]*>")
_TAG_HEADS = ("<ref", "<removed_ref")
_MAX_HELD_TAG_CHARS = 200
PROMPT_SUMMARY_CHARS = 1200


def output_label(round_no: int, image_index: int) -> str:
    return f"round-{round_no}-image-{image_index}"


def input_label(round_no: int, image_index: int) -> str:
    return f"round-{round_no}-input-{image_index}"


def is_valid_label(value: str) -> bool:
    return bool(LABEL_RE.match(str(value or "")))


def to_ref_tag(label: str) -> str:
    return f'<ref id="{label}"/>'


def to_removed_ref_tag(label: str) -> str:
    return f'<removed_ref id="{label}"/>'


def _mention_label(match: re.Match[str]) -> str:
    if match.group(1):
        return match.group(1)
    return output_label(int(match.group(2)), int(match.group(3)))


def parse_user_mentions(text: str) -> list[str]:
    """Labels mentioned in a user message, in first-appearance order."""
    labels: list[str] = []
    for match in MENTION_RE.finditer(str(text or "")):
        label = _mention_label(match)
        if label not in labels:
            labels.append(label)
    return labels


def rewrite_mentions_to_ref_tags(text: str) -> str:
    return MENTION_RE.sub(lambda match: to_ref_tag(_mention_label(match)), str(text or ""))


def extract_ref_tags(prompt: str) -> list[str]:
    labels: list[str] = []
    for match in REF_TAG_RE.finditer(str(prompt or "")):
        if match.group(1) not in labels:
            labels.append(match.group(1))
    return labels


def strip_ref_tags(text: str) -> str:
    return _ANY_REF_TAG_RE.sub("", str(text or ""))


def truncate_prompt(prompt: str, limit: int = PROMPT_SUMMARY_CHARS) -> str:
    collapsed = " ".join(str(prompt or "").split())
    if len(collapsed) <= limit:
        return collapsed
    return collapsed[: max(0, limit - 1)].rstrip() + "…"


class RefTagStripper:
    """Removes ``<ref/>`` and ``<removed_ref/>`` tags from streamed text.

    A tag may arrive split across deltas, so a possible tag prefix at the end of
    the buffer is held back until the next delta shows whether it is a tag.
    """

    def __init__(self) -> None:
        self._held = ""

    @staticmethod
    def _could_be_tag_start(fragment: str) -> bool:
        return any(head.startswith(fragment) or fragment.startswith(head) for head in _TAG_HEADS)

    def feed(self, delta: str) -> str:
        buffer = self._held + str(delta or "")
        self._held = ""
        out: list[str] = []
        position = 0
        while position < len(buffer):
            start = buffer.find("<", position)
            if start == -1:
                out.append(buffer[position:])
                break
            out.append(buffer[position:start])
            fragment = buffer[start:]
            close = fragment.find(">")
            if close != -1:
                candidate = fragment[: close + 1]
                if _ANY_REF_TAG_RE.fullmatch(candidate):
                    position = start + close + 1
                    continue
                out.append("<")
                position = start + 1
                continue
            if len(fragment) <= _MAX_HELD_TAG_CHARS and self._could_be_tag_start(fragment):
                self._held = fragment
                break
            out.append("<")
            position = start + 1
        return "".join(out)

    def flush(self) -> str:
        held, self._held = self._held, ""
        return held
