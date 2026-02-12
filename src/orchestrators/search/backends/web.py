"""Web search backend (SearXNG). Returns SearchResultV1."""

from typing import Any

import httpx

from src.contracts.mcp_search_v1 import SearchResultV1, SourceClass
from src.core.config import config
from src.orchestrators.search.interface import SearchBackend


class WebSearchBackend(SearchBackend):
    def __init__(self, base_url: str | None = None):
        search_url = base_url or config.searxng_url or ""
        self._base_url = search_url.rstrip("/") if search_url else ""
        if self._base_url and not self._base_url.endswith("/search"):
            self._base_url = self._base_url + "/search"

    async def search(
        self,
        query: str,
        methods: list[str] | None = None,
        filters: list[dict[str, Any]] | None = None,
        top_k: int = 10,
    ) -> list[SearchResultV1]:
        if not self._base_url or not query.strip():
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
        results: list[SearchResultV1] = []
        for i, item in enumerate(raw[:top_k]):
            title = item.get("title") or "No Title"
            content = item.get("content") or item.get("snippet") or ""
            url = item.get("url") or "#"
            score = max(0.3, 1.0 - (i * 0.05))

            results.append(
                SearchResultV1(
                    id=f"web_{i}_{hash(url) % 10000}",
                    source="web",
                    source_class=SourceClass.WEB,
                    title=title,
                    snippet=content,
                    timestamp=None,
                    scores={"fulltext": score},
                    methods_used=["fulltext"],
                    metadata={"url": url},
                    provenance=f"web result from {url.split('/')[2] if '/' in url else url}",
                )
            )
        return results

    def get_source_name(self) -> str:
        return "web"

    def get_source_class(self) -> SourceClass:
        return SourceClass.WEB

    def get_supported_methods(self) -> list[str]:
        return ["fulltext"]

    def get_supported_filters(self) -> list[dict[str, Any]]:
        return []
