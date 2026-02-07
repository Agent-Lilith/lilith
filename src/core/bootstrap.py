"""Tool registration and system prompt setup at startup."""

from src.core.config import config
from src.core.logger import logger
from src.core.prompts import build_system_prompt
from src.services.google_service import GoogleService
from src.tools.base import ToolRegistry
from src.tools import (
    SearchTool,
    ReadPageTool,
    ReadPagesTool,
    ExecutePythonTool,
    CalendarReadTool,
    CalendarWriteTool,
    TasksReadTool,
    TasksWriteTool,
)


def setup_tools() -> ToolRegistry:
    registry = ToolRegistry()
    google_service = GoogleService()
    registry.register(SearchTool())
    registry.register(ReadPageTool())
    registry.register(ReadPagesTool())
    registry.register(ExecutePythonTool())
    registry.register(CalendarReadTool(google_service))
    registry.register(CalendarWriteTool(google_service))
    registry.register(TasksReadTool(google_service))
    registry.register(TasksWriteTool(google_service))
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
