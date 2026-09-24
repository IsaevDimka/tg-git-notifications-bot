from app.bot import views
from app.models import Ball, Event, Kind, Role
from app.storage.store import Watched
from tests.factories import NOW, item, note
from tests.tg import callback_update, callbacks, make_ctx, message_update, texts


def watched(iid, role=Role.REVIEWER, ball=Ball.ME, snapshot=None, account_id=1, **kw) -> Watched:
    it = item(role, iid, **kw)
    return Watched(account_id, it.key, role, it, it.updated_at, snapshot or {}, ball, it.updated_at)


def test_mr_line_shows_state():
    w = watched(42, snapshot={"approved_by": ["bob"], "open_threads": 2, "pipeline": "failed"}, draft=True)
    line = views.mr_line(w, "en", NOW)
    assert line.startswith("🔴 📝 ")
    assert "!42" in line and "👍 1" in line and "💬 2" in line and "CI ✗" in line and "4 d" in line


def test_mr_line_shows_required_approvals():
    w = watched(5, role=Role.AUTHOR, snapshot={"approved_by": ["bob"], "approvals_left": 1}, author="me")
    assert "👍 1/2" in views.mr_line(w, "en", NOW)


def test_mr_list_splits_tabs_and_orders_my_moves_first():
    ws = [
        watched(1, ball=Ball.THEM, updated="2026-09-24T11:00:00Z"),
        watched(2, ball=Ball.ME, updated="2026-09-23T11:00:00Z"),
        watched(3, role=Role.AUTHOR, ball=Ball.THEM, author="me"),
    ]
    text = views.render_mr_list(ws, "rev", "en", NOW)
    assert text.index("!2") < text.index("!1")
    assert "!3" not in text
    assert "!3" in views.render_mr_list(ws, "own", "en", NOW)


def test_mr_list_empty_and_capped():
    assert "Nothing waiting" in views.render_mr_list([], "rev", "en", NOW)
    many = [watched(i) for i in range(1, 21)]
    text = views.render_mr_list(many, "rev", "en", NOW)
    assert "…and 5 more" in text
    assert len(text) < 4096


def test_mr_tabs_mark_active():
    markup = views.mr_tabs("own", "en")
    assert callbacks(markup) == ["mr:rev", "mr:own"]
    assert markup.inline_keyboard[0][1].text.startswith("• ")


def test_inbox_only_my_moves_with_unread_and_mentions():
    mine, theirs = watched(1, ball=Ball.ME), watched(2, ball=Ball.THEM)
    text, shown, empty = views.render_inbox([mine, theirs], {mine.key: 3}, 2, "en", NOW)
    assert not empty
    assert "!1" in text and "!2" not in text and "unread: 3" in text and "Mentions outside" in text
    assert shown == [mine]
    assert callbacks(views.inbox_keyboard(shown, "en")) == ["ib:r:0", "ib:all"]


def test_inbox_empty():
    text, shown, empty = views.render_inbox([watched(1, ball=Ball.NONE)], {}, 0, "en", NOW)
    assert empty and shown == [] and "Nobody is waiting" in text


async def _setup(store):
    await store.register_user(1, 1, "me", "en", False, 180, NOW)
    return await store.add_account(1, "gitlab", "gitlab.example.com", "me", "s", NOW)


async def test_cmd_mr_without_accounts(store):
    await store.register_user(1, 1, "me", "en", False, 180, NOW)
    ctx = make_ctx(store)
    upd = message_update("/mr", lang="en")
    await views.cmd_mr(upd, ctx)
    assert "/start" in texts(upd.effective_chat.send_message)[0]


async def test_cmd_mr_lists_watched(store):
    acc = await _setup(store)
    await store.put_watched(watched(7, account_id=acc.id))
    ctx = make_ctx(store)
    upd = message_update("/mr", lang="en")
    await views.cmd_mr(upd, ctx)
    call = upd.effective_chat.send_message.await_args
    assert "!7" in call.args[0]
    assert callbacks(call.kwargs["reply_markup"]) == ["mr:rev", "mr:own"]


async def test_cb_inbox_marks_item_and_all_read(store):
    acc = await _setup(store)
    w = watched(7, account_id=acc.id)
    await store.put_watched(w)
    await store.add_event(1, acc.id, Event(Kind.NEW_COMMENT, dedup="a", item=w.item, note=note(1, "x")), NOW)
    await store.add_event(1, acc.id, Event(Kind.MENTION, dedup="b", note=note(2, "y")), NOW)
    ctx = make_ctx(store)
    await views.cmd_inbox(message_update("/inbox", lang="en"), ctx)
    assert ctx.user_data["inbox_keys"] == [w.key]
    await views.cb_inbox(callback_update("ib:r:0", lang="en"), ctx)
    assert await store.unread_counts(1) == {"mention:b": 1}
    upd = callback_update("ib:all", lang="en")
    await views.cb_inbox(upd, ctx)
    assert await store.unread_counts(1) == {}
    upd.callback_query.edit_message_text.assert_awaited()


async def test_cb_inbox_stale_index_is_harmless(store):
    await _setup(store)
    ctx = make_ctx(store)
    await views.cb_inbox(callback_update("ib:r:5", lang="en"), ctx)


async def test_cmd_my_opens_own_tab(store):
    acc = await _setup(store)
    await store.put_watched(watched(8, role=Role.AUTHOR, author="me", account_id=acc.id))
    await store.put_watched(watched(9, account_id=acc.id))
    ctx = make_ctx(store)
    upd = message_update("/my", lang="en")
    await views.cmd_my(upd, ctx)
    call = upd.effective_chat.send_message.await_args
    assert "!8" in call.args[0] and "!9" not in call.args[0]
    assert call.kwargs["reply_markup"].inline_keyboard[0][1].text.startswith("• ")


def test_review_tab_hides_what_i_already_approved():
    ws = [watched(1), watched(2, ball=Ball.NONE, snapshot={"approved_by_me": True})]
    text = views.render_mr_list(ws, "rev", "en", NOW)
    assert "!1" in text and "!2" not in text
    assert "Already approved by you: 1" in text
    only_approved = views.render_mr_list([ws[1]], "rev", "en", NOW)
    assert "Nothing waiting" in only_approved and "Already approved by you: 1" in only_approved


async def test_muted_project_is_hidden_from_lists(store):
    from dataclasses import replace as dc_replace

    acc = await _setup(store)
    await store.update_user(1, muted_projects=frozenset({"g/noisy"}))
    await store.put_watched(watched(7, account_id=acc.id))
    noisy = watched(8, account_id=acc.id)
    noisy = dc_replace(noisy, item=dc_replace(noisy.item, project="g/noisy"))
    await store.put_watched(noisy)
    ctx = make_ctx(store)
    upd = message_update("/mr", lang="en")
    await views.cmd_mr(upd, ctx)
    text = upd.effective_chat.send_message.await_args.args[0]
    assert "!7" in text and "!8" not in text


def test_mr_line_shows_threads_waiting_for_my_resolve():
    w = watched(9, snapshot={"awaiting_resolve": 2})
    assert "🧵 2" in views.mr_line(w, "en", NOW)
