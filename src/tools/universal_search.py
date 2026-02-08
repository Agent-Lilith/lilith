"""Universal Search tool: single entry point. Agent only decides that search is needed; context is injected."""

from src.core.prompts import get_tool_description, get_tool_examples
from src.search import UniversalSearchOrchestrator
from src.search.models import UniversalSearchResponse
from src.tools.base import Tool, ToolResult


class UniversalSearchTool(Tool):
    """One search tool. The agent only invokes it; full context is injected by the framework."""

    def __init__(self, orchestrator: UniversalSearchOrchestrator):
        self._orchestrator = orchestrator

    @property
    def name(self) -> str:
        return "universal_search"

    @property
    def description(self) -> str:
        return get_tool_description(self.name)

    @property
    def parameters(self) -> dict[str, str]:
        return {
            "max_results": "Optional. Max results to return (default 20, max 50).",
        }

    def get_examples(self) -> list[str]:
        return get_tool_examples(self.name)

    async def execute(
        self,
        max_results: str = "",
        user_message: str = "",
        conversation_context: str = "",
    ) -> ToolResult:
        from src.core.logger import logger

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

        logger.tool_execute(self.name, {"max_results": max_val})
        try:
            response: UniversalSearchResponse = await self._orchestrator.search(
                conversation_context=(conversation_context or "").strip(),
                user_message=(user_message or "").strip(),
                max_results=max_val,
                do_refinement=True,
            )
        except Exception as e:
            logger.tool_result(self.name, 0, False)
            return ToolResult.fail(f"Universal search failed: {e!s}")

        out = response.model_dump_json(indent=2)
        logger.tool_result(self.name, len(out), True)
        return ToolResult.ok(out)
