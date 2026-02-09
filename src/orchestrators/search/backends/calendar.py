"""Calendar search backend: list_events via Google Calendar, same logic as CalendarReadTool."""

import asyncio
from typing import Any

from src.orchestrators.search.interface import SearchTool
from src.orchestrators.search.models import SearchResult


def _event_start_iso(event: dict) -> str | None:
    start = event.get("start", {}) or {}
    dt = start.get("dateTime") or start.get("date")
    return str(dt) if dt else None


class CalendarSearchBackend(SearchTool):
    def __init__(self, google_service: Any):
        self._service = google_service

    async def search(
        self,
        query: str,
        top_k: int = 10,
        filters: dict[str, Any] | None = None,
    ) -> list[SearchResult]:
        if not self._service.is_connected:
            return []
        f = filters or {}
        range_preset = (f.get("range_preset") or "next_7_days").strip() or "next_7_days"
        calendar_id = f.get("calendar_id") or ""

        def _sync_list_events() -> list[SearchResult]:
            from src.services.google_service import range_preset_to_timebounds

            time_min, time_max = range_preset_to_timebounds(range_preset)
            cal_id = self._service.get_calendar_id(calendar_id or None)
            events_result = self._service.calendar.events().list(
                calendarId=cal_id,
                timeMin=time_min.isoformat().replace("+00:00", "Z"),
                timeMax=time_max.isoformat().replace("+00:00", "Z"),
                singleEvents=True,
                orderBy="startTime",
            ).execute()
            events = events_result.get("items", [])[:top_k]
            out: list[SearchResult] = []
            for i, ev in enumerate(events):
                summary = ev.get("summary") or "(No title)"
                start = ev.get("start", {}) or {}
                end = ev.get("end", {}) or {}
                start_str = start.get("dateTime") or start.get("date") or "?"
                end_str = end.get("dateTime") or end.get("date") or "?"
                content = f"{summary} | {start_str} — {end_str}"
                ts = _event_start_iso(ev)
                score = 1.0 - (i * 0.04)
                if score < 0.3:
                    score = 0.3
                out.append(
                    SearchResult(
                        content=content,
                        source="calendar",
                        title=summary,
                        timestamp=ts,
                        metadata={
                            "event_id": ev.get("id"),
                            "calendar_id": cal_id,
                            "start": start_str,
                            "end": end_str,
                        },
                        relevance_score=score,
                    )
                )
            return out

        return await asyncio.to_thread(_sync_list_events)

    def get_source_name(self) -> str:
        return "calendar"

    def can_handle_query(self, query: str, intent: dict[str, Any]) -> float:
        query_lower = query.lower()
        strong = ["calendar", "event", "events", "meeting", "meetings", "schedule", "appointment"]
        temporal = ["today", "tomorrow", "this week", "next week", "upcoming", "when is"]
        if any(w in query_lower for w in strong):
            return 0.9
        if any(w in query_lower for w in temporal):
            return 0.75
        return 0.35
