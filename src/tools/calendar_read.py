"""Calendar read: list calendars, list events, get event by id."""

from src.core.prompts import get_tool_description, get_tool_examples
from src.core.logger import logger
from src.services.google_service import GoogleService
from src.tools.base import Tool, ToolResult

def _format_event_summary(event: dict) -> str:
    summary = event.get("summary") or "(No title)"
    start = event.get("start", {})
    end = event.get("end", {})
    start_str = start.get("dateTime") or start.get("date", "?")
    end_str = end.get("dateTime") or end.get("date", "?")
    eid = event.get("id", "")
    return f"{summary} | {start_str} — {end_str} | id={eid}"


def _format_event_full(event: dict) -> str:
    lines = [
        f"Summary: {event.get('summary') or '(No title)'}",
        f"ID: {event.get('id', '')}",
        f"Start: {event.get('start', {})}",
        f"End: {event.get('end', {})}",
    ]
    if event.get("description"):
        lines.append(f"Description: {event['description']}")
    if event.get("location"):
        lines.append(f"Location: {event['location']}")
    if event.get("attendees"):
        emails = [a.get("email", "") for a in event["attendees"]]
        lines.append(f"Attendees: {', '.join(emails)}")
    if event.get("recurrence"):
        lines.append(f"Recurrence: {event['recurrence']}")
    if event.get("reminders"):
        lines.append(f"Reminders: {event['reminders']}")
    if event.get("visibility"):
        lines.append(f"Visibility: {event['visibility']}")
    return "\n".join(lines)


class CalendarReadTool(Tool):
    def __init__(self, google_service: GoogleService):
        self._service = google_service

    @property
    def name(self) -> str:
        return "calendar_read"

    @property
    def description(self) -> str:
        return get_tool_description(self.name)

    @property
    def parameters(self) -> dict[str, str]:
        return {
            "action": "One of: list_calendars, list_events, get_event",
            "range_preset": "For list_events: today, yesterday, tomorrow, this_week, end_of_week, next_7_days, next_14_days, this_month, next_month",
            "calendar_id": "Optional. Calendar id (e.g. primary or email); omit for default.",
            "event_id": "For get_event only: the event id",
        }

    def get_examples(self) -> list[str]:
        return get_tool_examples(self.name)

    async def execute(
        self,
        action: str,
        range_preset: str = "next_7_days",
        calendar_id: str = "",
        event_id: str = "",
    ) -> ToolResult:
        logger.tool_execute(self.name, {
            "action": action, 
            "range_preset": range_preset, 
            "calendar_id": calendar_id, 
            "event_id": event_id
        })
        import asyncio
        return await asyncio.to_thread(self._sync_execute, action, range_preset, calendar_id, event_id)

    def _sync_execute(
        self,
        action: str,
        range_preset: str = "next_7_days",
        calendar_id: str = "",
        event_id: str = "",
    ) -> ToolResult:
        if not self._service.is_connected:
            msg = "Calendar not connected. Run: python -m src.main google-auth"
            logger.tool_result(self.name, 0, False, error_reason=msg)
            return ToolResult.fail(msg)

        try:
            if action == "list_calendars":
                result = self._service.calendar.calendarList().list().execute()
                items = result.get("items", [])
                if not items:
                    out = "No calendars found."
                else:
                    lines = []
                    for c in items:
                        marks = []
                        if c.get("primary"):
                            marks.append("primary")
                        if c.get("id") == self._service.default_calendar_id:
                            marks.append("default")
                        suffix = f" [{', '.join(marks)}]" if marks else ""
                        lines.append(f"- {c['summary']} — id: {c['id']}{suffix}")
                    out = "\n".join(lines)
                logger.tool_result(self.name, len(out), True)
                return ToolResult.ok(out)

            if action == "list_events":
                from src.services.google_service import range_preset_to_timebounds
                time_min, time_max = range_preset_to_timebounds(range_preset)
                cal_id = self._service.get_calendar_id(calendar_id)
                
                events_result = self._service.calendar.events().list(
                    calendarId=cal_id,
                    timeMin=time_min.isoformat().replace("+00:00", "Z"),
                    timeMax=time_max.isoformat().replace("+00:00", "Z"),
                    singleEvents=True,
                    orderBy="startTime",
                ).execute()
                events = events_result.get("items", [])
                
                if not events:
                    out = "No events in that range."
                else:
                    out = "\n".join(_format_event_summary(e) for e in events)
                logger.tool_result(self.name, len(out), True)
                return ToolResult.ok(out)

            if action == "get_event":
                if not event_id or not event_id.strip():
                    msg = "get_event requires event_id"
                    logger.tool_result(self.name, 0, False, error_reason=msg)
                    return ToolResult.fail(msg)
                cal_id = self._service.get_calendar_id(calendar_id)
                event = self._service.calendar.events().get(calendarId=cal_id, eventId=event_id.strip()).execute()
                if not event:
                    msg = "Event not found."
                    logger.tool_result(self.name, 0, False, error_reason=msg)
                    return ToolResult.fail(msg)
                
                out = _format_event_full(event)
                logger.tool_result(self.name, len(out), True)
                return ToolResult.ok(out)

            msg = f"Unknown action: {action}. Use list_calendars, list_events, or get_event."
            logger.tool_result(self.name, 0, False, error_reason=msg)
            return ToolResult.fail(msg)

        except Exception as e:
            logger.tool_result(self.name, 0, False, error_reason=str(e))
            logger.error(f"Calendar read failed: {e}", e)
            return ToolResult.fail(str(e))
