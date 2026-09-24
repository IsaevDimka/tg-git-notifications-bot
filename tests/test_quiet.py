from dataclasses import replace
from datetime import UTC, datetime

from app.core.quiet import is_quiet
from app.storage.store import User

BASE = User(
    tg_id=1, chat_id=1, username="u", lang="ru", tz="Europe/Moscow", status="active", is_admin=False,
    poll_interval=180, quiet_enabled=True, quiet_from="22:00", quiet_to="09:00", quiet_weekends=True,
    muted_kinds=frozenset(),
)
# 2026-09-24 is a Thursday. Moscow = UTC+3.
THU_NOON_MSK = datetime(2026, 9, 24, 9, 0, tzinfo=UTC)
THU_23_MSK = datetime(2026, 9, 24, 20, 0, tzinfo=UTC)
FRI_08_MSK = datetime(2026, 9, 25, 5, 0, tzinfo=UTC)
SAT_NOON_MSK = datetime(2026, 9, 26, 9, 0, tzinfo=UTC)


def test_overnight_window():
    assert not is_quiet(BASE, THU_NOON_MSK)
    assert is_quiet(BASE, THU_23_MSK)
    assert is_quiet(BASE, FRI_08_MSK)


def test_weekends():
    assert is_quiet(BASE, SAT_NOON_MSK)
    assert not is_quiet(replace(BASE, quiet_weekends=False), SAT_NOON_MSK)


def test_daytime_window():
    user = replace(BASE, quiet_from="12:00", quiet_to="13:00")
    assert is_quiet(user, THU_NOON_MSK.replace(hour=9, minute=30))
    assert not is_quiet(user, THU_NOON_MSK.replace(hour=11))


def test_disabled_or_empty_window():
    assert not is_quiet(replace(BASE, quiet_enabled=False), THU_23_MSK)
    assert not is_quiet(replace(BASE, quiet_from="09:00", quiet_to="09:00", quiet_weekends=False), THU_23_MSK)


def test_unknown_tz_falls_back_to_utc():
    user = replace(BASE, tz="Mars/Olympus")
    assert is_quiet(user, datetime(2026, 9, 24, 23, 0, tzinfo=UTC))
    assert not is_quiet(user, datetime(2026, 9, 24, 12, 0, tzinfo=UTC))
