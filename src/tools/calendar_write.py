"""Calendar write: create (immediate), update/delete (confirmation)."""

import asyncio
import json
import re
import uuid
from datetime import datetime
from typing import Any

from src.core.config import config
from src.core.logger import logger
from src.core.prompts import get_tool_description, get_tool_examples
from src.services.google_service import GoogleService
from src.tools.base import Tool, ToolResult, format_confirm_required


def _parse_datetime(s: str, default_timezone: str | None = None) -> dict[str, str]:
    s = (s or "").strip()
    if not s:
        return {}
    tz = default_timezone or config.user_timezone or "UTC"
    if re.match(r"^\d{4}-\d{2}-\d{2}$", s):
        return {"date": s}
    if "T" in s:
        has_explicit_tz = s.endswith("Z") or re.search(r"[+-]\d{2}:?\d{2}$", s)
        return {"dateTime": s, "timeZone": "UTC" if has_explicit_tz else tz}
    return {"dateTime": s + "T00:00:00", "timeZone": tz}


def _format_event_when(event: dict[str, Any]) -> str:
    if not event:
        return ""
    start = event.get("start") or {}
    if isinstance(start, dict):
        if "dateTime" in start:
            raw = start["dateTime"]
            try:
                dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
                hour = int(dt.strftime("%I"))
                ampm = dt.strftime("%p").replace("AM", "am").replace("PM", "pm")
                return f"{dt.strftime('%A')} {hour}{ampm}"
            except (ValueError, TypeError):
                return raw[:16] if len(raw) >= 16 else raw
        if "date" in start:
            return start["date"]
    return ""


def _parse_reminders(reminders_str: str) -> dict[str, Any] | None:
    reminders_str = (reminders_str or "").strip()
    if not reminders_str:
        return None
    overrides = []
    for part in reminders_str.replace(" ", "").split(","):
        if part.isdigit():
            overrides.append({"method": "popup", "minutes": int(part)})
        elif part.lower() == "email":
            overrides.append({"method": "email", "minutes": 30})
    if not overrides:
        return None
    return {"useDefault": False, "overrides": overrides}


def _build_event_body(
    title: str = "",
    start: str = "",
    end: str = "",
    description: str = "",
    location: str = "",
    reminders: str = "",
    recurrence: str = "",
    visibility: str = "default",
    attendees: str = "",
    color_id: str = "",
) -> dict[str, Any]:
    body: dict[str, Any] = {}
    if title:
        body["summary"] = title
    start_d = _parse_datetime(start)
    if start_d:
        body["start"] = start_d
    end_d = _parse_datetime(end)
    if end_d:
        body["end"] = end_d
    if description:
        body["description"] = description
    if location:
        body["location"] = location
    rem = _parse_reminders(reminders)
    if rem:
        body["reminders"] = rem
    if recurrence and recurrence.strip():
        body["recurrence"] = [r.strip() for r in recurrence.split(";") if r.strip()]
    if visibility and visibility.lower() in (
        "default",
        "public",
        "private",
        "confidential",
    ):
        body["visibility"] = visibility.lower()
    if attendees:
        emails = [e.strip() for e in attendees.split(",") if e.strip()]
        body["attendees"] = [{"email": e} for e in emails]
    if color_id and color_id.strip():
        body["colorId"] = color_id.strip()
    return body


