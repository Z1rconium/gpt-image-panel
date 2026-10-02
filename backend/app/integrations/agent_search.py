"""Bounded, neutral search events and upstream URL citations."""

from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit


@dataclass(frozen=True)
class SearchStatus:
    call_id: str
    status: str
    action: str = "search"
    queries: tuple[str, ...] = ()
    url: str = ""


@dataclass(frozen=True)
class SourceCitation:
    item_id: str
    content_index: int
    title: str
    url: str
    start_index: int
    end_index: int


def safe_source_url(value: Any) -> str:
    if not isinstance(value, str) or len(value) > 2048 or any(ord(char) <= 32 or ord(char) == 127 for char in value):
        return ""
    try:
        parsed = urlsplit(value)
        _ = parsed.port
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
            return ""
        return value
    except ValueError:
        return ""


def citation_event(annotation: Any, item_id: str, content_index: int) -> SourceCitation | None:
    if not isinstance(annotation, dict) or annotation.get("type") != "url_citation":
        return None
    url = safe_source_url(annotation.get("url"))
    start, end = annotation.get("start_index"), annotation.get("end_index")
    if not url or type(start) is not int or type(end) is not int or not 0 <= start <= end <= 200_000:
        return None
    return SourceCitation(item_id[:200], content_index, str(annotation.get("title") or url)[:300], url, start, end)


def search_event(item: dict[str, Any], status: str | None = None) -> SearchStatus:
    action = item.get("action") if isinstance(item.get("action"), dict) else {}
    queries = action.get("queries") or [action.get("query")]
    if not isinstance(queries, list):
        queries = []
    return SearchStatus(
        str(item.get("id") or "search")[:200], status or str(item.get("status") or "in_progress"),
        str(action.get("type") or "search")[:40],
        tuple(value[:500] for value in queries[:8] if isinstance(value, str)),
        safe_source_url(action.get("url")),
    )
