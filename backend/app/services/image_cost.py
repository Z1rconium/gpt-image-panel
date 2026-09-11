"""Usage normalization and USD cost estimation for image generation/edit jobs.

Upstream services proxied by this app are "OpenAI-compatible" but not
necessarily OpenAI itself, so per-token pricing is inherently provider
specific. This module never invents a cost: when the upstream response has no
`usage` object, or the model has no configured rate, callers get back an
explicit "incomplete" estimate with a `reason` instead of a fabricated $0.
"""

from __future__ import annotations

import json
import logging
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any, TypedDict

from ..core import settings as config

logger = logging.getLogger(__name__)

COST_DECIMAL_PLACES = Decimal("0.000001")

# Builtin USD-per-1,000,000-token rates. Sourced from OpenAI's published Images
# API pricing for gpt-image-1. Third-party upstreams rarely match this exactly;
# override or extend via IMAGE_COST_RATES_JSON (see .env.example).
PRICING_VERSION = "builtin-2026-01"
BUILTIN_RATES_PER_MILLION: dict[str, dict[str, float]] = {
    "gpt-image-1": {
        "text_input_per_million": 5.0,
        "image_input_per_million": 10.0,
        "image_output_per_million": 40.0,
    },
}

_RATE_FIELDS = (
    "text_input_per_million",
    "image_input_per_million",
    "image_output_per_million",
)


class NormalizedUsage(TypedDict):
    raw: dict[str, Any]
    input_tokens: int | None
    output_tokens: int | None
    text_input_tokens: int | None
    image_input_tokens: int | None
    image_output_tokens: int | None
    total_tokens: int | None
    available: bool


class CostEstimate(TypedDict):
    currency: str
    estimated_cost_usd: float | None
    rate_source: str  # "builtin" | "env" | "unknown" | "mixed"
    pricing_model: str | None
    complete: bool
    reason: str | None


def _coerce_nonneg_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        try:
            number = int(float(value))
        except (TypeError, ValueError):
            return None
    if number < 0 or number > 10**12:
        return None
    return number


def normalize_usage(raw_usage: Any) -> NormalizedUsage | None:
    """Best-effort normalization of an upstream `usage` object.

    Returns None when no usable usage payload was provided. Unknown or
    malformed fields are preserved in `raw` but never used to invent numbers.
    Handles the Images/Responses API shape (input_tokens/output_tokens plus
    *_tokens_details) and the Chat Completions shape (prompt_tokens/
    completion_tokens).
    """
    if not isinstance(raw_usage, dict) or not raw_usage:
        return None

    details_in = raw_usage.get("input_tokens_details")
    details_out = raw_usage.get("output_tokens_details")
    text_input = (
        _coerce_nonneg_int(details_in.get("text_tokens"))
        if isinstance(details_in, dict)
        else None
    )
    image_input = (
        _coerce_nonneg_int(details_in.get("image_tokens"))
        if isinstance(details_in, dict)
        else None
    )
    image_output = (
        _coerce_nonneg_int(details_out.get("image_tokens"))
        if isinstance(details_out, dict)
        else None
    )

    input_tokens = _coerce_nonneg_int(raw_usage.get("input_tokens"))
    if input_tokens is None:
        input_tokens = _coerce_nonneg_int(raw_usage.get("prompt_tokens"))
    output_tokens = _coerce_nonneg_int(raw_usage.get("output_tokens"))
    if output_tokens is None:
        output_tokens = _coerce_nonneg_int(raw_usage.get("completion_tokens"))

    total_tokens = _coerce_nonneg_int(raw_usage.get("total_tokens"))
    if total_tokens is None and input_tokens is not None and output_tokens is not None:
        total_tokens = input_tokens + output_tokens

    if image_output is None and output_tokens is not None and text_input is None and image_input is None:
        # Image generation/edit responses have no text output component, so an
        # upstream that reports output_tokens without a breakdown means it's
        # entirely image tokens.
        image_output = output_tokens

    return {
        "raw": raw_usage,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "text_input_tokens": text_input,
        "image_input_tokens": image_input,
        "image_output_tokens": image_output,
        "total_tokens": total_tokens,
        "available": True,
    }


def _load_env_rates() -> dict[str, dict[str, float]]:
    raw = str(getattr(config, "IMAGE_COST_RATES_JSON", "") or "").strip()
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        logger.warning("IMAGE_COST_RATES_JSON is not valid JSON; ignoring")
        return {}
    if not isinstance(parsed, dict):
        logger.warning("IMAGE_COST_RATES_JSON must be a JSON object; ignoring")
        return {}

    rates: dict[str, dict[str, float]] = {}
    for model, entry in parsed.items():
        if not isinstance(entry, dict):
            continue
        normalized_entry: dict[str, float] = {}
        for key in _RATE_FIELDS:
            value = entry.get(key)
            if value is None:
                continue
            try:
                normalized_entry[key] = float(value)
            except (TypeError, ValueError):
                continue
        if normalized_entry:
            rates[str(model)] = normalized_entry
    return rates


def configured_model_count() -> int:
    """Count of models with a resolvable rate (builtin + env-overridden). Used
    for a startup log line; never logs the rates or the raw env value."""
    return len({**BUILTIN_RATES_PER_MILLION, **_load_env_rates()})


