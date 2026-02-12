"""MCP search dispatcher: routes unified_search calls to MCP servers.

Replaces the old per-backend SearchTool subclass pattern.
One dispatcher handles all MCP servers; each server is called via unified_search.
"""

import json
import logging
import time
from typing import Any, Callable, Awaitable

from src.contracts.mcp_search_v1 import SearchResultV1, SourceClass

logger = logging.getLogger(__name__)


class MCPSearchDispatcher:
    """Routes unified_search calls to MCP servers and normalizes results."""

    def __init__(self) -> None:
        # source_name -> MCP call function
        self._mcp_callers: dict[str, Callable[..., Awaitable[dict[str, Any]]]] = {}
        # source_name -> list of source names handled by this MCP connection
        self._connection_sources: dict[str, str] = {}  # source -> connection_key

    def register_mcp(
        self,
        connection_key: str,
        source_names: list[str],
        mcp_call: Callable[..., Awaitable[dict[str, Any]]],
    ) -> None:
        """Register an MCP connection that handles one or more sources."""
        for name in source_names:
            self._mcp_callers[name] = mcp_call
            self._connection_sources[name] = connection_key
        logger.info(
            "Dispatcher: registered MCP connection '%s' for sources %s",
            connection_key, source_names,
        )

    def has_source(self, source_name: str) -> bool:
        return source_name in self._mcp_callers

    async def search(
        self,
        source: str,
        query: str,
        methods: list[str] | None = None,
        filters: list[dict[str, Any]] | None = None,
        top_k: int = 10,
    ) -> list[SearchResultV1]:
        """Call unified_search on the appropriate MCP server and parse results."""
        mcp_call = self._mcp_callers.get(source)
        if not mcp_call:
            logger.warning("Dispatcher: no MCP connection for source '%s'", source)
            return []

        args: dict[str, Any] = {
            "query": query,
            "top_k": top_k,
            "include_scores": True,
        }
        if methods:
            args["methods"] = methods
        if filters:
            args["filters"] = filters

        # For browser server, route to the correct sub-source
        if source in ("browser_history", "browser_bookmarks"):
            args["search_history"] = source == "browser_history"
            args["search_bookmarks"] = source == "browser_bookmarks"

        t0 = time.monotonic()
        try:
            result = await mcp_call("unified_search", args)
        except Exception as e:
            logger.error("Dispatcher: MCP call failed for source '%s': %s", source, e)
            return []
        elapsed_ms = round((time.monotonic() - t0) * 1000, 1)

        logger.info(
            "Dispatcher: unified_search(%s) returned in %.1fms",
            source, elapsed_ms,
        )

        return self._parse_response(result, source)

    async def fetch_capabilities(self, connection_key: str, mcp_call: Callable) -> dict[str, Any]:
        """Call search_capabilities on an MCP server."""
        try:
            result = await mcp_call("search_capabilities", {})
            if result.get("success") is False:
                logger.warning("search_capabilities failed: %s", result.get("error"))
                return {}
            # May be wrapped in output
            output = result.get("output")
            if isinstance(output, str):
                return json.loads(output)
            if isinstance(output, dict):
                return output
            # Direct dict response
            if "schema_version" in result or "sources" in result:
                return result
            return {}
        except Exception as e:
            logger.error("Failed to fetch capabilities from '%s': %s", connection_key, e)
            return {}

    def _parse_response(self, result: dict[str, Any], source: str) -> list[SearchResultV1]:
        """Parse MCP unified_search response into SearchResultV1 list."""
        if not result.get("success", True) and "results" not in result:
            error = result.get("error", "Unknown error")
            logger.warning("Dispatcher: search failed for '%s': %s", source, error)
            return []

        # Extract the response data (may be wrapped in output string)
        data = result
        output = result.get("output")
        if isinstance(output, str):
            try:
                data = json.loads(output)
            except json.JSONDecodeError:
                logger.warning("Dispatcher: invalid JSON output from '%s'", source)
                return []
        elif isinstance(output, dict):
            data = output

        raw_results = data.get("results", [])
        if not isinstance(raw_results, list):
            return []

        results: list[SearchResultV1] = []
        for item in raw_results:
            if not isinstance(item, dict):
                continue
            try:
                results.append(SearchResultV1(
                    id=str(item.get("id", "")),
                    source=item.get("source", source),
                    source_class=SourceClass(item.get("source_class", "personal")),
                    title=item.get("title", ""),
                    snippet=item.get("snippet", ""),
                    timestamp=item.get("timestamp"),
                    scores=item.get("scores", {}),
                    methods_used=item.get("methods_used", []),
                    metadata=item.get("metadata", {}),
                    provenance=item.get("provenance"),
                ))
            except Exception as e:
                logger.debug("Dispatcher: failed to parse result from '%s': %s", source, e)
                continue

        return results
