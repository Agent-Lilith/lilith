"""Load and compose prompts from prompts/ (fragment list, substitution, validation)."""

from collections.abc import Callable
from pathlib import Path

from src.core.config import config
from src.core.logger import logger

REQUIRED_SYSTEM_PLACEHOLDERS = ("{tools}", "{tool_examples}")
DATE_CONTEXT_PLACEHOLDER = "{date_context}"


def _prompts_dir() -> Path:
    return config.prompts_dir


def _read_fragment(name: str) -> str:
    path = _prompts_dir() / name.strip()
    if not path.exists():
        raise FileNotFoundError(f"Prompt fragment not found: {path}")
    return path.read_text().rstrip()


def load_system_fragments() -> str:
    list_path = _prompts_dir() / "system_fragments.md"
    if not list_path.exists():
        raise FileNotFoundError(f"Fragment list not found: {list_path}")
    lines = [ln.strip() for ln in list_path.read_text().splitlines() if ln.strip()]
    parts = []
    for name in lines:
        parts.append(_read_fragment(name))
    return "\n\n".join(parts)


def build_system_prompt(
    get_tools_text: Callable[[], str], get_tool_examples_text: Callable[[], str]
) -> str:
    composed = load_system_fragments()
    composed = composed.replace("{tools}", get_tools_text())
    examples_block = get_tool_examples_text()
    composed = composed.replace("{tool_examples}", examples_block)
    for placeholder in REQUIRED_SYSTEM_PLACEHOLDERS:
        if placeholder in composed:
            raise ValueError(
                f"System prompt still contains unresolved placeholder {placeholder}. "
                "Check that the fragment list and tool registry are correct."
            )

    logger.debug("System prompt composed from fragments and validated.")
    return composed


def fill_date_context(system_prompt: str, date_context: str) -> str:
    if DATE_CONTEXT_PLACEHOLDER not in system_prompt:
        return system_prompt
    return system_prompt.replace(DATE_CONTEXT_PLACEHOLDER, date_context)


def load_search_prompt(name: str) -> str:
    path = _prompts_dir() / "search" / f"{name}.md"
    if not path.exists():
        raise FileNotFoundError(f"Search prompt not found: {path}")
    return path.read_text().rstrip()


def load_worker_prompt() -> str:
    path = _prompts_dir() / "worker.md"
    if not path.exists():
        raise FileNotFoundError(f"Worker prompt not found: {path}")
    return path.read_text()


def render_worker_prompt(task_description: str, instruction: str, data: str) -> str:
    template = load_worker_prompt()
    return (
        template.replace("{task_description}", task_description)
        .replace("{instruction}", instruction)
        .replace("{data}", data)
    )


_TOOL_CACHE: dict[str, tuple[str, list[str]]] = {}


def _parse_tool_md(content: str) -> tuple[str, list[str]]:
    """Parse ## Description and ## Examples; return (description, examples)."""
    desc = ""
    examples: list[str] = []
    if "## Description" in content:
        parts = content.split("## Description", 1)[1].split("## Examples", 1)
        desc = (parts[0].strip() or "").strip()
        if len(parts) > 1:
            raw = parts[1].strip()
            if raw.startswith("```"):
                lines = raw.splitlines()
                inner: list[str] = []
                in_block = False
                for line in lines:
                    if line.strip().startswith("```"):
                        if in_block:
                            break
                        in_block = True
                        continue
                    if in_block and line.strip():
                        inner.append(line.strip())
                examples = inner
            else:
                for line in raw.splitlines():
                    line = line.strip()
                    if line.startswith("- "):
                        line = line[2:].strip()
                    if line and not line.startswith("#"):
                        examples.append(line)
    else:
        desc = content.strip()
    return desc, examples


def get_tool_description(tool_name: str) -> str:
    if tool_name in _TOOL_CACHE:
        return _TOOL_CACHE[tool_name][0]
    path = _prompts_dir() / "tools" / f"{tool_name}.md"
    if not path.exists():
        return ""
    text = path.read_text()
    desc, examples = _parse_tool_md(text)
    _TOOL_CACHE[tool_name] = (desc, examples)
    return desc


def get_tool_examples(tool_name: str) -> list[str]:
    if tool_name in _TOOL_CACHE:
        return _TOOL_CACHE[tool_name][1]
    path = _prompts_dir() / "tools" / f"{tool_name}.md"
    if not path.exists():
        return []
    text = path.read_text()
    desc, examples = _parse_tool_md(text)
    _TOOL_CACHE[tool_name] = (desc, examples)
    return examples
