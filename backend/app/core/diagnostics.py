"""Bounded, redacted diagnostics for one image job unit.

Records the submit/poll/result/download stages of a unit, including trimmed
upstream response snapshots, so a failed mapping can be diagnosed from the job
history. Everything stored here is redacted and size-capped: credentials are
replaced, URL queries and fragments are stripped, image base64 is omitted, and
the total payload stays under `MAX_UNIT_DIAGNOSTIC_BYTES`.
"""

import json
import re
from typing import Any

from .redaction import redact_sensitive_text
from .utils import utc_now

MAX_UNIT_DIAGNOSTIC_BYTES = 64 * 1024
_MAX_RECORDS = 12
_MAX_RECORD_BYTES = MAX_UNIT_DIAGNOSTIC_BYTES // 8
_MAX_STRING_CHARS = 2000
_MAX_ARRAY_ITEMS = 20
_MAX_DEPTH = 8
_OMITTED_IMAGE = "[image data omitted]"
_SENSITIVE_KEY = re.compile(
    r"(authorization|api[-_]?key|apikey|secret|token|password|passwd|cookie|credential)",
    re.IGNORECASE,
)
_BASE64_LIKE = re.compile(r"^[A-Za-z0-9+/=\s]{512,}$")


def _bounded_value(
    value: Any,
    *,
    depth: int = 0,
    omitted: list[str],
    key: str = "",
) -> Any:
    if depth > _MAX_DEPTH:
        omitted.append("nested value depth limit")
        return "[depth limit]"
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for index, (child_key, child_value) in enumerate(value.items()):
            if index >= _MAX_ARRAY_ITEMS:
                omitted.append(f"{index + 1 - _MAX_ARRAY_ITEMS} dict entries")
                result["_omitted"] = f"{len(value) - _MAX_ARRAY_ITEMS} more entries"
                break
            if _SENSITIVE_KEY.search(str(child_key)):
                result[str(child_key)] = "[REDACTED]"
                continue
            result[str(child_key)] = _bounded_value(
                child_value, depth=depth + 1, omitted=omitted, key=str(child_key)
            )
        return result
    if isinstance(value, (list, tuple)):
        items = [
            _bounded_value(item, depth=depth + 1, omitted=omitted, key=key)
            for item in value[:_MAX_ARRAY_ITEMS]
        ]
        if len(value) > _MAX_ARRAY_ITEMS:
            omitted.append(f"{len(value) - _MAX_ARRAY_ITEMS} list items")
            items.append(f"[{len(value) - _MAX_ARRAY_ITEMS} more items omitted]")
        return items
    if isinstance(value, str):
        redacted = redact_sensitive_text(value)
        if redacted.startswith("data:image/") or (
            len(redacted) > _MAX_STRING_CHARS and _BASE64_LIKE.match(redacted)
        ):
            omitted.append(f"binary payload ({len(redacted)} chars)")
            return _OMITTED_IMAGE
        if len(redacted) > _MAX_STRING_CHARS:
            omitted.append(f"{len(redacted) - _MAX_STRING_CHARS} string chars")
            return redacted[:_MAX_STRING_CHARS] + "...[truncated]"
        return redacted
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return redact_sensitive_text(str(value))[:_MAX_STRING_CHARS]


