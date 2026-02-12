"""Structured logging: console, file, and external-call logs."""

import contextvars
import json
import logging
import os
import shutil
import sys
import textwrap
import threading
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any

from src.core.config import config


def _format_duration(seconds: float) -> str:
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
_llm_ctx: contextvars.ContextVar[tuple[float, str] | None] = contextvars.ContextVar(
    "llm_request", default=None
)
_log_in_turn: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "log_in_turn", default=False
)
_log_in_tool: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "log_in_tool", default=False
)
_log_tool_start: contextvars.ContextVar[float | None] = contextvars.ContextVar(
    "log_tool_start", default=None
)
_log_tool_name: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "log_tool_name", default=None
)
_log_tool_step: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "log_tool_step", default=None
)
_log_page_index: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "log_page_index", default=None
)
_log_page_hint: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "log_page_hint", default=None
)


def _use_color() -> bool:
    if os.environ.get("NO_COLOR"):
        return False
    try:
        return sys.stdout.isatty()
    except Exception:
        return False


def _c(role: str) -> str:
    if not _use_color():
        return ""
    # 38;5;N = foreground 256-color
    codes = {
        "dim": "\033[38;5;239m",
        "tool": "\033[38;5;81m",  # cyan for tool/step names
        "llm_call": "\033[38;5;81m",  # same cyan so "LLM call (tool › step)" is readable
        "run": "\033[38;5;78m",  # green for Run
        "done_ok": "\033[38;5;78m",  # green for Done / [ok]
        "done_fail": "\033[38;5;203m",  # red for [failed]
        "duration": "\033[38;5;221m",  # yellow for durations
        "model": "\033[38;5;245m",  # dim gray for model name
        "reply": "\033[38;5;246m",  # muted for reply preview
    }
    return codes.get(role, "")


def _reset() -> str:
    if not _use_color():
        return ""
    return "\033[0m"


def _tool_label() -> str:
    name = _log_tool_name.get() or "tool"
    step = _log_tool_step.get()
    page_index = _log_page_index.get()
    if step:
        return f"{name} › {step}"
    if page_index:
        return f"{name} {page_index}"
    return name


