"""Time helpers. The 'minute' override lets a demo show the 3-minute import margin."""
from datetime import datetime
from zoneinfo import ZoneInfo
from .config import settings

override_minute = None  # set by the dev panel, None = real time


def now():
    n = datetime.now(ZoneInfo(settings.import_tz))
    if override_minute is not None:
        n = n.replace(minute=int(override_minute), second=0, microsecond=0)
    return n


def minutes_to_import():
    n = now()
    return 60 - n.minute - n.second / 60
