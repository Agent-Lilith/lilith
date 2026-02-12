"""Calendar search backend: Google Calendar API. Returns SearchResultV1."""

import asyncio
from typing import Any

from src.contracts.mcp_search_v1 import SearchResultV1, SourceClass
from src.orchestrators.search.interface import SearchBackend


class CalendarSearchBackend(SearchBackend):
    def __init__(self, google_service: Any):
        self._service = google_service

    async def search(
        self,
        query: str,
        methods: list[str] | None = None,
        filters: list[dict[str, Any]] | None = None,
        top_k: int = 10,
    ) -> list[SearchResultV1]:
        if not self._service.is_connected:
            return []

        f_dict = {fc["field"]: fc["value"] for fc in (filters or []) if "field" in fc and "value" in fc}
        range_preset = f_dict.get("range_preset", "next_7_days")
        calendar_id = f_dict.get("calendar_id", "")

        def _sync_list_events() -> list[SearchResultV1]:
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
            results: list[SearchResultV1] = []
            for i, ev in enumerate(events):
                summary = ev.get("summary") or "(No title)"
                start = ev.get("start", {}) or {}
                end = ev.get("end", {}) or {}
                start_str = start.get("dateTime") or start.get("date") or "?"
                end_str = end.get("dateTime") or end.get("date") or "?"
                content = f"{summary} | {start_str} -- {end_str}"
                ts = start.get("dateTime") or start.get("date")
                score = max(0.3, 1.0 - (i * 0.04))

                results.append(SearchResultV1(
                    id=ev.get("id", f"cal_{i}"),
                    source="calendar",
                    source_class=SourceClass.PERSONAL,
                    title=summary,
                    snippet=content,
                    timestamp=str(ts) if ts else None,
                    scores={"structured": score},
                    methods_used=["structured"],
                    metadata={
                        "event_id": ev.get("id"),
                        "calendar_id": cal_id,
                        "start": start_str,
                        "end": end_str,
                        "location": ev.get("location", ""),
                        "attendees": [a.get("email", "") for a in ev.get("attendees", [])],
                    },
                    provenance=f"calendar event on {start_str}",
                ))
            return results

        return await asyncio.to_thread(_sync_list_events)

    def get_source_name(self) -> str:
        return "calendar"

    def get_source_class(self) -> SourceClass:
        return SourceClass.PERSONAL

    def get_supported_methods(self) -> list[str]:
        return ["structured"]

    def get_supported_filters(self) -> list[dict[str, Any]]:
        return [
            {"name": "range_preset", "type": "string", "operators": ["eq"], "description": "Time range: next_7_days, next_30_days, today"},
            {"name": "calendar_id", "type": "string", "operators": ["eq"], "description": "Google Calendar ID"},
        ]
