"""Shared helpers for tool confirmation flow (calendar_write, tasks_write)."""

from src.tools.base import ToolRegistry, ToolResult


def run_pending_confirm(
    tool_registry: ToolRegistry,
    tool_name: str,
    pending_id: str,
) -> ToolResult | None:
    if not pending_id or not tool_name:
        return None
    tool = tool_registry.get(tool_name)
    if not tool or not hasattr(tool, "execute_pending"):
        return None
    return tool.execute_pending(pending_id)


def get_confirmation_result(
    tool_registry: ToolRegistry,
    pending: dict,
    confirmed: bool,
) -> tuple[str, bool | None]:
    if not confirmed:
        return ("Cancelled.", None)
    tool_name = pending.get("tool", "calendar_write")
    pending_id = pending.get("pending_id", "")
    if not pending_id:
        return ("Cancelled.", None)
    result = run_pending_confirm(tool_registry, tool_name, pending_id)
    if result:
        return (result.output if result.success else result.error, result.success)
    return ("Tool or method not found.", False)


async def run_confirmation_flow(
    tool_registry: ToolRegistry,
    pending: dict,
    *,
    prompt_user: Callable[[str], Awaitable[bool]],
    on_result: Callable[[str, bool | None], Awaitable[None]],
) -> None:
    summary = pending.get("summary", "Proceed?")
    confirmed = await prompt_user(summary)
    message, success = get_confirmation_result(tool_registry, pending, confirmed)
    await on_result(message, success)
