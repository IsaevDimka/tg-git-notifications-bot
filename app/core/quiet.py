"""Quiet hours: evaluated in the user's time zone; overnight windows wrap past midnight."""

from datetime import datetime, time
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.storage.store import User


def user_zone(tz: str | None) -> ZoneInfo:
    try:
        return ZoneInfo(tz or "UTC")
    except (ZoneInfoNotFoundError, ValueError, OSError):
        return ZoneInfo("UTC")


def is_quiet(user: User, now: datetime) -> bool:
    if not user.quiet_enabled:
        return False
    local = now.astimezone(user_zone(user.tz))
    if user.quiet_weekends and local.weekday() >= 5:
        return True
    start, end = time.fromisoformat(user.quiet_from), time.fromisoformat(user.quiet_to)
    current = local.time()
    if start == end:
        return False
    if start < end:
        return start <= current < end
    return current >= start or current < end
