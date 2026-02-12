"""Tool registration, MCP wiring, and capability discovery at startup."""

from src.contracts.mcp_search_v1 import SearchCapabilities, FilterSpec, SourceClass
from src.core.config import config
from src.core.logger import logger
from src.core.prompts import build_system_prompt
from src.orchestrators.search.capabilities import CapabilityRegistry
from src.orchestrators.search.dispatcher import MCPSearchDispatcher
from src.orchestrators.search.orchestrator import UniversalSearchOrchestrator
from src.orchestrators.search.backends import (
    WebSearchBackend,
    CalendarSearchBackend,
    TasksSearchBackend,
)
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
from src.tools.universal_search import UniversalSearchTool


async def _discover_capabilities(
    mcp_call,
    connection_key: str,
    dispatcher: MCPSearchDispatcher,
    registry: CapabilityRegistry,
) -> list[str]:
    """Fetch capabilities from an MCP server and register them.

    Returns list of source names discovered.
    """
    try:
        caps_data = await dispatcher.fetch_capabilities(connection_key, mcp_call)
        if caps_data:
            registry.register_from_dict(caps_data)
            # Return source names
            if "sources" in caps_data:
                return [s["source_name"] for s in caps_data["sources"]]
            elif "source_name" in caps_data:
                return [caps_data["source_name"]]
    except Exception as e:
        logger.warning("Failed to discover capabilities from '%s': %s", connection_key, e)

    return []


def _register_direct_backend_capabilities(
    registry: CapabilityRegistry,
    backends: list,
) -> None:
    """Register capabilities for direct (non-MCP) backends."""
    for backend in backends:
        caps = SearchCapabilities(
            schema_version="1.0",
            source_name=backend.get_source_name(),
            source_class=backend.get_source_class(),
            supported_methods=backend.get_supported_methods(),
            supported_filters=[
                FilterSpec(**f) for f in backend.get_supported_filters()
            ],
            max_limit=50,
            default_limit=10,
        )
        registry.register(caps)


