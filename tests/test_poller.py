from dataclasses import replace
from datetime import timedelta

from app.core.poller import poll_account, poll_due, poll_one
from app.models import Kind, Mention, Role
from app.providers.base import AuthError, ProviderError
from app.timeutil import iso
from tests.factories import NOW, details, item, note, thread
from tests.fakes import FakeProvider


async def make_account(store, synced: bool):
    await store.register_user(1, 1, "me", "ru", False, 180, NOW)
    acc = await store.add_account(1, "gitlab", "gitlab.example.com", "me", "sealed", NOW)
    if synced:
        await store.update_account(acc.id, synced=1, mentions_cursor=iso(NOW - timedelta(hours=1)))
    return await store.get_account(acc.id)


async def events(store):
    return [e.event for e in await store.pending_events(1, NOW + timedelta(days=30))]


def calls(p, name):
    return [c for c in p.calls if c[0] == name]


async def test_first_sync_records_baseline_without_notifications(store):
    acc = await make_account(store, synced=False)
    p = FakeProvider()
    mr = item(Role.REVIEWER, 1)
    p.items = [mr]
    p.details_by_key[mr.key] = details(thread("t1", note(1, "alice")))
    res = await poll_account(p, store, acc, NOW)
    assert res.emitted == 0 and await events(store) == []
    assert await store.get_watched(acc.id, mr.key) is not None
    assert calls(p, "mentions") == []
    assert res.mentions_cursor == NOW


async def test_new_review_request_after_sync(store):
    acc = await make_account(store, synced=True)
    p = FakeProvider()
    mr = item(Role.REVIEWER, 2, author="bob")
    p.items = [mr]
    p.details_by_key[mr.key] = details()
    await poll_account(p, store, acc, NOW)
    [ev] = await events(store)
    assert ev.kind is Kind.REVIEW_REQUESTED and ev.actor == "bob" and ev.item == mr


async def test_unchanged_item_skips_details(store):
    acc = await make_account(store, synced=True)
    p = FakeProvider()
    mr = item(Role.REVIEWER, 1)
    p.items = [mr]
    p.details_by_key[mr.key] = details()
    await poll_account(p, store, acc, NOW)
    await poll_account(p, store, acc, NOW + timedelta(minutes=3))
    assert len(calls(p, "details")) == 1


async def test_changed_item_is_diffed(store):
    acc = await make_account(store, synced=True)
    p = FakeProvider()
    mr = item(Role.REVIEWER, 1)
    p.items = [mr]
    p.details_by_key[mr.key] = details(thread("t1", note(1, "me", "why?")))
    await poll_account(p, store, acc, NOW)
    newer = replace(mr, updated_at=mr.updated_at + timedelta(hours=1))
    p.items = [newer]
    p.details_by_key[mr.key] = details(thread("t1", note(1, "me", "why?"), note(2, "alice", "because")))
    await poll_account(p, store, acc, NOW + timedelta(hours=1))
    kinds = [e.kind for e in await events(store)]
    assert kinds == [Kind.REVIEW_REQUESTED, Kind.REPLY_TO_ME]


async def test_one_failing_mr_does_not_block_others(store):
    acc = await make_account(store, synced=True)
    p = FakeProvider()
    bad, good = item(Role.REVIEWER, 1), item(Role.REVIEWER, 2)
    p.items = [bad, good]
    p.details_by_key[bad.key] = ProviderError("403", status=403)
    p.details_by_key[good.key] = details()
    await poll_account(p, store, acc, NOW)
    assert [e.item.iid for e in await events(store)] == [2]
    assert await store.get_watched(acc.id, bad.key) is None


async def test_merged_own_mr_is_reported_and_forgotten(store):
    acc = await make_account(store, synced=True)
    p = FakeProvider()
    mine = item(Role.AUTHOR, 5, author="me")
    p.items = [mine]
    p.details_by_key[mine.key] = details(pending=["bob"])
    await poll_account(p, store, acc, NOW)
    p.items = []
    p.fetched[mine.key] = replace(mine, state="merged")
    await poll_account(p, store, acc, NOW + timedelta(minutes=3))
    assert [e.kind for e in await events(store)] == [Kind.MERGED]
    assert await store.get_watched(acc.id, mine.key) is None


async def test_refetch_failure_keeps_watching(store):
    acc = await make_account(store, synced=True)
    p = FakeProvider()
    mine = item(Role.AUTHOR, 5, author="me")
    p.items = [mine]
    p.details_by_key[mine.key] = details()
    await poll_account(p, store, acc, NOW)
    p.items = []
    await poll_account(p, store, acc, NOW + timedelta(minutes=3))
    assert await events(store) == []
    assert await store.get_watched(acc.id, mine.key) is not None