def _resolve_rates(model: str) -> tuple[dict[str, float] | None, str]:
    normalized_model = str(model or "").strip()
    if not normalized_model:
        return None, "unknown"
    env_rates = _load_env_rates()
    if normalized_model in env_rates:
        return env_rates[normalized_model], "env"
    if normalized_model in BUILTIN_RATES_PER_MILLION:
        return BUILTIN_RATES_PER_MILLION[normalized_model], "builtin"
    return None, "unknown"


def estimate_image_cost(model: str | None, usage: NormalizedUsage | None) -> CostEstimate:
    normalized_model = str(model or "").strip() or None

    if usage is None or not usage.get("available"):
        return {
            "currency": "USD",
            "estimated_cost_usd": None,
            "rate_source": "unknown",
            "pricing_model": normalized_model,
            "complete": False,
            "reason": "Upstream response did not include usage data",
        }

    rates, rate_source = _resolve_rates(normalized_model or "")
    if rates is None:
        reason = (
            f"No configured pricing rate for model '{normalized_model}'"
            if normalized_model
            else "Model is unknown; cannot estimate cost"
        )
        return {
            "currency": "USD",
            "estimated_cost_usd": None,
            "rate_source": "unknown",
            "pricing_model": normalized_model,
            "complete": False,
            "reason": reason,
        }

    field_map = (
        ("text_input_tokens", "text_input_per_million"),
        ("image_input_tokens", "image_input_per_million"),
        ("image_output_tokens", "image_output_per_million"),
    )
    total = Decimal(0)
    priced_any = False
    missing_fields: list[str] = []
    for usage_key, rate_key in field_map:
        tokens = usage.get(usage_key)  # type: ignore[literal-required]
        if tokens is None:
            continue
        rate = rates.get(rate_key)
        if rate is None:
            missing_fields.append(usage_key)
            continue
        try:
            total += (Decimal(tokens) * Decimal(str(rate))) / Decimal(1_000_000)
            priced_any = True
        except (InvalidOperation, TypeError):
            continue

    if not priced_any:
        return {
            "currency": "USD",
            "estimated_cost_usd": None,
            "rate_source": rate_source,
            "pricing_model": normalized_model,
            "complete": False,
            "reason": "Usage tokens were present but none matched a configured rate",
        }

    rounded = float(total.quantize(COST_DECIMAL_PLACES, rounding=ROUND_HALF_UP))
    complete = not missing_fields
    reason = (
        f"No rate configured for: {', '.join(missing_fields)}" if missing_fields else None
    )
    return {
        "currency": "USD",
        "estimated_cost_usd": rounded,
        "rate_source": rate_source,
        "pricing_model": normalized_model,
        "complete": complete,
        "reason": reason,
    }


def sum_usage(usages: list[NormalizedUsage | None]) -> NormalizedUsage | None:
    contributing = [usage for usage in usages if usage and usage.get("available")]
    if not contributing:
        return None

    def _sum_field(key: str) -> int | None:
        values = [usage.get(key) for usage in contributing if usage.get(key) is not None]  # type: ignore[literal-required]
        return sum(values) if values else None  # type: ignore[arg-type]

    return {
        "raw": {"aggregated_unit_count": len(contributing)},
        "input_tokens": _sum_field("input_tokens"),
        "output_tokens": _sum_field("output_tokens"),
        "text_input_tokens": _sum_field("text_input_tokens"),
        "image_input_tokens": _sum_field("image_input_tokens"),
        "image_output_tokens": _sum_field("image_output_tokens"),
        "total_tokens": _sum_field("total_tokens"),
        "available": True,
    }


def sum_costs(costs: list[CostEstimate | None]) -> CostEstimate | None:
    present = [cost for cost in costs if cost is not None]
    if not present:
        return None

    priced = [cost for cost in present if cost.get("estimated_cost_usd") is not None]
    if not priced:
        return {
            "currency": "USD",
            "estimated_cost_usd": None,
            "rate_source": "unknown",
            "pricing_model": None,
            "complete": False,
            "reason": "No unit reported a priceable cost",
        }

    total = Decimal(0)
    for cost in priced:
        try:
            total += Decimal(str(cost["estimated_cost_usd"]))
        except (InvalidOperation, TypeError, KeyError):
            continue
    rounded = float(total.quantize(COST_DECIMAL_PLACES, rounding=ROUND_HALF_UP))

    missing_count = len(present) - len(priced)
    incomplete_count = sum(1 for cost in present if not cost.get("complete"))
    complete = missing_count == 0 and incomplete_count == 0

    reason = None
    if not complete:
        parts = []
        if missing_count:
            parts.append(f"{missing_count} of {len(present)} units have no cost data")
        if incomplete_count:
            parts.append(f"{incomplete_count} of {len(present)} units have incomplete pricing")
        reason = "; ".join(parts) or "Partial cost estimate"

    rate_sources = {cost.get("rate_source") for cost in priced}
    rate_source = next(iter(rate_sources)) if len(rate_sources) == 1 else "mixed"
    pricing_models = {cost.get("pricing_model") for cost in priced}
    pricing_model = next(iter(pricing_models)) if len(pricing_models) == 1 else "mixed"

    return {
        "currency": "USD",
        "estimated_cost_usd": rounded,
        "rate_source": rate_source,
        "pricing_model": pricing_model,
        "complete": complete,
        "reason": reason,
    }
