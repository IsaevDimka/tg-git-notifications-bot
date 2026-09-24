from dataclasses import replace
from datetime import timedelta

from app.core.poller import poll_account, poll_due, poll_one
from app.models import Ball, Kind, Mention, Role
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
    assert kinds == [Kind.REPLY_TO_ME]  # I had already commented when it first appeared → no review request


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
    assert [e.kind for e in await events(store)] == [Kind.REPLY_TO_ME]


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


async def test_unchanged_items_are_rechecked_hourly(store):
    acc = await make_account(store, synced=True)
    p = FakeProvider()
    mine = item(Role.AUTHOR, 5, author="me", updated="2026-09-24T11:30:00Z")
    p.items = [mine]
    p.details_by_key[mine.key] = details(pending=["bob"])
    await poll_account(p, store, acc, NOW)
    p.details_by_key[mine.key] = details(pending=["bob"], conflicts=True)
    await poll_account(p, store, acc, NOW + timedelta(minutes=30))
    assert len(calls(p, "details")) == 1
    await poll_account(p, store, acc, NOW + timedelta(minutes=61))
    assert len(calls(p, "details")) == 2
    assert [e.kind for e in await events(store)] == [Kind.CONFLICT]


async def test_new_item_i_already_reviewed_is_not_a_review_request(store):
    acc = await make_account(store, synced=True)
    p = FakeProvider()
    commented, approved = item(Role.REVIEWER, 1), item(Role.REVIEWER, 2)
    p.items = [commented, approved]
    p.details_by_key[commented.key] = details(thread("t1", note(1, "me", "nit")))
    p.details_by_key[approved.key] = details(approved=["me"])
    await poll_account(p, store, acc, NOW)
    assert await events(store) == []


async def test_snapshot_remembers_my_approval(store):
    acc = await make_account(store, synced=True)
    p = FakeProvider()
    mr = item(Role.REVIEWER, 1)
    p.items = [mr]
    p.details_by_key[mr.key] = details(approved=["me"])
    await poll_account(p, store, acc, NOW)
    assert (await store.get_watched(acc.id, mr.key)).snapshot["approved_by_me"] is True


async def test_role_change_is_applied_immediately(store):
    acc = await make_account(store, synced=True)
    p = FakeProvider()
    mr = item(Role.REVIEWER, 1)
    p.items = [mr]
    p.details_by_key[mr.key] = details()
    await poll_account(p, store, acc, NOW)
    p.items = [replace(mr, role=Role.AUTHOR)]
    await poll_account(p, store, acc, NOW + timedelta(minutes=3))
    assert (await store.get_watched(acc.id, mr.key)).role is Role.AUTHOR


async def test_dead_token_is_reported_once(store):
    acc = await make_account(store, synced=True)
    p = FakeProvider()
    p.list_error = AuthError("401", status=401)
    await poll_one(store, p, acc, NOW)
    await poll_one(store, p, await store.get_account(acc.id), NOW + timedelta(minutes=3))
    [ev] = await events(store)
    assert ev.kind is Kind.TOKEN_BROKEN and ev.title == "gitlab.example.com"


async def test_token_broken_again_after_recovery_is_reported_again(store):
    acc = await make_account(store, synced=True)
    p = FakeProvider()
    p.list_error = AuthError("401", status=401)
    await poll_one(store, p, acc, NOW)
    p.list_error = None
    await poll_one(store, p, await store.get_account(acc.id), NOW + timedelta(minutes=3))
    p.list_error = AuthError("401", status=401)
    await poll_one(store, p, await store.get_account(acc.id), NOW + timedelta(minutes=6))
    assert [e.kind for e in await events(store)] == [Kind.TOKEN_BROKEN, Kind.TOKEN_BROKEN]


async def test_undecryptable_token_is_reported(store):
    await make_account(store, synced=True)

    def broken(_account):
        raise ValueError("InvalidToken")

    await poll_due(store, broken, NOW)
    assert [e.kind for e in await events(store)] == [Kind.TOKEN_BROKEN]


async def test_new_commits_after_my_review_make_it_my_move(store):
    acc = await make_account(store, synced=True)
    p = FakeProvider()
    mr = item(Role.REVIEWER, 1)
    p.items = [mr]
    p.details_by_key[mr.key] = details(thread("t", note(1, "me", "please fix")), cr=["me"], head_sha="aaa")
    await poll_account(p, store, acc, NOW)
    assert (await store.get_watched(acc.id, mr.key)).ball is Ball.THEM
    p.items = [replace(mr, updated_at=mr.updated_at + timedelta(hours=1))]
    p.details_by_key[mr.key] = details(thread("t", note(1, "me", "please fix")), cr=["me"], head_sha="bbb")
    await poll_account(p, store, acc, NOW + timedelta(hours=1))
    assert (await store.get_watched(acc.id, mr.key)).ball is Ball.ME
    assert [e.kind for e in await events(store)] == [Kind.REREVIEW]


