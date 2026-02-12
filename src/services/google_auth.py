"""Google Calendar + Tasks OAuth2. Run: python -m src.main google-auth"""

from pathlib import Path

from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

from src.core.config import config
from src.services.google_service import SCOPES, _save_tokens


def run_google_auth() -> int:
    if not config.google_client_id or not config.google_client_secret:
        print("Missing GOOGLE_CLIENT_ID or GOOGLE_CLIENT_SECRET in .env")
        print(
            "Add them from Google Cloud Console > APIs & Services > Credentials > OAuth 2.0 Client ID (Desktop)."
        )
        return 1

    client_config = {
        "installed": {
            "client_id": config.google_client_id,
            "client_secret": config.google_client_secret,
            "redirect_uris": ["http://localhost:6999/"],
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
        }
    }

    flow = InstalledAppFlow.from_client_config(client_config, scopes=SCOPES)
    print("Opening browser for Google sign-in...")
    creds = flow.run_local_server(port=6999)

    default_calendar_id = config.google_calendar_default_id
    try:
        service = build("calendar", "v3", credentials=creds, cache_discovery=False)
        cal_list = service.calendarList().list().execute()
        items = cal_list.get("items", [])
        if items:
            print("\nYour calendars:")
            for i, cal in enumerate(items):
                mark = " (primary)" if cal.get("primary") else ""
                print(
                    f"  {i + 1}. {cal.get('summary', 'No name')} — id: {cal.get('id')}{mark}"
                )
            choice = input(
                "\nSet default calendar by number (Enter = use 'primary'): "
            ).strip()
            if choice.isdigit() and 1 <= int(choice) <= len(items):
                default_calendar_id = items[int(choice) - 1]["id"]
                print(f"Default calendar set to: {default_calendar_id}")
    except Exception as e:
        print(f"Could not list calendars: {e}. Using default 'primary'.")

    default_task_list_id = ""
    try:
        tasks_service = build("tasks", "v1", credentials=creds, cache_discovery=False)
        task_lists = tasks_service.tasklists().list(maxResults=100).execute()
        items = task_lists.get("items", [])
        if items:
            print("\nYour task lists:")
            for i, lst in enumerate(items):
                print(f"  {i + 1}. {lst.get('title', 'No name')} — id: {lst.get('id')}")
            choice = input("\nSet default task list by number (Enter = skip): ").strip()
            if choice.isdigit() and 1 <= int(choice) <= len(items):
                default_task_list_id = items[int(choice) - 1]["id"]
                print(f"Default task list set to: {default_task_list_id}")
    except Exception as e:
        print(f"Could not list task lists: {e}. You can set default later.")

    path: Path = config.google_calendar_tokens_path
    path.parent.mkdir(parents=True, exist_ok=True)
    _save_tokens(path, creds, default_calendar_id, default_task_list_id)
    print(f"Tokens saved to {path}")
    print(
        "You can now use calendar_read, calendar_write, tasks_read, and tasks_write tools."
    )
    return 0