class CalendarWriteTool(Tool):
    def __init__(self, google_service: GoogleService) -> None:
        self._service = google_service
        self._pending: dict[str, dict[str, Any]] = {}

    @property
    def name(self) -> str:
        return "calendar_write"

    @property
    def description(self) -> str:
        return get_tool_description(self.name)

    @property
    def parameters(self) -> dict[str, str]:
        return {
            "action": "One of: create, update, delete",
            "event_id": "Required for update and delete",
            "calendar_id": "Optional. Omit for default calendar.",
            "title": "Event title (required for create)",
            "start": "Start datetime (ISO) or date (YYYY-MM-DD)",
            "end": "End datetime or date",
            "description": "Optional description",
            "location": "Optional location",
            "reminders": "Optional e.g. 30 (minutes) or 10,email",
            "recurrence": "Optional RRULE e.g. RRULE:FREQ=DAILY;COUNT=2",
            "visibility": "default, public, private, or confidential",
            "attendees": "Optional comma-separated emails",
            "color_id": "Optional color id",
        }

    def get_examples(self) -> list[str]:
        return get_tool_examples(self.name)

    async def execute(self, **kwargs: object) -> ToolResult:
        action = str(kwargs.get("action", ""))
        event_id = str(kwargs.get("event_id", ""))
        calendar_id = str(kwargs.get("calendar_id", ""))
        title = str(kwargs.get("title", ""))
        start = str(kwargs.get("start", ""))
        end = str(kwargs.get("end", ""))
        description = str(kwargs.get("description", ""))
        location = str(kwargs.get("location", ""))
        reminders = str(kwargs.get("reminders", ""))
        recurrence = str(kwargs.get("recurrence", ""))
        visibility = str(kwargs.get("visibility", "default"))
        attendees = str(kwargs.get("attendees", ""))
        color_id = str(kwargs.get("color_id", ""))
        confirm_pending_id = str(kwargs.get("confirm_pending_id", ""))
        if confirm_pending_id and confirm_pending_id.strip():
            return await asyncio.to_thread(
                self.execute_pending, confirm_pending_id.strip()
            )

        all_args = {
            "action": action,
            "event_id": event_id,
            "calendar_id": calendar_id,
            "title": title,
            "start": start,
            "end": end,
            "description": description,
            "location": location,
            "reminders": reminders,
            "recurrence": recurrence,
            "visibility": visibility,
            "attendees": attendees,
            "color_id": color_id,
        }
        log_args = {k: v for k, v in all_args.items() if v}
        logger.tool_execute(self.name, log_args)

        return await asyncio.to_thread(
            self._sync_execute,
            action,
            event_id,
            calendar_id,
            title,
            start,
            end,
            description,
            location,
            reminders,
            recurrence,
            visibility,
            attendees,
            color_id,
        )

    def _sync_execute(
        self,
        action: str,
        event_id: str = "",
        calendar_id: str = "",
        title: str = "",
        start: str = "",
        end: str = "",
        description: str = "",
        location: str = "",
        reminders: str = "",
        recurrence: str = "",
        visibility: str = "default",
        attendees: str = "",
        color_id: str = "",
    ) -> ToolResult:
        if not self._service.is_connected:
            return ToolResult.fail(
                "Calendar not connected. User should run: python -m src.main google-auth"
            )

        cal_id = self._service.get_calendar_id(calendar_id)

        try:
            if action == "create":
                if not title or not start or not end:
                    return ToolResult.fail("create requires title, start, and end.")
                body = _build_event_body(
                    title=title,
                    start=start,
                    end=end,
                    description=description,
                    location=location,
                    reminders=reminders,
                    recurrence=recurrence,
                    visibility=visibility,
                    attendees=attendees,
                    color_id=color_id,
                )
                event = (
                    self._service.calendar.events()
                    .insert(calendarId=cal_id, body=body)
                    .execute()
                )
                eid = event.get("id", "")
                url = event.get("htmlLink", "")
                payload = json.dumps({"id": eid, "url": url or ""})
                out = f"Event created: {event.get('summary', title)}. Use event_id for update/delete. {payload}"
                return ToolResult.ok(out)

            if action == "update":
                if not event_id:
                    return ToolResult.fail("update requires event_id.")
                body = _build_event_body(
                    title=title,
                    start=start,
                    end=end,
                    description=description,
                    location=location,
                    reminders=reminders,
                    recurrence=recurrence,
                    visibility=visibility,
                    attendees=attendees,
                    color_id=color_id,
                )
                if not body:
                    return ToolResult.fail(
                        "update requires at least one field to change."
                    )

                existing = (
                    self._service.calendar.events()
                    .get(calendarId=cal_id, eventId=event_id.strip())
                    .execute()
                )
                summary_title = (existing or {}).get("summary", "event")
                when = _format_event_when(existing or {})
                pending_id = str(uuid.uuid4())
                self._pending[pending_id] = {
                    "action": "update",
                    "calendar_id": cal_id,
                    "event_id": event_id.strip(),
                    "body": body,
                }
                summary_msg = (
                    f"Update the appointment on {when} called '{summary_title}' with the requested changes?"
                    if when
                    else f"Update '{summary_title}' with the requested changes?"
                )
                out = f"I need your confirmation to update this event. {format_confirm_required(self.name, pending_id, summary_msg)}"
                return ToolResult.ok(out)

            if action == "delete":
                if not event_id:
                    return ToolResult.fail("delete requires event_id.")

                existing = (
                    self._service.calendar.events()
                    .get(calendarId=cal_id, eventId=event_id.strip())
                    .execute()
                )
                summary_title = (existing or {}).get("summary", "this event")
                when = _format_event_when(existing or {})
                pending_id = str(uuid.uuid4())
                self._pending[pending_id] = {
                    "action": "delete",
                    "calendar_id": cal_id,
                    "event_id": event_id.strip(),
                    "body": None,
                }
                summary_msg = (
                    f"Delete the appointment on {when} called '{summary_title}'?"
                    if when
                    else f"Delete '{summary_title}'?"
                )
                out = f"I need your confirmation to delete this event. {format_confirm_required(self.name, pending_id, summary_msg)}"
                return ToolResult.ok(out)

            return ToolResult.fail(
                f"Unknown action: {action}. Use create, update, or delete."
            )

        except Exception as e:
            logger.error(f"Calendar write failed: {e}", e)
            return ToolResult.fail(str(e))

    def execute_pending(self, pending_id: str) -> ToolResult:
        if pending_id not in self._pending:
            return ToolResult.fail(
                "Confirmation expired or invalid. Please try the action again."
            )

        payload = self._pending.pop(pending_id)
        action = payload["action"]
        cal_id = payload["calendar_id"]
        event_id = payload["event_id"]

        if not self._service.is_connected:
            self._pending[pending_id] = payload
            return ToolResult.fail("Calendar not connected.")

        try:
            if action == "update":
                body = payload["body"]
                event = (
                    self._service.calendar.events()
                    .patch(calendarId=cal_id, eventId=event_id, body=body)
                    .execute()
                )
                out = f"Event updated: {event.get('summary', event_id)}"
            else:  # delete
                self._service.calendar.events().delete(
                    calendarId=cal_id, eventId=event_id
                ).execute()
                out = "Event deleted."

            logger.tool_result(self.name, len(out), True)
            return ToolResult.ok(out)
        except Exception as e:
            logger.error(f"Calendar execute_pending failed: {e}", e)
            self._pending[pending_id] = payload
            return ToolResult.fail(str(e))
