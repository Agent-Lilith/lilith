"""Universal Search tool: single entry point for multi-source hybrid search."""

from src.core.prompts import get_tool_description, get_tool_examples
from src.orchestrators.search import UniversalSearchOrchestrator
from src.orchestrators.search.models import UniversalSearchResponse
from src.tools.base import Tool, ToolResult


def _format_response(response: UniversalSearchResponse) -> str:
    """Format search results in a compact, LLM-friendly text format."""
    meta = response.meta
    sources = ", ".join(meta.get("sources_queried", []))
    methods = "+".join(meta.get("methods_used", []))
    total_ms = meta.get("timing_ms", {}).get("total", 0)
    count = len(response.results)

    parts: list[str] = []

    # Count mode: just the number
    meta_count = meta.get("count")
    if meta_count is not None:
        count_source = meta.get("count_source", "unknown")
        header = f"Count: {meta_count} from {count_source}"
        if total_ms:
            header += f" [{total_ms:.0f}ms]"
        parts.append(header)
        return "\n".join(parts)

    # Aggregate mode: top groups with counts
    aggregates = meta.get("aggregates")
    if aggregates:
        agg_source = meta.get("aggregates_source", "unknown")
        header = f"Aggregate: top {len(aggregates)} groups from {agg_source}"
        if total_ms:
            header += f" [{total_ms:.0f}ms]"
        parts.append(header)
        parts.append("")
        for i, agg in enumerate(aggregates, 1):
            label = agg.get("label") or agg.get("group_value", "?")
            cnt = agg.get("count", 0)
            parts.append(f"{i}. {label}: {cnt}")
        return "\n".join(parts)

    # Header for search mode
    header = f"Search: {count} results from {sources}"
    if methods:
        header += f" ({methods})"
    if total_ms:
        header += f" [{total_ms:.0f}ms]"
    parts.append(header)

    # Notes (e.g. "No data found for yesterday")
    for note in response.notes:
        parts.append(f"⚠ {note}")

    # Errors
    for err in response.errors:
        parts.append(f"⚠ {err}")

    # Results
    if response.results:
        parts.append("")
        for i, r in enumerate(response.results, 1):
            # Score: best method score
            best_score = max(r.scores.values()) if r.scores else 0.0
            # Timestamp
            ts = ""
            if r.timestamp:
                ts_str = r.timestamp[:10] if len(r.timestamp) >= 10 else r.timestamp
                ts = f" | {ts_str}"
            # Domain/sender info from metadata
            detail = ""
            if r.metadata.get("domain"):
                detail = f" | {r.metadata['domain']}"
            elif r.metadata.get("from"):
                detail = f" | from {r.metadata['from']}"
            # Visit count for browser
            visits = ""
            if r.metadata.get("visit_count") and r.metadata["visit_count"] > 1:
                visits = f" | {r.metadata['visit_count']} visits"

            line = f'{i}. [{r.source}] "{r.title}"{detail}{visits}{ts} | score:{best_score:.2f}'
            parts.append(line)
            # Include snippet (message body) when available and different from title
            if r.snippet and r.snippet.strip() and r.snippet.strip() != r.title.strip():
                snippet_text = r.snippet[:200].replace("\n", " ").strip()
                if snippet_text:
                    parts.append(f"   {snippet_text}")
    elif not response.notes:
        parts.append("No results found.")

    return "\n".join(parts)


class UniversalSearchTool(Tool):
    """One search tool. The agent invokes it; full context is injected by the framework."""

    def __init__(self, orchestrator: UniversalSearchOrchestrator):
        self._orchestrator = orchestrator

    @property
    def orchestrator(self) -> UniversalSearchOrchestrator:
        return self._orchestrator

    @property
    def name(self) -> str:
        return "universal_search"

    @property
    def description(self) -> str:
        base = get_tool_description(self.name)
        labels = self._orchestrator._capabilities.source_labels_for_agent()
        if labels:
            return base + "\n\nSearchable sources (current): " + ", ".join(labels)
        return base

    @property
    def parameters(self) -> dict[str, str]:
        return {
            "max_results": "Optional. Max results to return (default 20, max 50).",
        }

    def get_examples(self) -> list[str]:
        return get_tool_examples(self.name)

    async def execute(self, **kwargs: object) -> ToolResult:
        from src.core.logger import logger

        max_results = str(kwargs.get("max_results", ""))
        user_message = str(kwargs.get("user_message", ""))
        conversation_context = str(kwargs.get("conversation_context", ""))
        context = (conversation_context or user_message or "").strip()
        if not context:
            return ToolResult.fail(
                "Universal search requires context (injected by the system). "
                "Ensure the agent is invoking this from the normal conversation flow."
            )

        try:
            max_val = 20
            if max_results and str(max_results).strip():
                try:
                    max_val = min(50, max(1, int(str(max_results).strip())))
                except ValueError:
                    pass
        except Exception:
            max_val = 20

        try:
            logger.tool_execute(self.name, {"user_message": user_message[:200], "max_results": max_val})
            response: UniversalSearchResponse = await self._orchestrator.search(
                conversation_context=(conversation_context or "").strip(),
                user_message=(user_message or "").strip(),
                max_results=max_val,
                do_refinement=True,
            )
        except Exception as e:
            logger.error("Universal search failed: %s", e, exc_info=True)
            fail_msg = f"Universal search failed: {e!s}"
            logger.tool_result(self.name, 0, False, error_reason=fail_msg)
            return ToolResult.fail(fail_msg)

        out = _format_response(response)
        logger.tool_result(self.name, len(out), True, result_content=out)
        return ToolResult.ok(out)
