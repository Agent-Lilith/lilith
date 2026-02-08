"""Email tools via MCP (lilith-emails). Static binding, no schema discovery."""

import json
from typing import Any

from src.core.config import config
from src.core.logger import logger
from src.core.prompts import get_tool_description, get_tool_examples
from src.mcp.client import MCPClient
from src.tools.base import Tool, ToolResult

MCP_EMAIL_GET = "email_get"
MCP_EMAIL_GET_THREAD = "email_get_thread"
MCP_EMAILS_SUMMARIZE = "emails_summarize"


def _parse_int(s: str | None, default: int) -> int:
    if not s or not str(s).strip():
        return default
    try:
        return int(str(s).strip())
    except ValueError:
        return default


def _parse_bool(s: str | None) -> bool | None:
    if s is None or str(s).strip() == "":
        return None
    v = str(s).strip().lower()
    if v in ("true", "1", "yes"):
        return True
    if v in ("false", "0", "no"):
        return False
    return None


def _parse_list(s: str | None) -> list[str] | None:
    if not s or not str(s).strip():
        return None
    raw = str(s).strip()
    if raw.startswith("["):
        try:
            out = json.loads(raw)
            return out if isinstance(out, list) else [str(x) for x in out]
        except json.JSONDecodeError:
            pass
    return [x.strip() for x in raw.split(",") if x.strip()]


def _tool_result_from_mcp(data: dict[str, Any]) -> ToolResult:
    if data.get("success"):
        return ToolResult.ok(data.get("output", ""))
    return ToolResult.fail(data.get("error", "Unknown error"))


class _BaseEmailTool(Tool):
    """Base for email tools. Shares MCP client and common logic."""

    def __init__(self, mcp_client: MCPClient):
        self._mcp = mcp_client

    def _default_account_id(self) -> int:
        return config.mcp_email_account_id

    async def close(self) -> None:
        await self._mcp.close()


class EmailGetTool(_BaseEmailTool):
    @property
    def name(self) -> str:
        return "email_get"

    @property
    def description(self) -> str:
        return get_tool_description(self.name)

    @property
    def parameters(self) -> dict[str, str]:
        return {
            "email_id": "Gmail message ID",
            "account_id": "Optional. Restrict to account; omit for default",
        }

    def get_examples(self) -> list[str]:
        return get_tool_examples(self.name)

    async def execute(
        self,
        email_id: str = "",
        account_id: str = "",
    ) -> ToolResult:
        if not email_id or not email_id.strip():
            return ToolResult.fail("email_id is required")
        args: dict[str, Any] = {
            "email_id": email_id.strip(),
            "account_id": _parse_int(account_id, self._default_account_id())
            if account_id
            else self._default_account_id(),
        }
        logger.tool_execute(self.name, args)
        result = await self._mcp.call_tool(MCP_EMAIL_GET, args)
        return _tool_result_from_mcp(result)


class EmailGetThreadTool(_BaseEmailTool):
    @property
    def name(self) -> str:
        return "email_get_thread"

    @property
    def description(self) -> str:
        return get_tool_description(self.name)

    @property
    def parameters(self) -> dict[str, str]:
        return {
            "thread_id": "Gmail thread ID",
            "account_id": "Optional. Restrict to account; omit for default",
        }

    def get_examples(self) -> list[str]:
        return get_tool_examples(self.name)

    async def execute(
        self,
        thread_id: str = "",
        account_id: str = "",
    ) -> ToolResult:
        if not thread_id or not thread_id.strip():
            return ToolResult.fail("thread_id is required")
        args: dict[str, Any] = {
            "thread_id": thread_id.strip(),
            "account_id": _parse_int(account_id, self._default_account_id())
            if account_id
            else self._default_account_id(),
        }
        logger.tool_execute(self.name, args)
        result = await self._mcp.call_tool(MCP_EMAIL_GET_THREAD, args)
        return _tool_result_from_mcp(result)


class EmailsSummarizeTool(_BaseEmailTool):
    @property
    def name(self) -> str:
        return "emails_summarize"

    @property
    def description(self) -> str:
        return get_tool_description(self.name)

    @property
    def parameters(self) -> dict[str, str]:
        return {
            "email_ids": "Optional. Comma-separated or JSON array of Gmail message IDs",
            "thread_id": "Optional. Summarize this thread instead of email_ids",
            "account_id": "Optional. Restrict to account; omit for default",
        }

    def get_examples(self) -> list[str]:
        return get_tool_examples(self.name)

    async def execute(
        self,
        email_ids: str = "",
        thread_id: str = "",
        account_id: str = "",
    ) -> ToolResult:
        ids = _parse_list(email_ids)
        tid = thread_id.strip() if thread_id else None
        if not ids and not tid:
            return ToolResult.fail("Provide thread_id or email_ids")
        args: dict[str, Any] = {
            "account_id": _parse_int(account_id, self._default_account_id())
            if account_id
            else self._default_account_id(),
        }
        if ids:
            args["email_ids"] = ids
        if tid:
            args["thread_id"] = tid
        logger.tool_execute(self.name, {k: v for k, v in args.items() if k != "email_ids"})
        result = await self._mcp.call_tool(MCP_EMAILS_SUMMARIZE, args)
        return _tool_result_from_mcp(result)