async def test_mr_waiting_on_my_review_is_reminded_daily(store):
    acc = await make_account(store, synced=False)
    p = FakeProvider()
    old = item(Role.REVIEWER, 1, updated="2026-09-21T12:00:00Z")  # my move for 3 days
    fresh = item(Role.REVIEWER, 2, updated="2026-09-23T10:00:00Z")  # 26 h now, 50 h tomorrow
    p.items = [old, fresh]
    p.details_by_key[old.key] = details()
    p.details_by_key[fresh.key] = details()
    await poll_account(p, store, acc, NOW)  # baseline
    await store.update_account(acc.id, synced=1, mentions_cursor=iso(NOW))
    acc = await store.get_account(acc.id)
    await poll_account(p, store, acc, NOW + timedelta(minutes=3))
    await poll_account(p, store, acc, NOW + timedelta(minutes=6))
    stale = [e for e in await events(store) if e.kind is Kind.STALE_REVIEW]
    assert [e.item.iid for e in stale] == [1] and stale[0].since == old.updated_at
    await poll_account(p, store, acc, NOW + timedelta(days=1))
    assert len([e for e in await events(store) if e.kind is Kind.STALE_REVIEW]) == 3  # #1 again, #2 now 2d+


async def test_reminders_stop_after_30_days(store):
    acc = await make_account(store, synced=True)
    p = FakeProvider()
    ancient_review = item(Role.REVIEWER, 1, updated="2026-08-01T12:00:00Z")
    ancient_mine = item(Role.AUTHOR, 2, author="me", updated="2026-08-01T12:00:00Z")
    p.items = [ancient_review, ancient_mine]
    p.details_by_key[ancient_review.key] = details()
    p.details_by_key[ancient_mine.key] = details(pending=["bob"])
    await poll_account(p, store, acc, NOW)
    kinds = [e.kind for e in await events(store)]
    assert Kind.STALE_REVIEW not in kinds and Kind.WAITING_ON_REVIEWER not in kinds


async def test_reminders_are_capped_per_tick(store):
    acc = await make_account(store, synced=False)
    p = FakeProvider()
    p.items = [item(Role.REVIEWER, i, updated="2026-09-20T12:00:00Z") for i in range(1, 8)]
    for it in p.items:
        p.details_by_key[it.key] = details()
    await poll_account(p, store, acc, NOW)
    await store.update_account(acc.id, synced=1, mentions_cursor=iso(NOW))
    acc = await store.get_account(acc.id)
    await poll_account(p, store, acc, NOW + timedelta(minutes=3))
    assert len([e for e in await events(store) if e.kind is Kind.STALE_REVIEW]) == 5
    await poll_account(p, store, acc, NOW + timedelta(minutes=6))
    assert len([e for e in await events(store) if e.kind is Kind.STALE_REVIEW]) == 7


async def test_watched_by_link_reports_approvals_and_merge(store):
    acc = await make_account(store, synced=True)
    p = FakeProvider()
    theirs = item(Role.REVIEWER, 9, author="bob")  # role is overridden to WATCHER by get_by_ref
    p.by_ref[("g/app", 9)] = theirs
    p.details_by_key[theirs.key] = details()
    await store.add_watch_ref(acc.id, "g/app", 9, NOW)
    await poll_account(p, store, acc, NOW)
    w = await store.get_watched(acc.id, theirs.key)
    assert w.role is Role.WATCHER and w.ball is Ball.NONE
    assert await events(store) == []  # watching is not a review request
    p.by_ref[("g/app", 9)] = replace(theirs, updated_at=theirs.updated_at + timedelta(hours=1))
    p.details_by_key[theirs.key] = details(approved=["carol"])
    await poll_account(p, store, acc, NOW + timedelta(hours=1))
    p.by_ref[("g/app", 9)] = replace(theirs, state="merged")
    await poll_account(p, store, acc, NOW + timedelta(hours=2))
    assert [e.kind for e in await events(store)] == [Kind.APPROVED, Kind.MERGED]
    assert await store.watch_refs(acc.id) == []
    assert await store.get_watched(acc.id, theirs.key) is None


