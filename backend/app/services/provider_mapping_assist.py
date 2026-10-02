"""Mapping authoring assistance: prompt text, validation, sample extraction.

Nothing here performs an upstream request; sample extraction only reads JSON
pasted by the operator, and previews are redacted and truncated.
"""

import json
from typing import Any

from pydantic import ValidationError

from ..core.provider_mapping import select_all, select_first
from ..core.provider_mapping_prompt import build_provider_mapping_prompt
from ..core.redaction import redact_sensitive_text
from ..integrations.upstream.async_provider import render_url_template
from ..schemas.provider import ResolvedProviderConfig, resolve_provider_config

_MAX_PREVIEW_CHARS = 160
_MAX_ERRORS = 24


def build_mapping_prompt() -> str:
    return build_provider_mapping_prompt()


def _validation_errors(exc: Exception) -> list[dict]:
    if isinstance(exc, ValidationError):
        issues = []
        for error in exc.errors()[:_MAX_ERRORS]:
            location = ".".join(str(part) for part in error.get("loc", ()))
            issues.append(
                {
                    "path": location or "provider_config",
                    "message": str(error.get("msg") or "invalid value"),
                }
            )
        return issues
    return [
        {
            "path": "provider_config",
            "message": redact_sensitive_text(str(exc) or "invalid provider_config"),
        }
    ]


def _preview(value: Any) -> str:
    if isinstance(value, str):
        text = value
    else:
        try:
            text = json.dumps(value, ensure_ascii=False)
        except (TypeError, ValueError):
            text = str(value)
    return redact_sensitive_text(text)[:_MAX_PREVIEW_CHARS]


def _field(
    found: bool = False,
    value: str | None = None,
    source: str | None = None,
    detail: str | None = None,
) -> dict:
    return {"found": found, "value": value, "source": source, "detail": detail}


def _extract_task_id(source: Any, path: str | None) -> str | None:
    if source is None or not path:
        return None
    value = select_first(source, path)
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _render_template(template: str | None, task_id: str | None) -> str | None:
    if not template or not task_id:
        return None
    try:
        return render_url_template(template, task_id)
    except Exception:  # noqa: BLE001 - preview only
        return None


