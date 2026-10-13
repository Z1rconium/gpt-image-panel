import json
from datetime import datetime, timezone, timedelta
from typing import Any


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def json_dumps_canonical(value: Any, *, compact: bool = False) -> str:
    """Serialize JSON with the project-wide canonical form.

    Values are emitted with ``ensure_ascii=False`` and stable key ordering so
    that identical payloads round-trip to identical strings. ``compact=True``
    drops the default spaces for wire payloads that are hashed or sent verbatim.
    """
    if compact:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def coerce_nonnegative_int(
    value: Any,
    *,
    default: int | None = 0,
    reject_bool: bool = False,
    coerce_numeric_strings: bool = False,
    non_integral_float: str = "truncate",
    negative: str = "default",
    invalid: str = "default",
    max_value: int | None = None,
) -> int | None:
    """Coerce ``value`` into a non-negative int with explicit edge semantics.

    Callers previously duplicated this logic with subtly different outcomes.
    Each ``on_*``/mode knob selects whether an edge case yields ``default`` or
    ``0`` (``"default"`` or ``"zero"``):

    - ``None`` always yields ``default``.
    - ``reject_bool`` treats ``True``/``False`` as ``invalid``.
    - ``non_integral_float`` handles values such as ``1.5``: ``"truncate"``
      keeps ``int(value)``, while ``"default"``/``"zero"`` reject them.
    - ``negative`` handles values below zero.
    - ``invalid`` handles unparsable values; ``coerce_numeric_strings`` also
      accepts numeric strings such as ``"12.5"``.
    - ``max_value`` rejects values above the cap as ``invalid``.
    """

    def _fallback(mode: str) -> int | None:
        return 0 if mode == "zero" else default

    if value is None:
        return default
    if reject_bool and isinstance(value, bool):
        return _fallback(invalid)
    if isinstance(value, bool):
        number = int(value)
    elif isinstance(value, int):
        number = value
    elif isinstance(value, float):
        if not value.is_integer():
            if non_integral_float == "truncate":
                number = int(value)
            else:
                return _fallback(non_integral_float)
        else:
            number = int(value)
    else:
        try:
            number = int(value)
        except (TypeError, ValueError):
            if not coerce_numeric_strings:
                return _fallback(invalid)
            try:
                number = int(float(value))
            except (TypeError, ValueError):
                return _fallback(invalid)
    if number < 0:
        return _fallback(negative)
    if max_value is not None and number > max_value:
        return _fallback(invalid)
    return number


BEIJING_TIMEZONE = timezone(timedelta(hours=8))


def beijing_now() -> str:
    return datetime.now(BEIJING_TIMEZONE).isoformat()
