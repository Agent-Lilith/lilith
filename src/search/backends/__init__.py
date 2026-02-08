from src.search.backends.web import WebSearchBackend
from src.search.backends.email import EmailSearchBackend
from src.search.backends.calendar import CalendarSearchBackend
from src.search.backends.tasks import TasksSearchBackend

__all__ = [
    "WebSearchBackend",
    "EmailSearchBackend",
    "CalendarSearchBackend",
    "TasksSearchBackend",
]