def _simulate_extraction(
    cfg: ResolvedProviderConfig,
    sample_submit: Any,
    sample_poll: Any,
    sample_result: Any,
) -> dict:
    notes: list[str] = []
    sync_mode = cfg.mode == "sync"
    task_id = _field()
    status_url = _field()
    result_url = _field()
    status_value = _field()

    if sync_mode:
        task_id = _field(False, None, None, "sync mode submits and receives the result in one request")
        status_url = _field(False, None, None, "sync mode has no status endpoint")
        status_value = _field(False, None, None, "sync mode has no status endpoint")

    extracted_task_id = _extract_task_id(sample_submit, None if sync_mode else cfg.poll.task_id_path)
    if not sync_mode and cfg.poll.task_id_path:
        if extracted_task_id:
            task_id = _field(
                True, _preview(extracted_task_id), "submit", cfg.poll.task_id_path
            )
        else:
            task_id = _field(
                False,
                None,
                None,
                f"no task id at {cfg.poll.task_id_path} in the submit sample",
            )

    if not sync_mode and cfg.poll.url_path:
        raw = select_first(sample_submit, cfg.poll.url_path) if sample_submit is not None else None
        if isinstance(raw, str) and raw.strip():
            status_url = _field(
                True, _preview(raw.strip()), "submit", f"poll.url_path {cfg.poll.url_path}"
            )
            if cfg.poll.task_id_path is None:
                task_id = _field(
                    False, None, None, "this mapping reads a status URL, not a task id"
                )
        else:
            status_url = _field(
                False,
                None,
                None,
                f"no status URL at {cfg.poll.url_path} in the submit sample",
            )
    elif not sync_mode:
        rendered = _render_template(cfg.poll.url_template, extracted_task_id)
        if rendered:
            status_url = _field(
                True, rendered, "submit", f"poll.url_template {cfg.poll.url_template}"
            )
        else:
            status_url = _field(
                False,
                None,
                None,
                "waiting for a task id before poll.url_template can render",
            )

    if cfg.result.url_path:
        for label, sample in (("submit", sample_submit), ("poll", sample_poll)):
            raw = select_first(sample, cfg.result.url_path) if sample is not None else None
            if isinstance(raw, str) and raw.strip():
                result_url = _field(
                    True,
                    _preview(raw.strip()),
                    label,
                    f"result.url_path {cfg.result.url_path}",
                )
                break
        else:
            result_url = _field(
                False,
                None,
                None,
                f"no result URL at {cfg.result.url_path} in the samples",
            )
    elif cfg.result.task_id_path and cfg.result.url_template:
        rendered = _render_template(cfg.result.url_template, extracted_task_id)
        if rendered:
            result_url = _field(
                True,
                rendered,
                "submit",
                f"result.url_template {cfg.result.url_template}",
            )
        else:
            result_url = _field(
                False, None, None, "waiting for a task id before result.url_template can render"
            )
    else:
        result_url = _field(
            False,
            None,
            None,
            "images are read from the submit response" if sync_mode
            else "not configured; images are read from the final response",
        )

    if not sync_mode and sample_poll is not None:
        raw = select_first(sample_poll, cfg.poll.status_path)
        value = "" if raw is None else str(raw).strip()
        if value:
            state = (
                "done"
                if value in cfg.poll.done
                else "failed" if value in cfg.poll.failed else "pending"
            )
            status_value = _field(
                True,
                _preview(value),
                "poll",
                f"{state} (done={cfg.poll.done}, failed={cfg.poll.failed})",
            )
        else:
            status_value = _field(
                False, None, None, f"no value at {cfg.poll.status_path} in the poll sample"
            )
    elif not sync_mode:
        status_value = _field(
            False, None, None, "paste a poll response sample to verify the status path"
        )

    image_count: int | None = None
    image_samples: list[str] = []
    if sync_mode:
        source_label, source = ("submit", sample_submit)
    else:
        source_label, source = ("result", sample_result) if sample_result is not None else ("poll", sample_poll)
    if source is not None:
        values = [
            value
            for value in select_all(source, cfg.result.images_path)
            if isinstance(value, str) and value.strip()
        ]
        image_count = len(values)
        image_samples = [_preview(value.strip()) for value in values[:2]]
        if not values:
            notes.append(
                f"no image strings at {cfg.result.images_path} in the {source_label} sample"
            )
    elif sync_mode:
        notes.append("paste a submit response sample to verify image extraction")
    else:
        notes.append("paste a result or poll response sample to verify image extraction")

    return {
        "task_id": task_id,
        "status_url": status_url,
        "result_url": result_url,
        "status_value": status_value,
        "image_count": image_count,
        "image_kind": cfg.result.image_kind,
        "image_samples": image_samples,
        "notes": notes,
    }


def validate_provider_mapping_request(
    provider_config: Any,
    sample_submit: Any = None,
    sample_poll: Any = None,
    sample_result: Any = None,
) -> dict:
    try:
        cfg = resolve_provider_config(provider_config)
    except Exception as exc:  # noqa: BLE001 - surfaced as field errors
        return {
            "valid": False,
            "errors": _validation_errors(exc),
            "extraction": None,
            "capabilities": {},
        }
    extraction = None
    if any(
        sample is not None for sample in (sample_submit, sample_poll, sample_result)
    ):
        extraction = _simulate_extraction(cfg, sample_submit, sample_poll, sample_result)
    return {
        "valid": True,
        "errors": [],
        "extraction": extraction,
        "capabilities": cfg.capabilities.as_dict(),
    }


__all__ = ["build_mapping_prompt", "validate_provider_mapping_request"]
