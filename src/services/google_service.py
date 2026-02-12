"""
Unified Google API Service for Calendar and Tasks.
Handles credential loading, refreshing, and building of API service objects.
"""

import json
from datetime import UTC, datetime, timedelta, tzinfo
from pathlib import Path
from zoneinfo import ZoneInfo

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import Resource, build

from src.core.config import config
from src.core.logger import logger

SCOPES = [
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/tasks",
]


def _save_tokens(
    path: Path,
    creds: Credentials,
    default_calendar_id: str,
    default_task_list_id: str = "",
) -> None:
    """Saves credentials and default IDs to the token file."""
    data = {
        "access_token": creds.token,
        "refresh_token": creds.refresh_token,
        "expiry": creds.expiry.isoformat() if creds.expiry else None,
        "default_calendar_id": default_calendar_id or "primary",
        "default_task_list_id": default_task_list_id or "",
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


def _load_credentials(path: Path) -> tuple[Credentials | None, str, str]:
    """Loads credentials and default IDs from the token file."""
    if not path.exists():
        return None, config.google_calendar_default_id, ""
    with open(path) as f:
        data = json.load(f)

    creds = Credentials(
        token=data.get("access_token"),
        refresh_token=data.get("refresh_token"),
        token_uri="https://oauth2.googleapis.com/token",
        client_id=config.google_client_id,
        client_secret=config.google_client_secret,
        scopes=SCOPES,
    )
    if data.get("expiry"):
        try:
            creds.expiry = datetime.fromisoformat(data["expiry"].replace("Z", "+00:00"))
        except (ValueError, TypeError):
            pass

    default_calendar_id = data.get("default_calendar_id", "primary")
    default_task_list_id = data.get("default_task_list_id", "")

    return creds, default_calendar_id, default_task_list_id


class GoogleService:
    """A unified service to interact with Google Calendar and Tasks APIs."""

    def __init__(self, path: Path | None = None):
        self._token_path = path or config.google_calendar_tokens_path
        self._creds: Credentials | None = None
        self.default_calendar_id: str = config.google_calendar_default_id
        self.default_task_list_id: str = ""

        self._calendar_api: Resource | None = None
        self._tasks_api: Resource | None = None

        self._reload_credentials()

    def _reload_credentials(self) -> None:
        """Loads credentials from disk and refreshes them if necessary."""
        self._creds, self.default_calendar_id, self.default_task_list_id = (
            _load_credentials(self._token_path)
        )

        if self._creds and self._creds.valid:
            self._build_api_resources()
        elif self._creds and self._creds.expired and self._creds.refresh_token:
            try:
                self._creds.refresh(Request())
                _save_tokens(
                    self._token_path,
                    self._creds,
                    self.default_calendar_id,
                    self.default_task_list_id,
                )
                self._build_api_resources()
                logger.info("Google API token refreshed.")
            except Exception as e:
                logger.error(f"Failed to refresh Google API token: {e}")
                logger.error("Try re-authenticating: python -m src.main google-auth")
                self._creds = None
        else:
            self._creds = None

    def _build_api_resources(self):
        """Builds the calendar and tasks API resources if credentials are valid."""
        if not self._creds:
            return
        try:
            self._calendar_api = build(
                "calendar", "v3", credentials=self._creds, cache_discovery=False
            )
            self._tasks_api = build(
                "tasks", "v1", credentials=self._creds, cache_discovery=False
            )
        except Exception as e:
            logger.error(f"Failed to build Google API resources: {e}")
            self._calendar_api = None
            self._tasks_api = None

    @property
    def is_connected(self) -> bool:
        """Returns True if the service is connected to Google APIs."""
        return self._calendar_api is not None and self._tasks_api is not None

    @property
    def calendar(self) -> Resource:
        if not self._calendar_api:
            raise RuntimeError(
                "Google Calendar API not available. Please authenticate."
            )
        return self._calendar_api

    @property
    def tasks(self) -> Resource:
        if not self._tasks_api:
            raise RuntimeError("Google Tasks API not available. Please authenticate.")
        return self._tasks_api

    def get_calendar_id(self, calendar_id: str | None) -> str:
        """Returns the calendar ID to use, falling back to the default."""
        return (calendar_id or self.default_calendar_id) or "primary"

    def get_task_list_id(self, task_list_id: str | None) -> str:
        """
        Returns the task list ID to use, falling back to the default.
        If no default is set, it fetches the first available task list.
        """
        lid = (task_list_id or self.default_task_list_id or "").strip()
        if lid:
            return lid

        # If no ID is provided and no default is set, find the first list.
        task_lists = (
            self.tasks.tasklists().list(maxResults=1).execute().get("items", [])
        )
        if task_lists:
            return task_lists[0]["id"]

        raise RuntimeError(
            "No task list available. Create one in Google Tasks or set a default via google-auth."
        )


def range_preset_to_timebounds(
    range_preset: str, tz_name: str | None = None
) -> tuple[datetime, datetime]:
    """
    Return (time_min, time_max) in UTC for a given range_preset in the user's timezone.
    Supports: today, yesterday, tomorrow, this_week, end_of_week, next_7_days, next_14_days, this_month, next_month.
    """
    tz_name = tz_name or config.user_timezone or "UTC"
    try:
        tz: tzinfo = ZoneInfo(tz_name)
    except Exception:
        tz = UTC
    now = datetime.now(tz)
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)

    preset = (range_preset or "next_7_days").strip().lower()
    if preset == "today":
        start = today
        end = today + timedelta(days=1)
    elif preset == "yesterday":
        start = today - timedelta(days=1)
        end = today
    elif preset == "tomorrow":
        start = today + timedelta(days=1)
        end = today + timedelta(days=2)
    elif preset == "this_week":
        # Monday = 0
        start = today - timedelta(days=today.weekday())
        end = start + timedelta(days=7)
    elif preset == "end_of_week":
        # Through end of Sunday
        start = today - timedelta(days=today.weekday())
        end = start + timedelta(days=7)
    elif preset == "next_7_days":
        start = today
        end = today + timedelta(days=7)
    elif preset == "next_14_days":
        start = today
        end = today + timedelta(days=14)
    elif preset == "this_month":
        start = today.replace(day=1)
        next_month = (start.replace(day=28) + timedelta(days=4)).replace(day=1)
        end = next_month
    elif preset == "next_month":
        first_this = today.replace(day=1)
        first_next = (first_this.replace(day=28) + timedelta(days=4)).replace(day=1)
        end_next = (first_next.replace(day=28) + timedelta(days=4)).replace(day=1)
        start = first_next
        end = end_next
    else:
        start = today
        end = today + timedelta(days=7)

    return start.astimezone(UTC), end.astimezone(UTC)
