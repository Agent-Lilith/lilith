"""Session recorder: per-turn log buffer and trace export into logs/sessions/<session_id>/."""

import json
import logging
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from src.core.config import config
from src.core.logger import LogEvent

logger = logging.getLogger(__name__)


# Time-based session id: sortable (e.g. 20260213_192400)
def _time_based_session_id() -> str:
    return datetime.now(UTC).strftime("%Y%m%d_%H%M%S")


def _build_trace_from_events(
    session_dir: Path, turn_n: int, session_id: str | None
) -> dict[str, Any]:
    """Read turn_N.jsonl and aggregate events into a trace tree. No API key needed."""
    path = session_dir / f"turn_{turn_n:03d}.jsonl"
    if not path.exists():
        return {
            "session_id": session_id,
            "turn": turn_n,
            "error": "turn file not found",
        }

    events: list[dict[str, Any]] = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                events.append(json.loads(line))
    except (json.JSONDecodeError, OSError) as e:
        return {"session_id": session_id, "turn": turn_n, "error": str(e)}

    if not events:
        return {
            "session_id": session_id,
            "turn": turn_n,
            "children": [],
            "error": "no events",
        }

    user_message = ""
    response = ""
    start_time = events[0].get("timestamp") if events else None
    end_time = events[-1].get("timestamp") if events else None
    children: list[dict[str, Any]] = []
    thoughts: list[str] = []
    pending_context: dict[str, Any] = {}
    pending_llm: dict[str, Any] | None = None

    for ev in events:
        etype = ev.get("event_type", "")
        data = ev.get("data") or {}
        ts = ev.get("timestamp")

        if etype == "USER_INPUT":
            user_message = data.get("message", "")
        elif etype == "CONTEXT_BUILT":
            pending_context = {"input_tokens": data.get("tokens"), "ts": ts}
        elif etype == "LLM_REQUEST":
            role = data.get("prompt_role")
            base: dict[str, Any] = {
                "run_type": "llm",
                "name": "LLM call",
                "model": data.get("model", ""),
                "is_local": data.get("is_local"),
                "start_time": ts,
                **pending_context,
            }
            if role == "main_agent":
                base["prompt_role"] = "main_agent"
                base["system_ref"] = data.get("system_ref", "soul")
                base["conversation_tail"] = data.get("conversation_tail", "")
                base["prompt_length"] = data.get("prompt_length")
            elif role in ("intent", "plan", "refine", "entity_extract"):
                base["prompt_role"] = role
                base["prompt"] = data.get("prompt", "")
            else:
                base["prompt"] = data.get("prompt", data.get("prompt_preview", ""))
            pending_llm = base
            pending_context = {}
        elif etype == "LLM_RESPONSE" and pending_llm is not None:
            pending_llm["output_tokens"] = data.get("tokens")
            pending_llm["duration_seconds"] = data.get("duration_seconds")
            pending_llm["has_tool_call"] = data.get("has_tool_call")
            pending_llm["tool_name"] = data.get("tool_name")
            pending_llm["end_time"] = ts
            children.append(pending_llm)
            pending_llm = None
        elif etype == "TOOL_EXECUTE":
            pending_llm = None
            children.append(
                {
                    "run_type": "tool",
                    "name": data.get("tool", "tool"),
                    "inputs": data.get("args", {}),
                    "start_time": ts,
                    "_pending": True,
                }
            )
        elif etype == "TOOL_RESULT" and children:
            for c in reversed(children):
                if c.get("run_type") == "tool" and c.get("_pending"):
                    outputs: dict[str, Any] = {
                        "result_length": data.get("result_length"),
                        "success": data.get("success"),
                        "error_reason": data.get("error_reason"),
                    }
                    result_content = data.get("result_content")
                    if result_content is not None:
                        outputs["result_content"] = (
                            result_content[:200] + "..."
                            if len(result_content) > 200
                            else result_content
                        )
                    c["outputs"] = outputs
                    c["duration_seconds"] = data.get("duration_seconds")
                    c["end_time"] = ts
                    c.pop("_pending", None)
                    break
        elif etype == "THOUGHT":
            thoughts.append(data.get("thought", ""))
        elif etype == "FINAL_RESPONSE":
            response = data.get("response", "")

    # Flush any pending LLM (streaming edge case)
    if pending_llm is not None:
        children.append(pending_llm)

    return {
        "session_id": session_id,
        "turn": turn_n,
        "user_message": user_message,
        "response": response,
        "start_time": start_time,
        "end_time": end_time,
        "children": children,
        "thoughts": thoughts,
    }


