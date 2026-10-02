"""Attach native search progress and citations to one durable Agent message."""

from typing import Any

from ..integrations.agent_search import SearchStatus, SourceCitation
from .agent_refs import strip_ref_tags


class SearchBudgetExceeded(ValueError):
    pass


class TurnSearch:
    def __init__(self) -> None:
        self.calls: dict[str, dict[str, Any]] = {}
        self.parts: dict[tuple[str, int], dict[str, Any]] = {}
        self.citations: list[SourceCitation] = []
        self.sources_block: dict[str, Any] | None = None

    def record_text(self, item_id: str, content_index: int, raw: str, block: dict[str, Any]) -> None:
        key = (item_id, content_index)
        part = self.parts.setdefault(key, {"raw": "", "segments": []})
        segments = part["segments"]
        if not segments or segments[-1]["block_id"] != block["id"]:
            segments.append({"block_id": block["id"], "start": len(part["raw"]), "end": len(part["raw"]), "offset": len(block.get("text", ""))})
        part["raw"] += raw
        segments[-1]["end"] = len(part["raw"])

    async def update(self, run: Any, event: SearchStatus) -> None:
        block = self.calls.get(event.call_id)
        if block is None:
            if len(self.calls) >= run.agent.max_tool_rounds or run.rounds_used >= run.agent.max_tool_rounds:
                raise SearchBudgetExceeded("The Agent web search tool budget was exceeded.")
            run.rounds_used += 1
            block = {"id": run._next_block_id("s"), "type": "search", "call_id": event.call_id}
            self.calls[event.call_id] = block
        # Progress-only events must not erase an action/queries already reported.
        block = {**block, "status": event.status}
        if event.queries or event.url or "action" not in block:
            block.update(action=event.action, queries=list(event.queries), url=event.url)
        self.calls[event.call_id] = block
        await run.upsert_block(block)

    async def publish_sources(self, run: Any) -> None:
        if not self.citations:
            return
        sources = []
        for index, citation in enumerate(self.citations[:100], start=1):
            part = self.parts.get((citation.item_id, citation.content_index))
            source = {"id": f"source-{index}", "title": citation.title, "url": citation.url, "text_block_id": None,
                      "start_index": None, "end_index": None, "excerpt": ""}
            if part and citation.end_index <= len(part["raw"]):
                raw = part["raw"]
                source["excerpt"] = strip_ref_tags(raw[citation.start_index:citation.end_index])[:500]
                segment = next((segment for segment in part["segments"] if segment["start"] <= citation.start_index and citation.end_index <= segment["end"]), None)
                if segment:
                    start = segment["start"]
                    source.update(text_block_id=segment["block_id"],
                                  start_index=segment["offset"] + len(strip_ref_tags(raw[start:citation.start_index])),
                                  end_index=segment["offset"] + len(strip_ref_tags(raw[start:citation.end_index])))
            sources.append(source)
        if self.sources_block is None:
            self.sources_block = {"id": run._next_block_id("c"), "type": "sources"}
        self.sources_block = {**self.sources_block, "sources": sources}
        await run.upsert_block(self.sources_block)

    async def settle(self, run: Any, status: str) -> None:
        for call_id, block in list(self.calls.items()):
            if block.get("status") not in {"completed", "failed", "cancelled", "interrupted"}:
                block = {**block, "status": "failed" if status == "failed" else "cancelled" if status == "cancelled" else "interrupted"}
                self.calls[call_id] = block
                await run.upsert_block(block)
