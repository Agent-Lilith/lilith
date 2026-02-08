"""Email search backend (MCP emails_search)."""

import json
from typing import Any

from src.core.config import config
from src.search.interface import SearchTool
from src.search.models import SearchResult


def _parse_list(v: Any) -> list[str] | None:
    if v is None:
        return None
    if isinstance(v, list):
        return [str(x) for x in v]
    s = str(v).strip()
    if not s:
        return None
    if s.startswith("["):
        try:
            out = json.loads(s)
            return out if isinstance(out, list) else [str(x) for x in out]
        except json.JSONDecodeError:
            pass
    return [x.strip() for x in s.split(",") if x.strip()]


class EmailSearchBackend(SearchTool):
    """MCP emails_search wrapper."""

    def __init__(self, mcp_call_tool: callable, account_id: int | None = None):
        """
        mcp_call_tool: async (name: str, arguments: dict) -> dict with success, output/error.
        """
        self._mcp_call = mcp_call_tool
        self._account_id = account_id or config.mcp_email_account_id

    async def search(
        self,
        query: str,
        top_k: int = 10,
        filters: dict[str, Any] | None = None,
    ) -> list[SearchResult]:
        args: dict[str, Any] = {
            "query": query.strip() or "",
            "limit": min(max(1, top_k), 50),
            "account_id": self._account_id,
        }
        f = filters or {}
        if f.get("date_after"):
            args["date_after"] = str(f["date_after"])
        if f.get("date_before"):
            args["date_before"] = str(f["date_before"])
        if f.get("from_email"):
            args["from_email"] = str(f["from_email"])
        if f.get("labels"):
            labels = f["labels"]
            args["labels"] = _parse_list(labels) if not isinstance(labels, list) else labels

        result = await self._mcp_call("emails_search", args)
        if not result.get("success"):
            raise RuntimeError(result.get("error", "Email search failed"))

        data = result.get("results")
        if data is None:
            output = result.get("output", "[]")
            if isinstance(output, str):
                try:
                    data = json.loads(output)
                except json.JSONDecodeError:
                    return []
            else:
                data = output if isinstance(output, list) else []

        if not isinstance(data, list):
            return []

        results: list[SearchResult] = []
        for i, item in enumerate(data):
            if not isinstance(item, dict):
                continue
            meta = item.get("metadata", item)
            subject = meta.get("subject") or item.get("subject") or "No subject"
            snippet = item.get("snippet") or item.get("body", "")[:500] or ""
            date_val = meta.get("date") or item.get("date")
            if date_val and hasattr(date_val, "isoformat"):
                date_val = date_val.isoformat()
            score = 1.0 - (i * 0.04)
            if score < 0.3:
                score = 0.3
            results.append(
                SearchResult(
                    content=snippet,
                    source="email",
                    title=subject,
                    timestamp=date_val,
                    metadata={
                        "email_id": item.get("id"),
                        "thread_id": item.get("thread_id") or meta.get("thread_id"),
                        "from": meta.get("from") or item.get("from"),
                        "to": meta.get("to") or item.get("to"),
                    },
                    relevance_score=score,
                )
            )
        return results

    def get_source_name(self) -> str:
        return "email"

    def can_handle_query(self, query: str, intent: dict[str, Any]) -> float:
        query_lower = query.lower()
        strong = ["email", "sent", "received", "replied", "conversation", "inbox"]
        job = ["job", "application", "interview", "recruiter", "offer"]
        people = ["from", "to", "talked to", "said", "wrote", "message"]
        if any(w in query_lower for w in strong):
            return 0.95
        if any(w in query_lower for w in job):
            return 0.9
        if any(w in query_lower for w in people):
            return 0.8
        hints = intent.get("source_hints") or []
        if "email" in str(hints).lower():
            return 0.85
        return 0.3