class SessionRecorder:
    """Writes log events to session turn files as they arrive; trace file at turn end."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._session_id: str | None = None
        self._session_dir: Path | None = None
        self._turn_index = 0
        self._in_turn = False
        self._last_turn_n: int = 0
        self._last_session_dir: Path | None = None

    def _ensure_session(self) -> None:
        with self._lock:
            if self._session_dir is not None:
                return
            self._session_id = config.session_id or _time_based_session_id()
            session_path = config.sessions_dir / self._session_id
            if session_path.exists():
                _clear_session_path(session_path)
            session_path.mkdir(parents=True, exist_ok=True)
            self._session_dir = session_path
            meta = {
                "session_id": self._session_id,
                "started_at": datetime.now(UTC).isoformat(),
            }
            meta_path = self._session_dir / "session_meta.json"
            try:
                meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
            except Exception as e:
                logger.warning(
                    "Session recorder: could not write session_meta.json: %s", e
                )

    def reset_session(self) -> None:
        """Clear session state so next event will run _ensure_session() again. Call at startup when using a fixed session id."""
        with self._lock:
            self._session_dir = None
            self._session_id = None
            self._turn_index = 0
            self._in_turn = False
            self._last_turn_n = 0
            self._last_session_dir = None

    def clear_session_folder_on_open(self) -> None:
        """Delete contents of the session folder when LILITH_SESSION_ID is set. Call once when CLI opens."""
        if not config.session_id:
            return
        session_path = config.sessions_dir / config.session_id
        _clear_session_path(session_path)

    def _append_to_turn_file(
        self, session_dir: Path, turn_n: int, event: LogEvent
    ) -> None:
        """Append one event line to turn_N.jsonl (outside lock)."""
        path = session_dir / f"turn_{turn_n:03d}.jsonl"
        try:
            with open(path, "a", encoding="utf-8") as f:
                f.write(event.to_json() + "\n")
        except Exception as e:
            logger.warning("Session recorder: could not append to %s: %s", path, e)

    def on_log_event(self, event: LogEvent) -> None:
        """Write event immediately to the current turn's .jsonl file."""
        if event.event_type == "USER_INPUT":
            self._ensure_session()
            with self._lock:
                if self._session_dir is None:
                    return
                self._in_turn = True
                self._turn_index += 1
                turn_n = self._turn_index
                session_dir = self._session_dir
            self._append_to_turn_file(session_dir, turn_n, event)
            return
        with self._lock:
            if not self._in_turn or self._session_dir is None:
                return
            turn_n = self._turn_index
            session_dir = self._session_dir
        self._append_to_turn_file(session_dir, turn_n, event)

    def on_turn_end(self) -> None:
        """Mark turn closed and write trace from event stream to turn_N_trace.json."""
        self._ensure_session()
        with self._lock:
            if not self._in_turn:
                return
            if self._session_dir is None:
                return
            turn_n = self._turn_index
            session_dir = self._session_dir
            self._last_turn_n = turn_n
            self._last_session_dir = session_dir
            self._in_turn = False

        self._write_trace_file(session_dir, turn_n, None)

    def write_trace_file(self, run_tree: Any | None = None) -> None:
        """Write trace file for the last turn. Builds trace from event stream (no API key needed)."""
        with self._lock:
            turn_n = self._last_turn_n
            session_dir = self._last_session_dir
        if session_dir is None or turn_n <= 0:
            return
        self._write_trace_file(session_dir, turn_n, run_tree)

    def _write_trace_file(
        self, session_dir: Path, turn_n: int, run_tree: Any | None
    ) -> None:
        """Build trace from event stream and write turn_N_trace.json. No API key needed."""
        trace_path = session_dir / f"turn_{turn_n:03d}_trace.json"
        try:
            trace_data = _build_trace_from_events(session_dir, turn_n, self._session_id)
        except Exception as e:
            logger.warning("Session recorder: trace build failed: %s", e)
            trace_data = {
                "session_id": self._session_id or "unknown",
                "turn": turn_n,
                "error": str(e),
            }
        try:
            trace_path.write_text(
                json.dumps(trace_data, indent=2, default=str), encoding="utf-8"
            )
        except Exception as e:
            logger.warning("Session recorder: could not write %s: %s", trace_path, e)

    @property
    def session_dir(self) -> Path | None:
        return self._session_dir

    @property
    def session_id(self) -> str | None:
        return self._session_id


def _clear_session_path(session_path: Path) -> None:
    """Remove turn logs and traces in an existing session dir (so it can be reused)."""
    if not session_path.is_dir():
        return
    try:
        for p in session_path.iterdir():
            if p.is_file() and (
                (
                    p.name.startswith("turn_")
                    and (p.suffix == ".jsonl" or p.name.endswith("_trace.json"))
                )
                or p.name == "session_meta.json"
            ):
                p.unlink()
    except Exception as e:
        logger.warning(
            "Session recorder: could not clear session dir %s: %s", session_path, e
        )


# Singleton used by logger
_session_recorder: SessionRecorder | None = None


def get_session_recorder() -> SessionRecorder:
    global _session_recorder
    if _session_recorder is None:
        _session_recorder = SessionRecorder()
    return _session_recorder
