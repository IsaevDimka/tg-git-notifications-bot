from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace

from app.bot.daily import digest_due, render_daily, send_daily
from app.models import Ball, Role
from app.storage.store import User, Watched
from tests.factories import NOW, item

USER = User(
    tg_id=1, chat_id=100, username="me", lang="en", tz="Europe/Moscow", status="active", is_admin=True,
    poll_interval=180, quiet_enabled=True, quiet_from="22:00", quiet_to="09:00", quiet_weekends=True,
    muted_kinds=frozenset(),
)
# 2026-09-24 is a Thursday; 07:30 UTC = 10:30 in Moscow.
THU_1030_MSK = datetime(2026, 9, 24, 7, 30, tzinfo=UTC)


def watched(iid, role=Role.REVIEWER, ball=Ball.ME, account_id=1, **kw) -> Watched:
    it = item(role, iid, **kw)
    return Watched(account_id, it.key, role, it, it.updated_at, {}, ball, it.updated_at)


def test_digest_due_once_per_day_after_time():
    assert digest_due(USER, THU_1030_MSK) == "2026-09-24"
    assert digest_due(replace(USER, digest_last="2026-09-24"), THU_1030_MSK) is None
    assert digest_due(replace(USER, digest_time="11:00"), THU_1030_MSK) is None
    assert digest_due(replace(USER, digest_enabled=False), THU_1030_MSK) is None


def test_digest_respects_weekends_and_quiet_hours():
    saturday = datetime(2026, 9, 26, 7, 30, tzinfo=UTC)
    assert digest_due(USER, saturday) is None
    assert digest_due(replace(USER, quiet_weekends=False), saturday) == "2026-09-26"
    early = replace(USER, digest_time="09:00", quiet_to="10:00")
    assert digest_due(early, datetime(2026, 9, 24, 6, 30, tzinfo=UTC)) is None  # 09:30 MSK, still quiet


def test_render_daily_sections():
    ws = [
        watched(1, Role.REVIEWER, Ball.ME),
        watched(2, Role.REVIEWER, Ball.NONE),
        watched(3, Role.AUTHOR, Ball.ME, author="me"),
        watched(4, Role.AUTHOR, Ball.THEM, author="me"),
        watched(5, Role.AUTHOR, Ball.THEM, author="me"),
    ]
    text = render_daily(ws, "en", NOW)
    assert "Waiting for your review: 1" in text and "!1" in text and "!2" not in text
    assert "Your merge requests where it's your move: 1" in text and "!3" in text
    assert "waiting for reviewers: 2" in text and "!4" not in text
    assert "/settings" in text


def test_render_daily_nothing_to_say():
    assert render_daily([watched(2, Role.REVIEWER, Ball.NONE)], "en", NOW) is None


class Bot:
    def __init__(self):
        self.sent = []

    async def send_message(self, chat_id, text, **kw):
        self.sent.append((chat_id, text))
        return SimpleNamespace(message_id=1)


async def test_send_daily_sends_once_and_remembers(store):
    await store.register_user(1, 100, "me", "en", False, 180, NOW)
    await store.update_user(1, tz="Europe/Moscow")
    acc = await store.add_account(1, "gitlab", "gitlab.example.com", "me", "s", NOW)
    await store.put_watched(watched(7, account_id=acc.id))
    bot = Bot()
    assert await send_daily(bot, store, THU_1030_MSK) == 1
    assert await send_daily(bot, store, THU_1030_MSK) == 0
    assert bot.sent[0][0] == 100 and "!7" in bot.sent[0][1]
    assert (await store.get_user(1)).digest_last == "2026-09-24"


async def test_send_daily_empty_marks_day_without_message(store):
    await store.register_user(1, 100, "me", "en", False, 180, NOW)
    await store.update_user(1, tz="Europe/Moscow")
    bot = Bot()
    assert await send_daily(bot, store, THU_1030_MSK) == 0
    assert bot.sent == [] and (await store.get_user(1)).digest_last == "2026-09-24"
