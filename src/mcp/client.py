"""Generic MCP client for STDIO transport. Reusable across MCP servers."""

import json
from contextlib import AsyncExitStack
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from src.core.logger import logger


def _extract_text_from_content(content: list) -> str:
    parts: list[str] = []
    for item in content:
        if hasattr(item, "text") and item.text:
            parts.append(item.text)
        elif isinstance(item, dict) and item.get("type") == "text":
            parts.append(item.get("text", ""))
    return "".join(parts)


class MCPClient:
    def __init__(self, command: str, args: list[str]):
        if not command:
            raise ValueError("MCP command cannot be empty")
        self._command = command
        self._args = args
        self._session: ClientSession | None = None
        self._exit_stack: AsyncExitStack | None = None

    async def _ensure_connected(self) -> None:
        if self._session is not None:
            return
        self._exit_stack = AsyncExitStack()
        server_params = StdioServerParameters(
            command=self._command,
            args=self._args,
            env=None,
        )
        stdio_transport = await self._exit_stack.enter_async_context(stdio_client(server_params))
        read_stream, write_stream = stdio_transport
        self._session = await self._exit_stack.enter_async_context(
            ClientSession(read_stream, write_stream)
        )
        await self._session.initialize()
        logger.info(f"MCP: connected  {self._command}  {' '.join(self._args)}")

    async def call_tool(
        self,
        name: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        try:
            await self._ensure_connected()
            assert self._session is not None
            result = await self._session.call_tool(name, arguments)
            text = _extract_text_from_content(result.content)
            if not text.strip():
                return {"success": False, "error": "MCP tool returned empty response"}

            data = json.loads(text)
            if isinstance(data, dict) and "success" in data:
                return data
            return {"success": True, "output": text}
        except json.JSONDecodeError as e:
            logger.warning(f"MCP: tool returned non-JSON  {e}")
            return {"success": False, "error": f"Invalid tool response: {e!s}"}
        except Exception as e:
            logger.exception(f"MCP: call_tool failed  {name}")
            return {"success": False, "error": f"MCP call failed: {e!s}"}

    async def close(self) -> None:
        if self._exit_stack is not None:
            try:
                await self._exit_stack.aclose()
            except RuntimeError as e:
                # Swallow anyio cancel scope runtime errors during shutdown
                if "cancel scope" not in str(e):
                    raise
            self._exit_stack = None
            self._session = None
            logger.info("MCP: disconnected")
