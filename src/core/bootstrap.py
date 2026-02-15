"""Tool registration, MCP wiring, and capability discovery at startup."""

from src.contracts.mcp_search_v1 import (
    CapabilityTier,
    FilterSpec,
    SearchCapabilities,
)
from src.core.config import config
from src.core.logger import logger
from src.core.prompts import build_system_prompt
from src.orchestrators.search.backends import (
    CalendarSearchBackend,
    TasksSearchBackend,
    WebSearchBackend,
)
from src.orchestrators.search.capabilities import CapabilityRegistry
from src.orchestrators.search.dispatcher import MCPSearchDispatcher
from src.orchestrators.search.orchestrator import UniversalSearchOrchestrator
from src.services.google_service import GoogleService
from src.tools import (
    CalendarReadTool,
    CalendarWriteTool,
    ExecutePythonTool,
    ReadPagesTool,
    ReadPageTool,
    TasksReadTool,
    TasksWriteTool,
)
from src.tools.base import ToolRegistry
from src.tools.universal_search import UniversalSearchTool


async def _discover_capabilities(
    mcp_call,
    connection_key: str,
    dispatcher: MCPSearchDispatcher,
    registry: CapabilityRegistry,
) -> tuple[list[str], dict[str, dict[str, object]]]:
    """Fetch capabilities from an MCP server and register them.

    Returns (source names, request routing args map by source).
    """
    try:
        caps_data = await dispatcher.fetch_capabilities(connection_key, mcp_call)
        if caps_data:
            registry.register_from_dict(caps_data)
            source_routing_args: dict[str, dict[str, object]] = {}
            if "sources" in caps_data:
                names: list[str] = []
                for source_data in caps_data["sources"]:
                    source_name = source_data["source_name"]
                    names.append(source_name)
                    routing_args = source_data.get("request_routing_args")
                    if isinstance(routing_args, dict) and routing_args:
                        source_routing_args[source_name] = routing_args
                return names, source_routing_args
            elif "source_name" in caps_data:
                source_name = caps_data["source_name"]
                routing_args = caps_data.get("request_routing_args")
                if isinstance(routing_args, dict) and routing_args:
                    source_routing_args[source_name] = routing_args
                return [source_name], source_routing_args
    except Exception as e:
        logger.warning(
            "Failed to discover capabilities from '%s': %s", connection_key, e
        )

    return [], {}


# User-facing labels for direct backends (no MCP discovery)
_DIRECT_BACKEND_DISPLAY_LABELS: dict[str, str] = {
    "calendar": "Calendar",
    "tasks": "Tasks",
    "web": "Web",
}


def _register_direct_backend_capabilities(
    registry: CapabilityRegistry,
    backends: list,
) -> None:
    """Register capabilities for direct (non-MCP) backends."""
    for backend in backends:
        source_name = backend.get_source_name()
        caps = SearchCapabilities(
            schema_version="1.0",
            source_name=source_name,
            source_class=backend.get_source_class(),
            supported_methods=backend.get_supported_methods(),
            supported_filters=[
                FilterSpec(**f) for f in backend.get_supported_filters()
            ],
            max_limit=50,
            default_limit=10,
            display_label=_DIRECT_BACKEND_DISPLAY_LABELS.get(source_name),
            latency_tier=CapabilityTier.MEDIUM,
            quality_tier=CapabilityTier.MEDIUM,
            cost_tier=CapabilityTier.MEDIUM,
        )
        registry.register(caps)


async def setup_tools() -> tuple[ToolRegistry, list[object]]:
    """Initialize all tools, MCP connections, and capability discovery.

    Returns a tool registry and a list of external resources that need explicit shutdown
    (e.g., MCP clients).
    """
    registry = ToolRegistry()
    google_service = GoogleService()
    closables: list[object] = []

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
        from src.mcp_client.client import MCPClient

        mcp_email_client = MCPClient(
            config.mcp_email_command,
            config.mcp_email_args,
            env=config.mcp_forward_env,
        )
        closables.append(mcp_email_client)

        async def mcp_email_call(name: str, args: dict):
            return await mcp_email_client.call_tool(name, args)

        # Discover capabilities
        email_sources, email_routing_args = await _discover_capabilities(
            mcp_email_call,
            "email",
            dispatcher,
            capabilities,
        )
        if not email_sources:
            raise RuntimeError(
                "MCP email capability discovery failed; refusing to start with hidden fallback."
            )

        dispatcher.register_mcp(
            "email",
            email_sources,
            mcp_email_call,
            request_routing_args=email_routing_args,
        )
        logger.info("Email MCP registered: sources=%s", email_sources)

    # MCP Browser connection
    mcp_browser_client = None
    if config.mcp_browser_command:
        from src.mcp_client.client import MCPClient

        mcp_browser_client = MCPClient(
            config.mcp_browser_command,
            config.mcp_browser_args,
            env=config.mcp_forward_env,
        )
        closables.append(mcp_browser_client)

        async def mcp_browser_call(name: str, args: dict):
            return await mcp_browser_client.call_tool(name, args)

        # Discover capabilities
        browser_sources, browser_routing_args = await _discover_capabilities(
            mcp_browser_call,
            "browser",
            dispatcher,
            capabilities,
        )
        if not browser_sources:
            raise RuntimeError(
                "MCP browser capability discovery failed; refusing to start with hidden fallback."
            )

        dispatcher.register_mcp(
            "browser",
            browser_sources,
            mcp_browser_call,
            request_routing_args=browser_routing_args,
        )
        logger.info("Browser MCP registered: sources=%s", browser_sources)

    # MCP WhatsApp connection
    if config.mcp_whatsapp_command:
        from src.mcp_client.client import MCPClient

        mcp_whatsapp_client = MCPClient(
            config.mcp_whatsapp_command,
            config.mcp_whatsapp_args,
            env=config.mcp_forward_env,
        )
        closables.append(mcp_whatsapp_client)

        async def mcp_whatsapp_call(name: str, args: dict):
            return await mcp_whatsapp_client.call_tool(name, args)

        whatsapp_sources, whatsapp_routing_args = await _discover_capabilities(
            mcp_whatsapp_call,
            "whatsapp",
            dispatcher,
            capabilities,
        )
        if not whatsapp_sources:
            raise RuntimeError(
                "MCP WhatsApp capability discovery failed; refusing to start with hidden fallback."
            )

        dispatcher.register_mcp(
            "whatsapp",
            whatsapp_sources,
            mcp_whatsapp_call,
            request_routing_args=whatsapp_routing_args,
        )
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
            EmailGetThreadTool,
            EmailGetTool,
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

    return registry, closables


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
