"""Generic MCP client for STDIO transport. Reusable across MCP servers."""

import json
import os
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
        self._stderr_devnull: Any = None

    async def _ensure_connected(self) -> None:
        if self._session is not None:
            return
        self._exit_stack = AsyncExitStack()
        self._stderr_devnull = open(os.devnull, "w")
        server_params = StdioServerParameters(
            command=self._command,
            args=self._args,
            env=None,
        )
        stdio_transport = await self._exit_stack.enter_async_context(
            stdio_client(server_params, errlog=self._stderr_devnull)
        )
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

            if getattr(result, "isError", False):
                text = _extract_text_from_content(result.content)
                return {
                    "success": False,
                    "error": text.strip() or "Tool returned error",
                }

            # Prefer structuredContent (MCP SDK can return tool result as dict)
            if getattr(result, "structuredContent", None) and isinstance(
                result.structuredContent, dict
            ):
                data = dict(result.structuredContent)
                if "success" not in data:
                    data.setdefault("success", True)
                return data

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
            except (GeneratorExit, RuntimeError) as e:
                if isinstance(e, RuntimeError) and "cancel scope" not in str(e):
                    raise
                # Swallow GeneratorExit / anyio cancel-scope errors during shutdown
            except BaseExceptionGroup as eg:
                # anyio TaskGroup can raise this; suppress if all sub-exceptions are shutdown-related
                def _is_shutdown_exc(exc: BaseException) -> bool:
                    if isinstance(exc, GeneratorExit):
                        return True
                    if isinstance(exc, RuntimeError) and "cancel scope" in str(exc):
                        return True
                    return False

                try:
                    subs: tuple[BaseException, ...] | list[BaseException] = (
                        eg.exceptions
                    )
                except AttributeError:
                    subs = [eg]
                if not all(_is_shutdown_exc(e) for e in subs):
                    raise
            self._exit_stack = None
            self._session = None
        if self._stderr_devnull is not None:
            try:
                self._stderr_devnull.close()
            except OSError:
                pass
            self._stderr_devnull = None
        logger.info("MCP: disconnected")
