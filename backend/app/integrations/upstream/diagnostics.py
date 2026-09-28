from ...core.api_paths import CHAT_COMPLETIONS_API_PATH, RESPONSES_API_PATH
from ...schemas.generation import GenerateRequest

PROMPT_ONLY_API_PATHS = {RESPONSES_API_PATH, CHAT_COMPLETIONS_API_PATH}


def _normalized_text(value: str | None) -> str:
    return " ".join(str(value or "").split()).casefold()


def _normalized_size(value: str | None) -> str:
    return str(value or "").strip().lower().replace("×", "x").replace(" ", "")


def image_diagnostics(
    payload: GenerateRequest,
    api_path: str,
    *,
    prompt_guard: bool,
    sent_prompt: str,
    revised_prompt: str | None = None,
    reported_size: str | None = None,
    reported_quality: str | None = None,
    actual_size: tuple[int, int] | None = None,
) -> list[str]:
    """Flag upstream behaviour that silently diverged from the request."""
    codes: list[str] = []
    revised = _normalized_text(revised_prompt)
    if revised and revised not in {_normalized_text(payload.prompt), _normalized_text(sent_prompt)}:
        codes.append("prompt_rewritten_despite_guard" if prompt_guard else "prompt_rewritten")

    if api_path in PROMPT_ONLY_API_PATHS:
        # These builders only forward prompt + model, so size/quality/format
        # choices never reach upstream; comparing reported values is moot.
        if (
            payload.size != "auto"
            or payload.quality != "auto"
            or payload.output_format != "png"
            or payload.background != "auto"
        ):
            codes.append("params_not_sent")
        return codes

    requested_size = _normalized_size(payload.size)
    if requested_size and requested_size != "auto":
        reported = _normalized_size(reported_size)
        actual = f"{actual_size[0]}x{actual_size[1]}" if actual_size else ""
        if (reported and reported != "auto" and reported != requested_size) or (
            actual and actual != requested_size
        ):
            codes.append("size_ignored")

    requested_quality = str(payload.quality or "").strip().lower()
    reported_q = str(reported_quality or "").strip().lower()
    if requested_quality and requested_quality != "auto" and reported_q and reported_q != "auto":
        if reported_q != requested_quality:
            codes.append("quality_ignored")
    return codes
