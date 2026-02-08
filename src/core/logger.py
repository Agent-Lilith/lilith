"""Structured logging: console, file, and external-call logs.
"""

import contextvars
import json
import logging
import threading
import time
from pathlib import Path
from datetime import datetime
from typing import Any
from dataclasses import dataclass, asdict
import textwrap
import shutil

from src.core.config import config


def _format_duration(seconds: float) -> str:
    """Human-readable duration, e.g. 1m 15s, 12.3s, <0.1s."""
    if seconds < 0:
        return "0s"
    if seconds >= 60:
        m = int(seconds // 60)
        s = seconds % 60
        if s < 0.05:
            return f"{m}m"
        return f"{m}m {s:.0f}s" if s >= 1 else f"{m}m {s:.1f}s"
    if seconds >= 1:
        return f"{seconds:.1f}s"
    if seconds >= 0.05:
        return f"{seconds:.1f}s"
    if seconds > 0:
        return "<0.1s"
    return "0s"


def _short_reason(reason: str | None, max_len: int = 80) -> str:
    """One-line short reason for console (failed tool)."""
    if not reason or not reason.strip():
        return ""
    s = reason.strip().replace("\n", " ").strip()
    return s[:max_len] + "..." if len(s) > max_len else s


_TURN_SEP = "  " + "─" * 42 + "  "
_llm_ctx: contextvars.ContextVar[tuple[float, str] | None] = contextvars.ContextVar("llm_request", default=None)
_log_in_turn: contextvars.ContextVar[bool] = contextvars.ContextVar("log_in_turn", default=False)
_log_in_tool: contextvars.ContextVar[bool] = contextvars.ContextVar("log_in_tool", default=False)
_log_tool_start: contextvars.ContextVar[float | None] = contextvars.ContextVar("log_tool_start", default=None)


@dataclass
class LogEvent:
    event_type: str
    timestamp: str
    data: dict[str, Any]
    
    def to_dict(self) -> dict:
        return asdict(self)
    
    def to_json(self) -> str:
        return json.dumps(self.to_dict(), default=str)


class LilithLogger:
    def __init__(self):
        config.logs_dir.mkdir(parents=True, exist_ok=True)
        self.log_file = config.logs_dir / "agent.log"
        self.external_dir = config.logs_dir / "external"
        self.external_dir.mkdir(exist_ok=True)
        self._file_lock = threading.Lock()
        self._log_file_handle = open(self.log_file, "a", encoding="utf-8")
        self._setup_console_logger()
        self._llm_was_local: bool = False

    def _setup_console_logger(self):
        self.console = logging.getLogger("lilith")
        self.console.setLevel(logging.DEBUG)
        if not self.console.handlers:
            handler = logging.StreamHandler()
            handler.setLevel(logging.INFO)
            formatter = logging.Formatter(
                "%(asctime)s │ %(message)s",
                datefmt="%H:%M:%S"
            )
            handler.setFormatter(formatter)
            self.console.addHandler(handler)

    def log_event(self, event: LogEvent) -> None:
        with self._file_lock:
            self._log_file_handle.write(event.to_json() + "\n")
            self._log_file_handle.flush()
    
    def _timestamp(self) -> str:
        return datetime.now().isoformat()

    def _prefix(self) -> str:
        """Turn indent (under user) + optional tool indent (Worker under tool)."""
        if _log_in_tool.get():
            return "  │   └ "
        if _log_in_turn.get():
            return "  │ "
        return ""

    def _format_tool_args(self, args: dict) -> str:
        """Shorten args for console so long code/urls don't flood the log."""
        max_val = 72
        out = []
        for k, v in (args or {}).items():
            s = repr(v)
            if len(s) > max_val:
                s = s[: max_val - 3].rstrip() + "..."
            out.append(f"{k}={s}")
        return ", ".join(out)

    def user_input(self, message: str):
        _log_in_turn.set(True)
        event = LogEvent(
            event_type="USER_INPUT",
            timestamp=self._timestamp(),
            data={"message": message[:500]}
        )
        self.log_event(event)
        self.console.info(f"User: {message[:100]}{'...' if len(message) > 100 else ''}")
    
    def context_built(self, token_count: int, message_count: int):
        event = LogEvent(
            event_type="CONTEXT_BUILT",
            timestamp=self._timestamp(),
            data={"tokens": token_count, "messages": message_count}
        )
        self.log_event(event)
        self.console.debug(f"📋 Context: {token_count} tokens, {message_count} messages")
    
    def llm_request(self, model: str, is_local: bool, prompt_preview: str = ""):
        self._llm_was_local = is_local
        _llm_ctx.set((time.monotonic(), model))
        event = LogEvent(
            event_type="LLM_REQUEST",
            timestamp=self._timestamp(),
            data={
                "model": model,
                "is_local": is_local,
                "prompt_preview": prompt_preview[:200]
            }
        )
        self.log_event(event)
        if _log_in_tool.get():
            pre = self._prefix()
            self.console.info(f"{pre}LLM call (tool context)  [{model}]")

    def llm_response(
        self,
        token_count: int,
        has_tool_call: bool,
        tool_name: str = None,
        *,
        duration_seconds: float | None = None,
        page_chars: int | None = None,
    ):
        pair = _llm_ctx.get()
        if pair is not None:
            _llm_ctx.set(None)
        if duration_seconds is not None:
            elapsed = duration_seconds
            model = (pair[1] if pair else "?")
        elif pair is not None:
            start, model = pair
            elapsed = time.monotonic() - start
        else:
            elapsed = 0.0
            model = "?"
        event = LogEvent(
            event_type="LLM_RESPONSE",
            timestamp=self._timestamp(),
            data={
                "tokens": token_count,
                "has_tool_call": has_tool_call,
                "tool_name": tool_name,
                "duration_seconds": round(elapsed, 3),
            }
        )
        self.log_event(event)
        pre = self._prefix()
        dur = _format_duration(elapsed)
        if _log_in_tool.get():
            chars_suffix = f"  {page_chars} chars" if page_chars is not None else ""
            self.console.info(f"{pre}Page summarized  ({dur}){chars_suffix}")
        elif has_tool_call:
            self.console.info(f"{pre}Agent chose tool: {tool_name}  ({dur})")
        else:
            self.console.info(f"{pre}Agent  ({dur})  [{model}]")

    def llm_stream_done(self):
        pair = _llm_ctx.get()
        if pair is not None:
            _llm_ctx.set(None)
        if pair is not None:
            start, model = pair
            elapsed = time.monotonic() - start
        else:
            elapsed = 0.0
            model = "?"
        dur = _format_duration(elapsed)
        self.console.info(f"{self._prefix()}Agent  ({dur})  [{model}]")
    
    def tool_execute(self, tool_name: str, args: dict):
        _log_tool_start.set(time.monotonic())
        _log_in_tool.set(True)
        event = LogEvent(
            event_type="TOOL_EXECUTE",
            timestamp=self._timestamp(),
            data={"tool": tool_name, "args": args}
        )
        self.log_event(event)
        short_args = self._format_tool_args(args)
        self.console.info(f"{self._prefix()}Run  {tool_name}({short_args})")

    def tool_page_fetched(self, duration_seconds: float):
        if not _log_in_tool.get():
            return
        dur = _format_duration(duration_seconds)
        self.console.info(f"{self._prefix()}Fetched  ({dur})")

    def tool_result(
        self,
        tool_name: str,
        result_length: int,
        success: bool,
        *,
        error_reason: str | None = None,
    ) -> None:
        # Use tool-level prefix for the Done line before clearing _log_in_tool
        done_prefix = self._prefix()
        _log_in_tool.set(False)
        start = _log_tool_start.get()
        _log_tool_start.set(None)
        elapsed = (time.monotonic() - start) if start is not None else 0.0
        data: dict[str, Any] = {
            "tool": tool_name,
            "result_length": result_length,
            "success": success,
            "duration_seconds": round(elapsed, 3),
        }
        if not success and error_reason:
            data["error_reason"] = (error_reason[:500] if len(error_reason) > 500 else error_reason)
        event = LogEvent(event_type="TOOL_RESULT", timestamp=self._timestamp(), data=data)
        self.log_event(event)
        dur = _format_duration(elapsed)
        if success:
            status = "ok"
        else:
            status = f"failed: {_short_reason(error_reason)}" if error_reason else "failed"
        self.console.info(f"{done_prefix}Done  {tool_name}  {result_length} chars  ({dur})  [{status}]")
        self.console.info("")
    
    def final_response(self, response: str):
        event = LogEvent(
            event_type="FINAL_RESPONSE",
            timestamp=self._timestamp(),
            data={"response": response[:500], "length": len(response)}
        )
        self.log_event(event)
        first_line = (response.split("\n")[0] or "").strip()
        if len(first_line) > 80:
            first_line = first_line[:77].rstrip() + "..."
        elif len(response.strip()) > len(first_line):
            first_line = first_line + "..."
        self.console.info(f"{self._prefix()}Reply: {first_line or '(empty)'}")
        _log_in_turn.set(False)
        self.console.info(_TURN_SEP)

    def thought(self, content: str):
        if not content:
            return
        
        event = LogEvent(
            event_type="THOUGHT",
            timestamp=self._timestamp(),
            data={"thought": content[:1000]}
        )
        self.log_event(event)
        try:
            terminal_width = shutil.get_terminal_size().columns
        except OSError:
            terminal_width = 80
        
        wrap_width = max(terminal_width - 10, 40)
        border_color = "\033[38;5;239m"
        text_color = "\033[38;5;244m\033[3m"
        header_color = "\033[35m\033[1m"
        reset = "\033[0m"
        
        header = f"  {header_color}🧠 LILITH'S THOUGHT LOG{reset}"
        wrapped_lines = []
        for line in content.split("\n"):
            if not line.strip():
                wrapped_lines.append("")
                continue
            wrapped_lines.extend(textwrap.wrap(line, width=wrap_width))
        output = [f"\n{header}"]
        for line in wrapped_lines:
            output.append(f"  {border_color}│{reset}  {text_color}{line}{reset}")
        output.append(f"  {border_color}╰{'─' * (wrap_width + 2)}{reset}\n")
        
        self.console.info("\n".join(output))
    
    def external_call(self, provider: str, model: str, full_payload: dict):
        timestamp = self._timestamp()
        event = LogEvent(
            event_type="EXTERNAL_CALL",
            timestamp=timestamp,
            data={
                "provider": provider,
                "model": model,
                "payload_size": len(json.dumps(full_payload))
            }
        )
        self.log_event(event)
        safe_timestamp = timestamp.replace(":", "-")
        payload_file = self.external_dir / f"{safe_timestamp}_{provider}.json"
        with open(payload_file, "w") as f:
            json.dump({
                "timestamp": timestamp,
                "provider": provider,
                "model": model,
                "payload": full_payload
            }, f, indent=2, default=str)
        
        self.console.warning(f"☁️ EXTERNAL: {provider}/{model} → logged to {payload_file.name}")
    
    def error(self, message: str, exception: Exception = None, **kwargs):
        event = LogEvent(
            event_type="ERROR",
            timestamp=self._timestamp(),
            data={
                "message": message,
                "exception": str(exception) if exception else None
            }
        )
        self.log_event(event)
        self.console.error(f"❌ Error: {message}", **kwargs)
    
    def info(self, message: str):
        self.console.info(message)

    def debug(self, message: str):
        event = LogEvent(
            event_type="DEBUG",
            timestamp=self._timestamp(),
            data={"message": message}
        )
        self.log_event(event)


logger = LilithLogger()