def _encode(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


class UnitDiagnostics:
    """Collects diagnostic records for one unit execution attempt.

    The recorder is deliberately not thread-safe; one instance is owned by one
    executor task. Records are dropped (oldest first) once `_MAX_RECORDS`
    records exist, and each record is trimmed to `_MAX_RECORD_BYTES`.
    """

    def __init__(self, *, max_bytes: int = MAX_UNIT_DIAGNOSTIC_BYTES):
        self.max_bytes = max(1024, int(max_bytes))
        self._code = ""
        self._records: list[dict[str, Any]] = []
        self._recovery: dict[str, Any] = {}

    @property
    def code(self) -> str:
        return self._code

    @property
    def has_records(self) -> bool:
        return bool(self._code or self._records or self._recovery)

    def set_code(self, code: str) -> None:
        if code and not self._code:
            self._code = str(code)

    def set_recovery(self, **fields: Any) -> None:
        omitted: list[str] = []
        bounded = _bounded_value(fields, omitted=omitted, depth=_MAX_DEPTH - 1)
        if isinstance(bounded, dict):
            self._recovery.update(bounded)

    def record_http(
        self,
        phase: str,
        *,
        method: str,
        url: str,
        status: int | None = None,
        snapshot: Any = None,
        mapping_path: str | None = None,
        error: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> None:
        record: dict[str, Any] = {
            "phase": str(phase),
            "at": utc_now(),
            "http": {"method": str(method).upper(), "url": redact_sensitive_text(url)},
        }
        if status is not None:
            record["http"]["status"] = int(status)
        self._finish_record(record, snapshot=snapshot, mapping_path=mapping_path, error=error, extra=extra)

    def record_event(
        self,
        phase: str,
        message: str,
        *,
        mapping_path: str | None = None,
        error: str | None = None,
        snapshot: Any = None,
        extra: dict[str, Any] | None = None,
    ) -> None:
        record: dict[str, Any] = {
            "phase": str(phase),
            "at": utc_now(),
            "message": redact_sensitive_text(message)[:_MAX_STRING_CHARS],
        }
        self._finish_record(record, snapshot=snapshot, mapping_path=mapping_path, error=error, extra=extra)

    def record_download(
        self,
        *,
        url: str | None = None,
        status: int | None = None,
        error: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> None:
        record: dict[str, Any] = {"phase": "download", "at": utc_now()}
        if url is not None:
            record["url"] = redact_sensitive_text(url)
        if status is not None:
            record["status"] = int(status)
        self._finish_record(record, snapshot=None, mapping_path=None, error=error, extra=extra)

    def _finish_record(
        self,
        record: dict[str, Any],
        *,
        snapshot: Any,
        mapping_path: str | None,
        error: str | None,
        extra: dict[str, Any] | None,
    ) -> None:
        omitted: list[str] = []
        if mapping_path:
            record["mapping_path"] = redact_sensitive_text(mapping_path)[:_MAX_STRING_CHARS]
        if error:
            record["error"] = redact_sensitive_text(error)[:_MAX_STRING_CHARS]
        if extra:
            record["extra"] = _bounded_value(extra, omitted=omitted)
        if snapshot is not None:
            record["snapshot"] = _bounded_value(snapshot, omitted=omitted)
        if omitted:
            record["omitted"] = omitted[:_MAX_ARRAY_ITEMS]
        if len(_encode(record)) > _MAX_RECORD_BYTES:
            record.pop("snapshot", None)
            record["truncated"] = True
        if len(_encode(record)) > _MAX_RECORD_BYTES:
            record.pop("extra", None)
            if "message" in record:
                record["message"] = str(record["message"])[:500]
            if "error" in record:
                record["error"] = str(record["error"])[:500]
        self._records.append(record)
        if len(self._records) > _MAX_RECORDS:
            del self._records[: len(self._records) - _MAX_RECORDS]

    def payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"stages": [dict(record) for record in self._records]}
        if self._code:
            payload["code"] = self._code
        if self._recovery:
            payload["recovery"] = dict(self._recovery)
        encoded = _encode(payload)
        if len(encoded.encode("utf-8")) > self.max_bytes:
            # Progressive shrink: drop snapshots, then older records entirely.
            for record in payload["stages"]:
                record.pop("snapshot", None)
                record["truncated"] = True
                if len(_encode(payload).encode("utf-8")) <= self.max_bytes:
                    break
        while len(payload["stages"]) > 1 and len(_encode(payload).encode("utf-8")) > self.max_bytes:
            payload["stages"].pop(0)
        if len(_encode(payload).encode("utf-8")) > self.max_bytes:
            payload["stages"] = payload["stages"][:1]
            payload["truncated"] = True
        return payload


__all__ = ["MAX_UNIT_DIAGNOSTIC_BYTES", "UnitDiagnostics"]
