from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace

from telegram.error import BadRequest, Forbidden, NetworkError

from app.core.delivery import deliver_all, deliver_user
from app.models import Event, Kind
from tests.factories import NOW, item, note

# 2026-09-24 12:00 UTC is a Thursday afternoon in UTC; tests set tz=UTC and disable weekends.


class FakeBot:
    def __init__(self, fail=None, fail_chat=None):
        self.sent: list[tuple[int, str, dict]] = []
        self.fail = fail  # callable(text) -> Exception | None
        self.fail_chat = fail_chat or {}  # chat_id -> Exception

    async def send_message(self, chat_id, text, **kw):
        if chat_id in self.fail_chat:
            raise self.fail_chat[chat_id]
        if self.fail and (err := self.fail(text)):
            raise err
        self.sent.append((chat_id, text, kw))
        return SimpleNamespace(message_id=len(self.sent))


async def setup(store, **user_fields):
    await store.register_user(1, 100, "me", "en", False, 180, NOW)
    await store.update_user(1, tz="UTC", quiet_weekends=False, **user_fields)
    acc = await store.add_account(1, "gitlab", "gitlab.example.com", "me", "s", NOW)
    return acc, await store.get_user(1)


def comment(i, body="hi"):
    return Event(Kind.NEW_COMMENT, dedup=f"c{i}", item=item(iid=i), actor="alice", note=note(i, "alice", body),
                 thread_id="t")


async def test_sends_each_event_with_buttons(store):
    acc, user = await setup(store)
    await store.add_event(1, acc.id, comment(1), NOW)
    await store.add_event(1, acc.id, comment(2), NOW)
    bot = FakeBot()
    assert await deliver_user(bot, store, user, NOW) == 2
    assert [chat for chat, _, _ in bot.sent] == [100, 100]
    assert bot.sent[0][2]["reply_markup"] is not None
    assert await store.pending_events(1, NOW) == []


async def test_quiet_hours_hold_events(store):
    acc, user = await setup(store, quiet_from="11:00", quiet_to="13:00")
    await store.add_event(1, acc.id, comment(1), NOW)
    bot = FakeBot()
    assert await deliver_user(bot, store, user, NOW) == 0
    assert bot.sent == []
    assert len(await store.pending_events(1, NOW)) == 1


async def test_after_quiet_hours_waiting_events_become_one_digest(store):
    acc, user = await setup(store)
    for i in range(3):
        await store.add_event(1, acc.id, comment(i), NOW - timedelta(hours=8))
    await store.add_event(1, acc.id, comment(9), NOW)
    bot = FakeBot()
    assert await deliver_user(bot, store, user, NOW) == 2
    assert "3 events" in bot.sent[0][1]
    assert bot.sent[1][2]["reply_markup"] is not None


async def test_muted_kinds_are_swallowed(store):
    acc, user = await setup(store, muted_kinds=frozenset({"new_comment"}))
    await store.add_event(1, acc.id, comment(1), NOW)
    bot = FakeBot()
    assert await deliver_user(bot, store, user, NOW) == 0
    assert bot.sent == [] and await store.pending_events(1, NOW) == []


async def test_rejected_message_is_dropped_not_retried(store):
    acc, user = await setup(store)
    await store.add_event(1, acc.id, comment(1, "BROKEN"), NOW)
    await store.add_event(1, acc.id, comment(2, "fine"), NOW)
    bot = FakeBot(fail=lambda text: BadRequest("can't parse entities") if "BROKEN" in text else None)
    await deliver_user(bot, store, user, NOW)
    assert len(bot.sent) == 1 and "fine" in bot.sent[0][1]
    assert await store.pending_events(1, NOW) == []


async def test_blocked_user_stops_delivery(store):
    acc, user = await setup(store)
    await store.add_event(1, acc.id, comment(1), NOW)
    await store.add_event(1, acc.id, comment(2), NOW)
    bot = FakeBot(fail=lambda text: Forbidden("bot was blocked by the user"))
    await deliver_user(bot, store, user, NOW)
    assert (await store.get_user(1)).status == "blocked"
    assert await store.pending_events(1, NOW) == []


async def test_network_error_keeps_events_pending(store):
    acc, user = await setup(store)
    await store.add_event(1, acc.id, comment(1), NOW)
    bot = FakeBot(fail=lambda text: NetworkError("timeout"))
    await deliver_user(bot, store, user, NOW)
    assert len(await store.pending_events(1, NOW)) == 1


async def test_snoozed_event_returns_individually(store):
    acc, user = await setup(store)
    a = await store.add_event(1, acc.id, comment(1), NOW - timedelta(hours=5))
    b = await store.add_event(1, acc.id, comment(2), NOW - timedelta(hours=5))
    await store.mark_delivered([a, b], NOW - timedelta(hours=5))
    await store.snooze(a, NOW - timedelta(minutes=1))
    await store.snooze(b, NOW - timedelta(minutes=1))
    bot = FakeBot()
    assert await deliver_user(bot, store, replace(user), NOW) == 2
    assert all(kw["reply_markup"] is not None for _, _, kw in bot.sent)


async def test_one_users_failure_does_not_stop_others(store):
    acc, _ = await setup(store)
    await store.register_user(2, 200, "bob", "en", True, 180, NOW)
    await store.update_user(2, tz="UTC", quiet_weekends=False)
    acc2 = await store.add_account(2, "gitlab", "gitlab.example.com", "bob", "s", NOW)
    await store.add_event(1, acc.id, comment(1), NOW)
    await store.add_event(2, acc2.id, comment(2), NOW)
    bot = FakeBot(fail_chat={100: RuntimeError("boom")})
    await deliver_all(bot, store, NOW)
    assert [chat for chat, _, _ in bot.sent] == [200]


def by(actor, i, kind=Kind.NEW_COMMENT, iid=1, body=None):
    return Event(kind, dedup=f"{actor}{i}", item=item(iid=iid), actor=actor,
                 note=note(i, actor, body or f"comment {i}"), thread_id=f"t{i}")


async def test_burst_from_one_person_is_one_message(store):
    acc, user = await setup(store)
    for i in range(1, 5):
        await store.add_event(1, acc.id, by("alice", i, Kind.REPLY_TO_ME if i == 2 else Kind.NEW_COMMENT), NOW)
    await store.add_event(1, acc.id, by("bob", 9), NOW)
    bot = FakeBot()
    assert await deliver_user(bot, store, user, NOW) == 2
    burst, single = bot.sent[0][1], bot.sent[1][1]
    assert "@alice" in burst and "4" in burst and "replies to you: 1" in burst
    assert "comment 1" in burst and "comment 3" in burst and "comment 4" not in burst
    assert "@bob" in single
    assert await store.pending_events(1, NOW) == []


async def test_two_comments_are_not_a_burst(store):
    acc, user = await setup(store)
    await store.add_event(1, acc.id, by("alice", 1), NOW)
    await store.add_event(1, acc.id, by("alice", 2), NOW)
    bot = FakeBot()
    assert await deliver_user(bot, store, user, NOW) == 2


async def test_burst_is_per_merge_request(store):
    acc, user = await setup(store)
    for i in range(1, 4):
        await store.add_event(1, acc.id, by("alice", i, iid=1), NOW)
    for i in range(4, 6):
        await store.add_event(1, acc.id, by("alice", i, iid=2), NOW)
    bot = FakeBot()
    assert await deliver_user(bot, store, user, NOW) == 3
