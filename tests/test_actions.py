from datetime import UTC, datetime, timedelta

from app.bot import actions, inputs
from app.models import Event, Kind, Role
from app.providers.base import ProviderError
from tests.factories import NOW, item, note
from tests.fakes import FakeProvider
from tests.tg import callback_update, callbacks, make_ctx, message_update, texts


async def setup(store, provider=None):
    provider = provider or FakeProvider()
    ctx = make_ctx(store, provider)
    await store.register_user(1, 1, "me", "en", False, 180, NOW)
    await store.update_user(1, tz="Europe/Moscow")
    acc = await store.add_account(1, "gitlab", "gitlab.example.com", "me", ctx.bot_data["box"].seal("glpat-x"), NOW)
    comment_id = await store.add_event(1, acc.id, Event(
        Kind.REPLY_TO_ME, dedup="c", item=item(Role.REVIEWER, 42), actor="alice",
        note=note(2, "alice", "fixed"), quote=note(1, "me", "why?"), thread_id="t1", resolvable=True), NOW)
    review_id = await store.add_event(1, acc.id, Event(
        Kind.REVIEW_REQUESTED, dedup="r", item=item(Role.REVIEWER, 43), actor="alice"), NOW)
    wait_id = await store.add_event(1, acc.id, Event(
        Kind.WAITING_ON_REVIEWER, dedup="w", item=item(Role.AUTHOR, 44, author="me"), actors=("bob", "carol"),
        since=NOW - timedelta(days=2)), NOW)
    return ctx, provider, {"comment": comment_id, "review": review_id, "wait": wait_id}


def edited_markup(upd):
    return upd.callback_query.edit_message_reply_markup.await_args.kwargs["reply_markup"]


def test_parse():
    assert actions.parse("a:read:7") == ("read", 7, "")
    assert actions.parse("a:sz:7:tm") == ("sz", 7, "tm")


def test_snooze_until():
    assert actions.snooze_until("2h", NOW, "UTC") == NOW + timedelta(hours=2)
    assert actions.snooze_until("tm", NOW, "Europe/Moscow") == datetime(2026, 9, 25, 7, 0, tzinfo=UTC)
    assert actions.snooze_until("tm", NOW, None) == datetime(2026, 9, 25, 10, 0, tzinfo=UTC)


async def test_read_marks_read_and_clears_buttons(store):
    ctx, _, ids = await setup(store)
    upd = callback_update(f"a:read:{ids['comment']}", lang="en")
    await actions.cb_action(upd, ctx)
    assert (await store.get_event(ids["comment"], 1)).read_at is not None
    assert edited_markup(upd) is None


async def test_someone_elses_event_is_not_found(store):
    ctx, provider, ids = await setup(store)
    await store.register_user(2, 2, "eve", "en", True, 180, NOW)
    upd = callback_update(f"a:approve!:{ids['review']}", user_id=2, lang="en")
    await actions.cb_action(upd, ctx)
    assert upd.callback_query.answer.await_args.kwargs.get("show_alert") is True
    assert provider.calls == []


async def test_approve_needs_confirmation(store):
    ctx, provider, ids = await setup(store)
    upd = callback_update(f"a:approve:{ids['review']}", lang="en")
    await actions.cb_action(upd, ctx)
    assert callbacks(edited_markup(upd)) == [f"a:approve!:{ids['review']}", f"a:x:{ids['review']}"]
    assert provider.calls == []
    await actions.cb_action(callback_update(f"a:approve!:{ids['review']}", lang="en"), ctx)
    assert provider.calls == [("approve", item(Role.REVIEWER, 43).key)]
    assert (await store.get_event(ids["review"], 1)).read_at is not None


async def test_cancel_restores_original_keyboard(store):
    ctx, _, ids = await setup(store)
    upd = callback_update(f"a:x:{ids['review']}", lang="en")
    await actions.cb_action(upd, ctx)
    assert f"a:approve:{ids['review']}" in callbacks(edited_markup(upd))


async def test_ping_comments_with_mentions(store):
    ctx, provider, ids = await setup(store)
    await actions.cb_action(callback_update(f"a:ping!:{ids['wait']}", lang="en"), ctx)
    [(name, key, body)] = provider.calls
    assert name == "comment" and key == item(Role.AUTHOR, 44).key
    assert body.startswith("@bob @carol")


async def test_resolve(store):
    ctx, provider, ids = await setup(store)
    await actions.cb_action(callback_update(f"a:resolve:{ids['comment']}", lang="en"), ctx)
    assert provider.calls == [("resolve", item(Role.REVIEWER, 42).key, "t1")]


async def test_reply_flow(store):
    ctx, provider, ids = await setup(store)
    upd = callback_update(f"a:reply:{ids['comment']}", lang="en")
    await actions.cb_action(upd, ctx)
    assert ctx.user_data["await"] == {"kind": "reply", "event_id": ids["comment"]}
    assert "!42" in texts(upd.effective_chat.send_message)[0]
    answer = message_update("Thanks, merged the fix", lang="en")
    await inputs.on_text(answer, ctx)
    assert provider.calls == [("reply", item(Role.REVIEWER, 42).key, "t1", "Thanks, merged the fix")]
    assert "!42" in texts(answer.effective_chat.send_message)[0]


async def test_forbidden_write_explains_missing_scope(store):
    provider = FakeProvider()
    provider.write_error = ProviderError("403", status=403)
    ctx, _, ids = await setup(store, provider)
    upd = callback_update(f"a:resolve:{ids['comment']}", lang="en")
    await actions.cb_action(upd, ctx)
    call = upd.callback_query.answer.await_args
    assert "api" in call.args[0] and call.kwargs["show_alert"] is True
    assert (await store.get_event(ids["comment"], 1)).read_at is None


async def test_snooze_flow(store):
    ctx, _, ids = await setup(store)
    upd = callback_update(f"a:snooze:{ids['comment']}", lang="en")
    await actions.cb_action(upd, ctx)
    assert f"a:sz:{ids['comment']}:2h" in callbacks(edited_markup(upd))
    await store.mark_delivered([ids["comment"]], NOW)
    await actions.cb_action(callback_update(f"a:sz:{ids['comment']}:2h", lang="en"), ctx)
    ev = await store.get_event(ids["comment"], 1)
    assert ev.delivered_at is None and ev.snoozed_until is not None


async def test_read_item_marks_every_event_of_that_mr(store):
    ctx, _, ids = await setup(store)
    acc = (await store.accounts_for(1))[0]
    extra = await store.add_event(1, acc.id, Event(Kind.NEW_COMMENT, dedup="x", item=item(Role.REVIEWER, 42),
                                                   actor="bob", note=note(5, "bob")), NOW)
    await actions.cb_action(callback_update(f"a:ri:{extra}", lang="en"), ctx)
    assert (await store.get_event(ids["comment"], 1)).read_at is not None
    assert (await store.get_event(extra, 1)).read_at is not None
    assert (await store.get_event(ids["review"], 1)).read_at is None
