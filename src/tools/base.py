"""Base Tool class, ToolResult, and ToolRegistry."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

CONFIRM_REQUIRED_PREFIX = "CONFIRM_REQUIRED|"


def parse_pending_confirm(result_content: str) -> dict | None:
    if CONFIRM_REQUIRED_PREFIX not in result_content:
        return None
    rest = result_content.split(CONFIRM_REQUIRED_PREFIX, 1)[1]
    parts = rest.split("|", 2)
    return {
        "tool": parts[0].strip() if parts else "calendar_write",
        "pending_id": parts[1].strip() if len(parts) > 1 else "",
        "summary": parts[2].strip() if len(parts) > 2 else "Confirm?",
    }


def format_confirm_required(tool_name: str, pending_id: str, summary_msg: str) -> str:
    return f"{CONFIRM_REQUIRED_PREFIX}{tool_name}|{pending_id}|{summary_msg}"


@dataclass
class ToolResult:
    success: bool
    output: str
    error: str = ""
    
    @classmethod
    def ok(cls, output: str) -> "ToolResult":
        return cls(success=True, output=output)

    @classmethod
    def fail(cls, error: str) -> "ToolResult":
        return cls(success=False, output="", error=error)


class Tool(ABC):
    @property
    @abstractmethod
    def name(self) -> str:
        pass

    @property
    @abstractmethod
    def description(self) -> str:
        pass

    @property
    @abstractmethod
    def parameters(self) -> dict[str, str]:
        pass

    @abstractmethod
    async def execute(self, **kwargs) -> ToolResult:
        pass

    def get_schema(self) -> str:
        if not self.parameters:
            return f"- **{self.name}**: {self.description}"
        param_lines = [f"    - `{k}`: {v}" for k, v in self.parameters.items()]
        params_str = "\n".join(param_lines)
        return f"- **{self.name}**: {self.description}\n  Parameters:\n{params_str}"

    def get_examples(self) -> list[str]:
        return []


class ToolRegistry:
    def __init__(self):
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        if not isinstance(tool, Tool):
            raise TypeError(f"Expected Tool instance, got {type(tool)}")
        self._tools[tool.name] = tool
    
    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def list_tools(self) -> list[Tool]:
        return list(self._tools.values())

    def get_tools_prompt(self) -> str:
        if not self._tools:
            return "No tools available yet."
        
        lines = ["Available tools:"]
        for tool in self._tools.values():
            lines.append(tool.get_schema())
        return "\n".join(lines)

    def get_tools_list(self) -> str:
        if not self._tools:
            return "- None yet"
        return "\n".join(f"- {t.name}: {t.description}" for t in self._tools.values())

    def get_tools_examples(self) -> str:
        lines: list[str] = []
        for tool in self._tools.values():
            for ex in tool.get_examples():
                lines.append(f"```json\n{ex}\n```")
        if not lines:
            return ""
        return "Examples:\n" + "\n".join(lines)