async def test_reviewer_item_gone_is_dropped_silently(store):
    acc = await make_account(store, synced=False)
    p = FakeProvider()
    mr = item(Role.REVIEWER, 1)
    p.items = [mr]
    p.details_by_key[mr.key] = details()
    await poll_account(p, store, acc, NOW)
    p.items = []
    await poll_account(p, store, acc, NOW + timedelta(minutes=3))
    assert await store.get_watched(acc.id, mr.key) is None
    assert calls(p, "fetch") == []


async def test_escalation_once_per_day_after_24h(store):
    acc = await make_account(store, synced=True)
    p = FakeProvider()
    mine = item(Role.AUTHOR, 5, author="me", updated="2026-09-22T12:00:00Z")
    p.items = [mine]
    p.details_by_key[mine.key] = details(pending=["bob", "carol"])
    await poll_account(p, store, acc, NOW)
    await poll_account(p, store, acc, NOW + timedelta(hours=1))
    waits = [e for e in await events(store) if e.kind is Kind.WAITING_ON_REVIEWER]
    assert len(waits) == 1
    assert waits[0].actors == ("bob", "carol") and waits[0].since == mine.updated_at
    await poll_account(p, store, acc, NOW + timedelta(days=1))
    assert len([e for e in await events(store) if e.kind is Kind.WAITING_ON_REVIEWER]) == 2


async def test_no_escalation_before_24h_or_for_drafts(store):
    acc = await make_account(store, synced=True)
    p = FakeProvider()
    fresh = item(Role.AUTHOR, 5, author="me", updated="2026-09-24T01:00:00Z")
    draft = item(Role.AUTHOR, 6, author="me", updated="2026-09-20T01:00:00Z", draft=True)
    p.items = [fresh, draft]
    p.details_by_key[fresh.key] = details(pending=["bob"])
    p.details_by_key[draft.key] = details(pending=["bob"])
    await poll_account(p, store, acc, NOW)
    assert await events(store) == []


async def test_mentions_after_sync_advance_cursor(store):
    acc = await make_account(store, synced=True)
    p = FakeProvider()
    at = NOW - timedelta(minutes=5)
    p.mention_list = [Mention("77", "bob", "@me look", "https://x#note_77", "Bug", at)]
    res = await poll_account(p, store, acc, NOW)
    [ev] = await events(store)
    assert ev.kind is Kind.MENTION and ev.dedup == "note:gitlab.example.com:77" and ev.title == "Bug"
    assert res.mentions_cursor == at


async def test_mention_duplicate_of_reply_is_dropped(store):
    acc = await make_account(store, synced=True)
    p = FakeProvider()
    mr = item(Role.REVIEWER, 1)
    p.items = [mr]
    p.details_by_key[mr.key] = details(thread("t1", note(1, "me", "why?")))
    await poll_account(p, store, acc, NOW)
    p.items = [replace(mr, updated_at=mr.updated_at + timedelta(hours=1))]
    p.details_by_key[mr.key] = details(thread("t1", note(1, "me", "why?"), note(2, "alice", "@me done")))
    p.mention_list = [Mention("2", "alice", "@me done", "u", "T", NOW)]
    await poll_account(p, store, acc, NOW + timedelta(hours=1))
    assert [e.kind for e in await events(store)] == [Kind.REVIEW_REQUESTED, Kind.REPLY_TO_ME]


async def test_mentions_failure_does_not_fail_poll(store):
    acc = await make_account(store, synced=True)
    p = FakeProvider()
    p.mentions_error = ProviderError("403 notifications need classic token", status=403)
    res = await poll_account(p, store, acc, NOW)
    assert res.mentions_cursor == NOW - timedelta(hours=1)


async def test_poll_one_records_auth_failure(store):
    acc = await make_account(store, synced=True)
    p = FakeProvider()
    p.list_error = AuthError("401", status=401)
    await poll_one(store, p, acc, NOW)
    fresh = await store.get_account(acc.id)
    assert fresh.last_error == "auth" and fresh.last_poll_at == iso(NOW)


async def test_poll_one_success_marks_synced(store):
    acc = await make_account(store, synced=False)
    await poll_one(store, FakeProvider(), acc, NOW)
    fresh = await store.get_account(acc.id)
    assert fresh.synced and fresh.last_ok_at == iso(NOW) and fresh.last_error is None
    assert fresh.mentions_cursor == iso(NOW)


async def test_poll_one_unexpected_error_is_recorded_not_raised(store):
    acc = await make_account(store, synced=True)
    p = FakeProvider()
    p.list_error = KeyError("username")
    await poll_one(store, p, acc, NOW)
    assert (await store.get_account(acc.id)).last_poll_at == iso(NOW)


async def test_poll_due_marks_undecryptable_token_as_auth(store):
    acc = await make_account(store, synced=True)

    def broken(_account):
        raise ValueError("InvalidToken")

    assert await poll_due(store, broken, NOW) == 1
    assert (await store.get_account(acc.id)).last_error == "auth"