def _page_hint_suffix() -> str:
    """Short suffix for console when in read_pages (e.g. '  pbs.org')."""
    hint = _log_page_hint.get()
    if not hint:
        return ""
    return f"  {hint}"


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
        self._console_formatter = logging.Formatter(
            "%(asctime)s │ %(message)s", datefmt="%H:%M:%S"
        )
        if not self.console.handlers:
            handler = logging.StreamHandler()
            handler.setLevel(logging.INFO)
            handler.setFormatter(self._console_formatter)
            self.console.addHandler(handler)
        self._setup_third_party_console_logging()

    def _setup_third_party_console_logging(self):
        handler = logging.StreamHandler()
        handler.setLevel(logging.INFO)
        handler.setFormatter(self._console_formatter)
        for name in ("mcp", "core.embeddings"):
            log = logging.getLogger(name)
            log.setLevel(logging.INFO)
            log.propagate = False
            log.addHandler(handler)

    def log_event(self, event: LogEvent) -> None:
        with self._file_lock:
            self._log_file_handle.write(event.to_json() + "\n")
            self._log_file_handle.flush()

    def _timestamp(self) -> str:
        return datetime.now().isoformat()

    def _prefix(self) -> str:
        if _log_in_tool.get():
            return "  │   └ "
        if _log_in_turn.get():
            return "  │ "
        return ""

    def set_tool_step(self, step: str | None) -> None:
        _log_tool_step.set(step)

    def set_page_index(self, index: int | None, total: int | None = None) -> None:
        if index is None or total is None or total <= 0:
            _log_page_index.set(None)
        else:
            _log_page_index.set(f"{index}/{total}")

    def set_page_hint(self, hint: str | None) -> None:
        """Set short page hint (e.g. domain) for read_pages. Cleared in tool_result."""
        if not hint or not hint.strip():
            _log_page_hint.set(None)
        else:
            _log_page_hint.set(hint.strip()[:40])

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
            data={"message": message[:500]},
        )
        self.log_event(event)
        self.console.info(f"User: {message[:100]}{'...' if len(message) > 100 else ''}")

    def context_built(self, token_count: int, message_count: int):
        event = LogEvent(
            event_type="CONTEXT_BUILT",
            timestamp=self._timestamp(),
            data={"tokens": token_count, "messages": message_count},
        )
        self.log_event(event)
        self.console.debug(
            f"📋 Context: {token_count} tokens, {message_count} messages"
        )

    def llm_request(self, model: str, is_local: bool, prompt_preview: str = ""):
        self._llm_was_local = is_local
        _llm_ctx.set((time.monotonic(), model))
        event = LogEvent(
            event_type="LLM_REQUEST",
            timestamp=self._timestamp(),
            data={
                "model": model,
                "is_local": is_local,
                "prompt_preview": prompt_preview[:200],
            },
        )
        self.log_event(event)
        if _log_in_tool.get():
            pre = self._prefix()
            label = _tool_label()
            hint = _page_hint_suffix()
            self.console.info(
                f"{pre}{_c('llm_call')}LLM call ({label}){_reset()}{hint}  [{_reset()}{_c('model')}{model}{_reset()}]"
            )

    def llm_response(
        self,
        token_count: int,
        has_tool_call: bool,
        tool_name: str | None = None,
        *,
        duration_seconds: float | None = None,
        page_chars: int | None = None,
    ):
        pair = _llm_ctx.get()
        if pair is not None:
            _llm_ctx.set(None)
        if duration_seconds is not None:
            elapsed = duration_seconds
            model = pair[1] if pair else "?"
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
            },
        )
        self.log_event(event)
        pre = self._prefix()
        dur = _format_duration(elapsed)
        dur_colored = f"{_c('duration')}{dur}{_reset()}"
        if _log_in_tool.get():
            label = _tool_label()
            hint = _page_hint_suffix()
            chars_suffix = f"  {page_chars} chars" if page_chars is not None else ""
            self.console.info(
                f"{pre}{_c('tool')}{label}{_reset()}{hint}  in {dur_colored}  {chars_suffix}"
            )
        elif has_tool_call:
            self.console.info(f"{pre}Agent chose tool: {tool_name}  {dur_colored}")
        else:
            self.console.info(
                f"{pre}Agent  {dur_colored}  {_c('model')}[{model}]{_reset()}"
            )

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
        dur_colored = f"{_c('duration')}{dur}{_reset()}"
        self.console.info(
            f"{self._prefix()}Agent  took {dur_colored}  {_c('model')}[{model}]{_reset()}"
        )

    def tool_execute(self, tool_name: str, args: dict):
        _log_tool_start.set(time.monotonic())
        _log_in_tool.set(True)
        _log_tool_name.set(tool_name)
        _log_tool_step.set(None)
        _log_page_index.set(None)
        _log_page_hint.set(None)
        event = LogEvent(
            event_type="TOOL_EXECUTE",
            timestamp=self._timestamp(),
            data={"tool": tool_name, "args": args},
        )
        self.log_event(event)
        short_args = self._format_tool_args(args)
        self.console.info(
            f"{self._prefix()}{_c('run')}▶ Run{_reset()}  {_c('tool')}{tool_name}{_reset()}({short_args})"
        )

    def tool_page_fetched(self, duration_seconds: float):
        if not _log_in_tool.get():
            return
        dur = _format_duration(duration_seconds)
        hint = _page_hint_suffix()
        self.console.info(f"{self._prefix()}Fetched  ({dur}){hint}")

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
        _log_tool_name.set(None)
        _log_tool_step.set(None)
        _log_page_index.set(None)
        _log_page_hint.set(None)
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
            data["error_reason"] = (
                error_reason[:500] if len(error_reason) > 500 else error_reason
            )
        event = LogEvent(
            event_type="TOOL_RESULT", timestamp=self._timestamp(), data=data
        )
        self.log_event(event)
        dur = _format_duration(elapsed)
        dur_colored = f"{_c('duration')}{dur}{_reset()}"
        if success:
            status = "ok"
            status_str = f"{_c('done_ok')}[ok]{_reset()}"
        else:
            status = (
                f"failed: {_short_reason(error_reason)}" if error_reason else "failed"
            )
            status_str = f"{_c('done_fail')}[failed]{_reset()}"
        self.console.info(
            f"{done_prefix}{_c('done_ok')}✓ Done{_reset()}  {_c('tool')}{tool_name}{_reset()}  "
            f"total {dur_colored}  {result_length} chars  {status_str}"
        )
        self.console.info("")

    def final_response(self, response: str):
        event = LogEvent(
            event_type="FINAL_RESPONSE",
            timestamp=self._timestamp(),
            data={"response": response[:500], "length": len(response)},
        )
        self.log_event(event)
        text = response.strip().replace("\n", " ")
        preview_len = 90
        if len(text) <= preview_len:
            preview = text or "(empty)"
        else:
            start = 0
            while start < len(text) and not text[start].isalnum():
                start += 1
            if start < len(text):
                first_space = text.find(" ", start)
                if first_space != -1 and first_space - start <= 2:
                    start = first_space + 1
                    while start < len(text) and not text[start].isalnum():
                        start += 1
            preview = text[start : start + preview_len].rstrip()
            if len(preview) < 20:
                preview = text[:preview_len].rstrip()
            if len(text) > start + len(preview):
                preview = preview + "..."
        length_note = f" ({len(response)} chars)" if len(response) > 80 else ""
        self.console.info(
            f"{self._prefix()}{_c('reply')}Reply{length_note}: {preview}{_reset()}"
        )
        _log_in_turn.set(False)
        self.console.info(_TURN_SEP)

    def thought(self, content: str):
        if not content:
            return

        event = LogEvent(
            event_type="THOUGHT",
            timestamp=self._timestamp(),
            data={"thought": content[:1000]},
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
                "payload_size": len(json.dumps(full_payload)),
            },
        )
        self.log_event(event)
        safe_timestamp = timestamp.replace(":", "-")
        payload_file = self.external_dir / f"{safe_timestamp}_{provider}.json"
        with open(payload_file, "w") as f:
            json.dump(
                {
                    "timestamp": timestamp,
                    "provider": provider,
                    "model": model,
                    "payload": full_payload,
                },
                f,
                indent=2,
                default=str,
            )

        self.console.warning(
            f"☁️ EXTERNAL: {provider}/{model} → logged to {payload_file.name}"
        )

    def error(self, message: str, *args, exception: Exception | None = None, **kwargs):
        event = LogEvent(
            event_type="ERROR",
            timestamp=self._timestamp(),
            data={
                "message": message,
                "exception": str(exception) if exception else None,
            },
        )
        self.log_event(event)

        # Filter kwargs for standard logger
        allowed = {"exc_info", "stack_info", "stacklevel", "extra"}
        log_kwargs = {k: v for k, v in kwargs.items() if k in allowed}
        if exception and "exc_info" not in log_kwargs:
            log_kwargs["exc_info"] = exception

        self.console.error(f"❌ Error: {message}", *args, **log_kwargs)

    def info(self, message: str, *args, **kwargs):
        allowed = {"exc_info", "stack_info", "stacklevel", "extra"}
        log_kwargs = {k: v for k, v in kwargs.items() if k in allowed}
        self.console.info(message, *args, **log_kwargs)

    def warning(self, message: str, *args, **kwargs):
        event = LogEvent(
            event_type="WARNING",
            timestamp=self._timestamp(),
            data={"message": message[:500]},
        )
        self.log_event(event)

        allowed = {"exc_info", "stack_info", "stacklevel", "extra"}
        log_kwargs = {k: v for k, v in kwargs.items() if k in allowed}
        self.console.warning(f"⚠️ {message}", *args, **log_kwargs)

    def exception(self, message: str, *args, **kwargs):
        event = LogEvent(
            event_type="ERROR",
            timestamp=self._timestamp(),
            data={"message": message[:500]},
        )
        self.log_event(event)
        self.console.exception(f"❌ {message}", *args, **kwargs)

    def debug(self, message: str, *args, **kwargs):
        event = LogEvent(
            event_type="DEBUG", timestamp=self._timestamp(), data={"message": message}
        )
        self.log_event(event)

        allowed = {"exc_info", "stack_info", "stacklevel", "extra"}
        log_kwargs = {k: v for k, v in kwargs.items() if k in allowed}
        self.console.debug(message, *args, **log_kwargs)


logger = LilithLogger()
