"""Parse JSON tool calls from LLM response; validate with per-tool Pydantic models."""

import json
import re
from typing import Annotated, Literal

from pydantic import BaseModel, Field, TypeAdapter, field_validator


class CalendarReadCall(BaseModel):
    tool: Literal["calendar_read"] = "calendar_read"
    action: str = ""
    range_preset: str = "next_7_days"
    calendar_id: str = ""
    event_id: str = ""


class CalendarWriteCall(BaseModel):
    tool: Literal["calendar_write"] = "calendar_write"
    action: str = ""
    event_id: str = ""
    calendar_id: str = ""
    title: str = ""
    start: str = ""
    end: str = ""
    description: str = ""
    location: str = ""
    reminders: str = ""
    recurrence: str = ""
    visibility: str = "default"
    attendees: str = ""
    color_id: str = ""
    confirm_pending_id: str = ""


class UniversalSearchCall(BaseModel):
    tool: Literal["universal_search"] = "universal_search"
    max_results: str = ""


class ReadPageCall(BaseModel):
    tool: Literal["read_page"] = "read_page"
    url: str = ""
    topic: str = "Summarize the key information"


class ReadPagesCall(BaseModel):
    tool: Literal["read_pages"] = "read_pages"
    urls: str = ""
    topic: str = "Summarize the key information"


class TasksReadCall(BaseModel):
    tool: Literal["tasks_read"] = "tasks_read"
    action: str = ""
    list_id: str = ""
    task_id: str = ""
    show_completed: str = "true"


class TasksWriteCall(BaseModel):
    tool: Literal["tasks_write"] = "tasks_write"
    action: str = ""
    task_id: str = ""
    list_id: str = ""
    title: str = ""
    notes: str = ""
    due: str = ""
    status: str = ""
    confirm_pending_id: str = ""


class ExecutePythonCall(BaseModel):
    tool: Literal["execute_python"] = "execute_python"
    code: str = ""


class GetEmailCall(BaseModel):
    tool: Literal["email_get"] = "email_get"
    email_id: str = ""
    account_id: str = ""


class GetEmailThreadCall(BaseModel):
    tool: Literal["email_get_thread"] = "email_get_thread"
    thread_id: str = ""
    account_id: str = ""


class SummarizeEmailsCall(BaseModel):
    tool: Literal["emails_summarize"] = "emails_summarize"
    email_ids: str = ""
    thread_id: str = ""
    account_id: str = ""

    @field_validator("email_ids", mode="before")
    @classmethod
    def coerce_email_ids(cls, v: object) -> str:
        return (
            json.dumps(v) if isinstance(v, list) else (str(v) if v is not None else "")
        )


ToolCallUnion = Annotated[
    CalendarReadCall
    | CalendarWriteCall
    | UniversalSearchCall
    | ReadPageCall
    | ReadPagesCall
    | TasksReadCall
    | TasksWriteCall
    | ExecutePythonCall
    | GetEmailCall
    | GetEmailThreadCall
    | SummarizeEmailsCall,
    Field(discriminator="tool"),
]

ToolCall = (
    CalendarReadCall
    | CalendarWriteCall
    | UniversalSearchCall
    | ReadPageCall
    | ReadPagesCall
    | TasksReadCall
    | TasksWriteCall
    | ExecutePythonCall
    | GetEmailCall
    | GetEmailThreadCall
    | SummarizeEmailsCall
)

_tool_call_adapter: TypeAdapter[ToolCallUnion] = TypeAdapter(ToolCallUnion)


def _extract_fenced_json(text: str) -> tuple[str | None, int]:
    """Extract first ```json...``` block; return (content, end_index) or (None, -1)."""
    match = re.search(r"```(?:json)?\s*(.*?)```", text.strip(), re.DOTALL)
    if not match:
        return None, -1
    end_index = match.end()
    return match.group(1).strip(), end_index


def _normalize_json(s: str) -> str:
    """Remove trailing commas before } or ] so malformed JSON still parses."""
    s = re.sub(r",\s*}", "}", s)
    s = re.sub(r",\s*]", "]", s)
    return s


def _coerce_tool_dict(data: dict) -> dict:
    """Coerce values to str for Pydantic str fields."""
    return {k: str(v) if v is not None else "" for k, v in data.items()}


def parse_tool_call_from_response(
    response: str,
    valid_tool_names: set[str],
) -> tuple[ToolCall | None, int]:
    """Parse first ```json...``` block; return (parsed_model, end_index) or (None, -1)."""
    raw, end_index = _extract_fenced_json(response)
    if not raw or end_index < 0:
        return None, -1
    raw = _normalize_json(raw)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None, -1
    if not isinstance(data, dict):
        return None, -1
    tool_name = data.get("tool")
    if not isinstance(tool_name, str) or tool_name not in valid_tool_names:
        return None, -1
    data = _coerce_tool_dict(data)
    try:
        parsed: ToolCall = _tool_call_adapter.validate_python(data)
        return parsed, end_index
    except Exception:
        return None, -1


def get_tool_name(parsed: ToolCall) -> str:
    return parsed.tool


def get_tool_arguments(parsed: ToolCall) -> dict[str, str]:
    """Return kwargs for Tool.execute(); excludes 'tool'."""
    d = parsed.model_dump()
    d.pop("tool", None)
    return {k: (v if isinstance(v, str) else str(v)) for k, v in d.items()}


def parse_legacy_tool_call(
    clean_response: str,
    valid_tool_names: set[str],
) -> tuple[str, dict[str, str], str] | None:
    match = re.search(
        r'<(tool|tool_call|[\w_]+)\s+(?:name="([^"]+)"\s*)?(.*?)\s*/>',
        clean_response,
        re.DOTALL,
    )
    if not match:
        return None
    tag_name = match.group(1)
    name_attr = match.group(2)
    args_str = match.group(3)
    tool_name = name_attr if name_attr else tag_name
    if tool_name not in valid_tool_names and tag_name in valid_tool_names:
        tool_name = tag_name
    if tool_name not in valid_tool_names:
        return None
    args = {}
    for k, v in re.findall(r'([\w_]+)="([^"]*)"', args_str, re.DOTALL):
        v_clean = (
            v.replace("\\n", "\n")
            .replace('\\"', '"')
            .replace("\\'", "'")
            .replace("\\\\", "\\")
        )
        args[k] = v_clean
    for k, v in re.findall(r"([\w_]+)=([^\s/\"'>=]+)", args_str):
        if k not in args and v:
            args[k] = v
    tag_end = match.end()
    assistant_content = clean_response[:tag_end].strip()
    return (tool_name, args, assistant_content)