async def test_watching_my_own_review_item_does_not_duplicate(store):
    acc = await make_account(store, synced=True)
    p = FakeProvider()
    mine = item(Role.REVIEWER, 9)
    p.items = [mine]
    p.by_ref[("g/app", 9)] = mine
    p.details_by_key[mine.key] = details()
    await store.add_watch_ref(acc.id, "g/app", 9, NOW)
    await poll_account(p, store, acc, NOW)
    assert (await store.get_watched(acc.id, mine.key)).role is Role.REVIEWER


async def test_poll_one_stores_rate_limit(store):
    acc = await make_account(store, synced=True)
    p = FakeProvider()
    p.rate_remaining = 321
    await poll_one(store, p, acc, NOW)
    assert (await store.get_account(acc.id)).rate_remaining == 321


async def test_review_request_on_a_draft_arrives_when_it_is_ready(store):
    acc = await make_account(store, synced=True)
    p = FakeProvider()
    draft = item(Role.REVIEWER, 1, draft=True)
    p.items = [draft]
    p.details_by_key[draft.key] = details()
    await poll_account(p, store, acc, NOW)
    assert await events(store) == []
    ready = replace(draft, draft=False, updated_at=draft.updated_at + timedelta(hours=1))
    p.items = [ready]
    await poll_account(p, store, acc, NOW + timedelta(hours=1))
    [ev] = await events(store)
    assert ev.kind is Kind.REVIEW_REQUESTED and ev.item.iid == 1


async def test_rereview_is_announced_once_for_several_pushes(store):
    acc = await make_account(store, synced=True)
    p = FakeProvider()
    mr = item(Role.REVIEWER, 1)
    my_note = thread("t", note(1, "me", "please fix", at="2026-09-24T09:00:00Z"))
    p.items = [mr]
    p.details_by_key[mr.key] = details(my_note, cr=["me"], head_sha="a")
    await poll_account(p, store, acc, NOW)
    for i, sha in enumerate(("b", "c", "d"), start=1):
        p.items = [replace(mr, updated_at=mr.updated_at + timedelta(hours=i))]
        p.details_by_key[mr.key] = details(my_note, cr=["me"], head_sha=sha)
        await poll_account(p, store, acc, NOW + timedelta(hours=i))
    assert [e.kind for e in await events(store)] == [Kind.REREVIEW]
    assert (await store.get_watched(acc.id, mr.key)).ball is Ball.ME


async def test_no_rereview_if_i_already_commented_after_the_push(store):
    acc = await make_account(store, synced=True)
    p = FakeProvider()
    mr = item(Role.REVIEWER, 1)
    p.items = [mr]
    p.details_by_key[mr.key] = details(thread("t", note(1, "me", "fix", at="2026-09-24T09:00:00Z")), cr=["me"], head_sha="a")
    await poll_account(p, store, acc, NOW)
    p.items = [replace(mr, updated_at=mr.updated_at + timedelta(hours=1))]
    fresh_note = note(2, "me", "looked again, fine", at="2026-09-24T12:30:00Z")  # after last check, before this poll
    p.details_by_key[mr.key] = details(thread("t", note(1, "me", "fix", at="2026-09-24T09:00:00Z"), fresh_note),
                                       cr=["me"], head_sha="b")
    await poll_account(p, store, acc, NOW + timedelta(hours=1))
    assert Kind.REREVIEW not in [e.kind for e in await events(store)]


async def test_token_dead_before_upgrade_is_still_reported_once(store):
    acc = await make_account(store, synced=True)
    await store.update_account(acc.id, last_error="auth")  # marked by an older version, never notified
    p = FakeProvider()
    p.list_error = AuthError("401", status=401)
    await poll_one(store, p, await store.get_account(acc.id), NOW)
    await poll_one(store, p, await store.get_account(acc.id), NOW + timedelta(minutes=3))
    assert [e.kind for e in await events(store)] == [Kind.TOKEN_BROKEN]


async def test_network_errors_are_logged_without_traceback(store, caplog):
    import httpx

    acc = await make_account(store, synced=True)
    p = FakeProvider()
    p.list_error = httpx.ConnectTimeout("timed out")
    with caplog.at_level("WARNING"):
        await poll_one(store, p, acc, NOW)
    [record] = [r for r in caplog.records if "account" in r.getMessage()]
    assert record.levelname == "WARNING" and record.exc_info is None
    assert (await store.get_account(acc.id)).last_error == "ConnectTimeout: timed out"
