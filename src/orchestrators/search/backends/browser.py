"""Browser search backend (MCP history_search + bookmarks_search)."""

import json
from datetime import datetime, timezone, timedelta
from typing import Any

from src.orchestrators.search.interface import SearchTool
from src.orchestrators.search.models import SearchResult

LIMIT_MAX = 100

# Map plan-friendly time_range to days ago (for date_after)
TIME_RANGE_DAYS: dict[str, int] = {
    "last_week": 7,
    "last_month": 30,
    "last_30_days": 30,
}


def _parse_mcp_results(result: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract results list from MCP response. Server returns {results: [...], error?, message?}; client may wrap in {success, output}."""
    if not result.get("success") and "results" not in result:
        return []
    data = result.get("results")
    if data is None:
        output = result.get("output", "{}")
        if isinstance(output, str):
            try:
                parsed = json.loads(output)
            except json.JSONDecodeError:
                return []
            if isinstance(parsed, dict) and parsed.get("error"):
                return []
            data = parsed.get("results") if isinstance(parsed, dict) else None
        else:
            data = output.get("results") if isinstance(output, dict) else None
    if not isinstance(data, list):
        return []
    return data


def _item_to_search_result(item: dict[str, Any], kind: str) -> SearchResult | None:
    if not isinstance(item, dict):
        return None
    title = item.get("title") or "No title"
    snippet = item.get("snippet") or item.get("content", "")[:500] or ""
    url = item.get("url") or ""
    score = item.get("score")
    if score is None or not isinstance(score, (int, float)):
        score = 0.8
    score = max(0.0, min(1.0, float(score)))
    ts = item.get("last_visit_time") if kind == "history" else item.get("added_at")
    if ts is not None and hasattr(ts, "isoformat"):
        ts = ts.isoformat()
    metadata: dict[str, Any] = {"url": url, "type": kind}
    if kind == "history":
        if item.get("domain"):
            metadata["domain"] = item["domain"]
        if item.get("visit_count") is not None:
            metadata["visit_count"] = item["visit_count"]
    else:
        if item.get("folder"):
            metadata["folder"] = item["folder"]
    return SearchResult(
        content=snippet,
        source="browser",
        title=title,
        timestamp=ts,
        metadata=metadata,
        relevance_score=score,
    )


class BrowserSearchBackend(SearchTool):
    def __init__(self, mcp_call_tool: callable):
        self._mcp_call = mcp_call_tool

    async def search(
        self,
        query: str,
        top_k: int = 10,
        filters: dict[str, Any] | None = None,
    ) -> list[SearchResult]:
        limit = min(max(1, top_k), LIMIT_MAX)
        half = max(1, limit // 2)
        f = filters or {}
        # Map time_range to date_after when not explicitly set
        if f.get("time_range") and not f.get("date_after"):
            days = TIME_RANGE_DAYS.get(str(f["time_range"]).strip().lower())
            if days is not None:
                f = {**f, "date_after": (datetime.now(timezone.utc) - timedelta(days=days)).date().isoformat()}
        history_args: dict[str, Any] = {
            "query": (query or "").strip(),
            "limit": half,
        }
        if f.get("date_after"):
            history_args["date_after"] = str(f["date_after"])
        if f.get("date_before"):
            history_args["date_before"] = str(f["date_before"])
        if f.get("domain"):
            history_args["domain"] = str(f["domain"])
        bookmarks_args: dict[str, Any] = {
            "query": (query or "").strip(),
            "limit": half,
        }
        if f.get("folder"):
            bookmarks_args["folder"] = str(f["folder"])

        history_result = await self._mcp_call("history_search", history_args)
        bookmarks_result = await self._mcp_call("bookmarks_search", bookmarks_args)

        out: list[SearchResult] = []
        for raw in _parse_mcp_results(history_result):
            r = _item_to_search_result(raw, "history")
            if r:
                out.append(r)
        for raw in _parse_mcp_results(bookmarks_result):
            r = _item_to_search_result(raw, "bookmark")
            if r:
                out.append(r)
        out.sort(key=lambda x: -x.relevance_score)
        return out[:limit]

    def get_source_name(self) -> str:
        return "browser"

    def can_handle_query(self, query: str, intent: dict[str, Any]) -> float:
        query_lower = query.lower()
        strong = [
            "browser",
            "visited",
            "bookmark",
            "saved link",
            "history",
            "sites i opened",
            "pages i visited",
            "tabs i had",
            "vivaldi",
        ]
        if any(w in query_lower for w in strong):
            return 0.95
        hints = intent.get("source_hints") or []
        hint_str = " ".join(str(h).lower() for h in hints)
        if any(x in hint_str for x in ("browser", "history", "bookmarks")):
            return 0.85
        return 0.3
