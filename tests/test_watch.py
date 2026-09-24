import pytest

from app.bot import watch
from app.models import Role
from tests.factories import NOW, item
from tests.fakes import FakeProvider
from tests.tg import callback_update, callbacks, make_ctx, message_update, texts


@pytest.mark.parametrize(
    ("url", "ref"),
    [
        ("https://gitlab.company.com/g/sub/app/-/merge_requests/42", ("gitlab", "gitlab.company.com", "g/sub/app", 42)),
        ("https://gitlab.com/g/app/-/merge_requests/7/diffs#note_1", ("gitlab", "gitlab.com", "g/app", 7)),
        ("https://github.com/acme/app/pull/5/files", ("github", "github.com", "acme/app", 5)),
        ("https://example.com/whatever", None),
        ("not a url", None),
    ],
)
def test_parse_mr_url(url, ref):
    assert watch.parse_mr_url(url) == ref


async def _setup(store, provider):
    await store.register_user(1, 1, "me", "en", False, 180, NOW)
    ctx = make_ctx(store, provider)
    acc = await store.add_account(1, "gitlab", "gitlab.company.com", "me", ctx.bot_data["box"].seal("t"), NOW)
    return ctx, acc


async def test_watch_by_link(store):
    p = FakeProvider()
    p.by_ref[("g/app", 42)] = item(Role.REVIEWER, 42, author="bob", title="Speed up search")
    ctx, acc = await _setup(store, p)
    ctx.args = ["https://gitlab.company.com/g/app/-/merge_requests/42"]
    upd = message_update("/watch", lang="en")
    await watch.cmd_watch(upd, ctx)
    assert await store.watch_refs(acc.id) == [("g/app", 42)]
    assert "Speed up search" in texts(upd.effective_chat.send_message)[0]


async def test_watch_needs_a_connected_account_for_that_host(store):
    ctx, _ = await _setup(store, FakeProvider())
    ctx.args = ["https://github.com/acme/app/pull/5"]
    upd = message_update("/watch", lang="en")
    await watch.cmd_watch(upd, ctx)
    assert "github.com" in texts(upd.effective_chat.send_message)[0]


async def test_watch_unreachable_mr_is_not_saved(store):
    ctx, acc = await _setup(store, FakeProvider())
    ctx.args = ["https://gitlab.company.com/g/app/-/merge_requests/404"]
    await watch.cmd_watch(message_update("/watch", lang="en"), ctx)
    assert await store.watch_refs(acc.id) == []


async def test_list_and_unwatch(store):
    ctx, acc = await _setup(store, FakeProvider())
    await store.add_watch_ref(acc.id, "g/app", 42, NOW)
    upd = message_update("/watch", lang="en")
    await watch.cmd_watch(upd, ctx)
    ref = watch.ref_id(acc.id, "g/app", 42)
    assert callbacks(upd.effective_chat.send_message.await_args.kwargs["reply_markup"]) == [f"wt:rm:{ref}"]
    await store.add_watch_ref(acc.id, "a/first", 1, NOW)  # list changes before the old button is tapped
    await watch.cb_watch(callback_update(f"wt:rm:{ref}", lang="en"), ctx)
    assert await store.watch_refs(acc.id) == [("a/first", 1)]
