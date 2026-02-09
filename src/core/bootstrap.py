"""Tool registration and system prompt setup at startup."""

from src.core.config import config
from src.core.logger import logger
from src.core.prompts import build_system_prompt
from src.services.google_service import GoogleService
from src.tools.base import ToolRegistry
from src.tools import (
    ReadPageTool,
    ReadPagesTool,
    ExecutePythonTool,
    CalendarReadTool,
    CalendarWriteTool,
    TasksReadTool,
    TasksWriteTool,
)
from src.orchestrators.search import UniversalSearchOrchestrator
from src.orchestrators.search.backends import (
    WebSearchBackend,
    EmailSearchBackend,
    CalendarSearchBackend,
    TasksSearchBackend,
)
from src.tools.universal_search import UniversalSearchTool


def setup_tools() -> ToolRegistry:
    registry = ToolRegistry()
    google_service = GoogleService()

    # Universal Search: web, calendar, tasks, email (when configured)
    search_backends: list = [
        WebSearchBackend(),
        CalendarSearchBackend(google_service),
        TasksSearchBackend(google_service),
    ]
    mcp_client = None
    if config.mcp_email_command:
        from src.mcp.client import MCPClient

        mcp_client = MCPClient(config.mcp_email_command, config.mcp_email_args)

        async def mcp_call(name: str, args: dict):
            return await mcp_client.call_tool(name, args)

        search_backends.append(EmailSearchBackend(mcp_call_tool=mcp_call))

    orchestrator = UniversalSearchOrchestrator(tools=search_backends, max_refinement_rounds=1)
    registry.register(UniversalSearchTool(orchestrator))

    registry.register(ReadPageTool())
    registry.register(ReadPagesTool())
    registry.register(ExecutePythonTool())
    registry.register(CalendarReadTool(google_service))
    registry.register(CalendarWriteTool(google_service))
    registry.register(TasksReadTool(google_service))
    registry.register(TasksWriteTool(google_service))

    if mcp_client is not None:
        from src.tools.email import (
            EmailGetThreadTool,
            EmailGetTool,
            EmailsSummarizeTool,
        )

        registry.register(EmailGetTool(mcp_client))
        registry.register(EmailGetThreadTool(mcp_client))
        registry.register(EmailsSummarizeTool(mcp_client))
        logger.info(f"Email tools registered (MCP: {config.mcp_email_command})")
    else:
        logger.debug("MCP_EMAIL_COMMAND not set; email tools disabled")

    return registry


def save_system_prompt_for_debug(registry: ToolRegistry) -> str:
    system_prompt = build_system_prompt(
        get_tools_text=registry.get_tools_prompt,
        get_tool_examples_text=registry.get_tools_examples,
    )
    system_prompt_path = config.data_dir.resolve() / "system_prompt.md"
    try:
        config.data_dir.mkdir(parents=True, exist_ok=True)
        system_prompt_path.write_text(system_prompt, encoding="utf-8")
        logger.info(f"🔧 System prompt saved to {system_prompt_path}")
    except OSError as e:
        logger.warning(f"Could not save system prompt to {system_prompt_path}: {e}")
    return system_prompt
