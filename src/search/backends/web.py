"""Web search backend (SearXNG)."""

from typing import Any

import httpx

from src.core.config import config
from src.search.interface import SearchTool
from src.search.models import SearchResult


class WebSearchBackend(SearchTool):
    """SearXNG-backed web search."""

    def __init__(self, base_url: str | None = None):
        search_url = base_url or config.searxng_url or ""
        self._base_url = search_url.rstrip("/") if search_url else ""
        if self._base_url and not self._base_url.endswith("/search"):
            self._base_url = self._base_url + "/search"

    async def search(
        self,
        query: str,
        top_k: int = 10,
        filters: dict[str, Any] | None = None,
    ) -> list[SearchResult]:
        if not self._base_url:
            return []
        params = {"q": query, "format": "json", "language": "en-US"}
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(
                self._base_url,
                params=params,
                follow_redirects=True,
            )
            response.raise_for_status()
            data = response.json()
        raw = data.get("results", [])
        results: list[SearchResult] = []
        for i, item in enumerate(raw[:top_k]):
            title = item.get("title") or "No Title"
            content = item.get("content") or item.get("snippet") or "No content available"
            url = item.get("url") or "#"
            score = 1.0 - (i * 0.05)
            if score < 0.5:
                score = 0.5
            results.append(
                SearchResult(
                    content=content,
                    source="web",
                    title=title,
                    timestamp=None,
                    metadata={"url": url},
                    relevance_score=score,
                )
            )
        return results

    def get_source_name(self) -> str:
        return "web"

    def can_handle_query(self, query: str, intent: dict[str, Any]) -> float:
        query_lower = query.lower()
        news = ["latest", "recent", "today", "news", "current"]
        entities = ["who is", "what is", "where is", "how to", "why", "explain"]
        if any(w in query_lower for w in news):
            return 0.9
        if any(p in query_lower for p in entities):
            return 0.7
        return 0.5