async def setup_tools() -> ToolRegistry:
    """Initialize all tools, MCP connections, and capability discovery."""
    registry = ToolRegistry()
    google_service = GoogleService()

    # Capability registry and MCP dispatcher
    capabilities = CapabilityRegistry()
    dispatcher = MCPSearchDispatcher()

    # Direct (non-MCP) backends
    direct_backends = [
        WebSearchBackend(),
        CalendarSearchBackend(google_service),
        TasksSearchBackend(google_service),
    ]

    # Register direct backend capabilities
    _register_direct_backend_capabilities(capabilities, direct_backends)

    # MCP Email connection
    mcp_email_client = None
    if config.mcp_email_command:
        from src.mcp.client import MCPClient

        mcp_email_client = MCPClient(config.mcp_email_command, config.mcp_email_args)

        async def mcp_email_call(name: str, args: dict):
            return await mcp_email_client.call_tool(name, args)

        # Discover capabilities
        email_sources = await _discover_capabilities(
            mcp_email_call, "email", dispatcher, capabilities,
        )
        if not email_sources:
            # Fallback: register known capabilities
            email_sources = ["email"]
            capabilities.register(SearchCapabilities(
                source_name="email",
                source_class=SourceClass.PERSONAL,
                supported_methods=["structured", "fulltext", "vector"],
                supported_filters=[
                    FilterSpec(name="from_email", type="string", operators=["eq", "contains"]),
                    FilterSpec(name="date_after", type="date", operators=["gte"]),
                    FilterSpec(name="date_before", type="date", operators=["lte"]),
                    FilterSpec(name="labels", type="string[]", operators=["in"]),
                    FilterSpec(name="has_attachments", type="boolean", operators=["eq"]),
                ],
            ))

        dispatcher.register_mcp("email", email_sources, mcp_email_call)
        logger.info("Email MCP registered: sources=%s", email_sources)

    # MCP Browser connection
    mcp_browser_client = None
    if config.mcp_browser_command:
        from src.mcp.client import MCPClient

        mcp_browser_client = MCPClient(config.mcp_browser_command, config.mcp_browser_args)

        async def mcp_browser_call(name: str, args: dict):
            return await mcp_browser_client.call_tool(name, args)

        # Discover capabilities
        browser_sources = await _discover_capabilities(
            mcp_browser_call, "browser", dispatcher, capabilities,
        )
        if not browser_sources:
            # Fallback
            browser_sources = ["browser_history", "browser_bookmarks"]
            capabilities.register(SearchCapabilities(
                source_name="browser_history",
                source_class=SourceClass.PERSONAL,
                supported_methods=["structured", "fulltext", "vector"],
                supported_filters=[
                    FilterSpec(name="date_after", type="date", operators=["gte"]),
                    FilterSpec(name="date_before", type="date", operators=["lte"]),
                    FilterSpec(name="domain", type="string", operators=["contains"]),
                ],
            ))
            capabilities.register(SearchCapabilities(
                source_name="browser_bookmarks",
                source_class=SourceClass.PERSONAL,
                supported_methods=["structured", "fulltext", "vector"],
                supported_filters=[
                    FilterSpec(name="folder", type="string", operators=["contains"]),
                ],
            ))

        dispatcher.register_mcp("browser", browser_sources, mcp_browser_call)
        logger.info("Browser MCP registered: sources=%s", browser_sources)

    # MCP WhatsApp connection
    if config.mcp_whatsapp_command:
        from src.mcp.client import MCPClient

        mcp_whatsapp_client = MCPClient(config.mcp_whatsapp_command, config.mcp_whatsapp_args)

        async def mcp_whatsapp_call(name: str, args: dict):
            return await mcp_whatsapp_client.call_tool(name, args)

        whatsapp_sources = await _discover_capabilities(
            mcp_whatsapp_call, "whatsapp", dispatcher, capabilities,
        )
        if not whatsapp_sources:
            capabilities.register(SearchCapabilities(
                source_name="whatsapp_messages",
                source_class=SourceClass.PERSONAL,
                supported_methods=["structured", "fulltext", "vector"],
                supported_filters=[
                    FilterSpec(name="chat_id", type="integer", operators=["eq"]),
                    FilterSpec(name="from_me", type="boolean", operators=["eq"]),
                    FilterSpec(name="date_after", type="date", operators=["gte"]),
                    FilterSpec(name="date_before", type="date", operators=["lte"]),
                ],
            ))
            whatsapp_sources = ["whatsapp_messages"]

        dispatcher.register_mcp("whatsapp", whatsapp_sources, mcp_whatsapp_call)
        logger.info("WhatsApp MCP registered: sources=%s", whatsapp_sources)

    # Build orchestrator
    orchestrator = UniversalSearchOrchestrator(
        capabilities=capabilities,
        dispatcher=dispatcher,
        direct_backends=direct_backends,
        max_refinement_rounds=1,
    )
    registry.register(UniversalSearchTool(orchestrator))

    # Other tools
    registry.register(ReadPageTool())
    registry.register(ReadPagesTool())
    registry.register(ExecutePythonTool())
    registry.register(CalendarReadTool(google_service))
    registry.register(CalendarWriteTool(google_service))
    registry.register(TasksReadTool(google_service))
    registry.register(TasksWriteTool(google_service))

    # Email direct tools (get, thread, summarize) via MCP
    if mcp_email_client is not None:
        from src.tools.email import (
            EmailGetTool,
            EmailGetThreadTool,
            EmailsSummarizeTool,
        )
        registry.register(EmailGetTool(mcp_email_client))
        registry.register(EmailGetThreadTool(mcp_email_client))
        registry.register(EmailsSummarizeTool(mcp_email_client))
        logger.info("Email tools registered (MCP: %s)", config.mcp_email_command)
    else:
        logger.debug("MCP_EMAIL_COMMAND not set; email tools disabled")

    logger.info(
        "Bootstrap complete: %s tools, %s sources (%s)",
        len(registry.list_tools()),
        len(capabilities.all_sources()),
        ", ".join(capabilities.all_sources()),
    )

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
        logger.info("System prompt saved to %s", system_prompt_path)
    except OSError as e:
        logger.warning("Could not save system prompt to %s: %s", system_prompt_path, e)
    return system_prompt
