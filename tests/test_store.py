from datetime import timedelta

import pytest

from app.models import Ball, Event, Kind, Role
from app.storage.schema import MIGRATIONS
from app.storage.store import Store, Watched
from app.timeutil import iso
from tests.factories import NOW, item, note


async def _user(store, tg_id=1, preapproved=False):
    user, _ = await store.register_user(tg_id, tg_id, f"u{tg_id}", "ru", preapproved, 180, NOW)
    return user


async def test_migrations_are_idempotent(tmp_path):
    path = tmp_path / "bot.db"
    s = await Store.open(path)
    await s.close()
    s = await Store.open(path)
    async with s.db.execute("PRAGMA user_version") as cur:
        assert (await cur.fetchone())[0] == len(MIGRATIONS)
    await s.close()


async def test_first_user_is_admin_rest_pending(store):
    first, created = await store.register_user(1, 1, "a", "ru", False, 180, NOW)
    second, _ = await store.register_user(2, 2, "b", "en", False, 180, NOW)
    assert created and first.is_admin and first.status == "active"
    assert not second.is_admin and second.status == "pending"
    assert [u.tg_id for u in await store.admins()] == [1]


async def test_preapproved_user_is_active_not_admin(store):
    await _user(store, 1)
    user = await _user(store, 2, preapproved=True)
    assert user.status == "active" and not user.is_admin


async def test_register_twice_returns_existing(store):
    await _user(store, 1)
    again, created = await store.register_user(1, 1, "other", "en", False, 180, NOW)
    assert not created and again.username == "u1"


async def test_update_user_fields_and_muted_kinds(store):
    await _user(store, 1)
    await store.update_user(1, tz="Europe/Moscow", muted_kinds=frozenset({"new_comment"}))
    user = await store.get_user(1)
    assert user.tz == "Europe/Moscow"
    assert user.muted_kinds == frozenset({"new_comment"})
    with pytest.raises(ValueError):
        await store.update_user(1, is_root=True)


async def test_add_account_upsert_resets_sync_and_watched(store):
    await _user(store, 1)
    acc = await store.add_account(1, "gitlab", "gitlab.example.com", "me", "sealed1", NOW)
    await store.update_account(acc.id, synced=1)
    await store.put_watched(Watched(acc.id, "k", Role.REVIEWER, item(), NOW, {}, Ball.ME, NOW))
    again = await store.add_account(1, "gitlab", "gitlab.example.com", "me2", "sealed2", NOW)
    assert again.id == acc.id
    assert again.username == "me2" and again.token_enc == "sealed2" and not again.synced
    assert await store.list_watched(acc.id) == []


async def test_delete_account_only_own(store):
    await _user(store, 1)
    await _user(store, 2, preapproved=True)
    acc = await store.add_account(1, "github", "github.com", "me", "s", NOW)
    assert not await store.delete_account(acc.id, tg_id=2)
    assert await store.delete_account(acc.id, tg_id=1)
    assert await store.accounts_for(1) == []


async def test_watched_roundtrip(store):
    await _user(store, 1)
    acc = await store.add_account(1, "gitlab", "gitlab.example.com", "me", "s", NOW)
    w = Watched(acc.id, item().key, Role.REVIEWER, item(), item().updated_at, {"notes": ["1"]}, Ball.THEM, NOW)
    await store.put_watched(w)
    assert await store.get_watched(acc.id, w.key) == w
    assert await store.watched_for_user(1) == [w]
    await store.delete_watched(acc.id, w.key)
    assert await store.get_watched(acc.id, w.key) is None


async def test_events_dedup_pending_and_snooze(store):
    await _user(store, 1)
    acc = await store.add_account(1, "gitlab", "gitlab.example.com", "me", "s", NOW)
    ev = Event(Kind.NEW_COMMENT, dedup="note:h:1", item=item(), actor="alice", note=note(1, "alice"))
    first = await store.add_event(1, acc.id, ev, NOW)
    assert first is not None
    assert await store.add_event(1, acc.id, ev, NOW) is None
    [pending] = await store.pending_events(1, NOW)
    assert pending.event == ev
    await store.mark_delivered([first], NOW)
    assert await store.pending_events(1, NOW) == []
    await store.snooze(first, NOW + timedelta(hours=2))
    assert await store.pending_events(1, NOW + timedelta(hours=1)) == []
    assert len(await store.pending_events(1, NOW + timedelta(hours=2))) == 1


async def test_unread_counts_and_mark_read(store):
    await _user(store, 1)
    acc = await store.add_account(1, "gitlab", "gitlab.example.com", "me", "s", NOW)
    a = await store.add_event(1, acc.id, Event(Kind.NEW_COMMENT, dedup="d1", item=item(iid=1)), NOW)
    await store.add_event(1, acc.id, Event(Kind.NEW_COMMENT, dedup="d2", item=item(iid=1)), NOW)
    await store.add_event(1, acc.id, Event(Kind.MENTION, dedup="d3", note=note(3, "bob")), NOW)
    assert await store.unread_counts(1) == {item(iid=1).key: 2, "mention:d3": 1}
    assert await store.unread_mention_count(1) == 1
    await store.mark_read([a], NOW)
    assert (await store.unread_counts(1))[item(iid=1).key] == 1
    await store.mark_item_read(1, item(iid=1).key, NOW)
    assert item(iid=1).key not in await store.unread_counts(1)
    await store.mark_all_read(1, NOW)
    assert await store.unread_counts(1) == {}


async def test_get_event_scoped_to_owner(store):
    await _user(store, 1)
    await _user(store, 2, preapproved=True)
    acc = await store.add_account(1, "gitlab", "gitlab.example.com", "me", "s", NOW)
    eid = await store.add_event(1, acc.id, Event(Kind.NEW_COMMENT, dedup="d", item=item()), NOW)
    assert await store.get_event(eid, 1) is not None
    assert await store.get_event(eid, 2) is None


async def test_due_accounts_respects_interval_and_status(store):
    await _user(store, 1)
    await _user(store, 2)  # pending — never polled
    acc = await store.add_account(1, "gitlab", "gitlab.example.com", "me", "s", NOW)
    await store.add_account(2, "gitlab", "gitlab.example.com", "bob", "s", NOW)
    assert [a.id for a in await store.due_accounts(NOW)] == [acc.id]
    await store.update_account(acc.id, last_poll_at=iso(NOW))
    assert await store.due_accounts(NOW + timedelta(seconds=179)) == []
    assert [a.id for a in await store.due_accounts(NOW + timedelta(seconds=180))] == [acc.id]


async def test_digest_defaults_and_update(store):
    user = await _user(store, 1)
    assert user.digest_enabled and user.digest_time == "10:00" and user.digest_last is None
    await store.update_user(1, digest_enabled=False, digest_time="09:00", digest_last="2026-09-24")
    user = await store.get_user(1)
    assert (user.digest_enabled, user.digest_time, user.digest_last) == (False, "09:00", "2026-09-24")


async def test_noise_settings_defaults_and_update(store):
    user = await _user(store, 1)
    assert user.mute_bots and user.mute_drafts and user.muted_projects == frozenset()
    await store.update_user(1, mute_bots=False, muted_projects=frozenset({"g/app"}))
    user = await store.get_user(1)
    assert not user.mute_bots and user.muted_projects == frozenset({"g/app"})
