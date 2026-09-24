import pytest
from telegram.error import BadRequest

from app.bot import access, inputs, onboarding
from app.models import Role
from app.providers.base import AuthError, Identity
from tests.factories import item
from tests.fakes import FakeProvider
from tests.tg import bot_texts, callback_update, callbacks, make_cfg, make_ctx, message_update, texts


async def start(ctx, user_id=1, **kw):
    upd = message_update("/start", user_id=user_id, **kw)
    await onboarding.cmd_start(upd, ctx)
    return upd


@pytest.mark.parametrize(
    ("raw", "host"),
    [
        ("gitlab.company.com", "gitlab.company.com"),
        ("https://GitLab.Company.com/group/", "gitlab.company.com"),
        ("http://git.lan:8443/", "git.lan:8443"),
        ("not a host", None),
        ("localhost", None),
        ("", None),
    ],
)
def test_normalize_host(raw, host):
    assert onboarding.normalize_host(raw) == host


def test_looks_like_token():
    assert inputs.looks_like_token("glpat-abcdefghijklmnop")
    assert inputs.looks_like_token("ghp_" + "a" * 36)
    assert inputs.looks_like_token("github_pat_" + "a" * 40)
    assert inputs.looks_like_token("  abcdefghij0123456789  ")
    assert not inputs.looks_like_token("thanks, will fix tomorrow")
    assert not inputs.looks_like_token("ok")


async def test_first_start_makes_admin_and_offers_connect(store):
    ctx = make_ctx(store)
    upd = await start(ctx)
    user = await store.get_user(1)
    assert user.is_admin and user.status == "active" and user.lang == "ru"
    assert callbacks(upd.effective_chat.send_message.await_args.kwargs["reply_markup"]) == ["ob:gl", "ob:glh", "ob:gh"]


async def test_second_user_waits_for_admin(store):
    ctx = make_ctx(store)
    await start(ctx, 1)
    upd = await start(ctx, 2, username="bob", lang="en")
    assert (await store.get_user(2)).status == "pending"
    assert "admin" in texts(upd.effective_chat.send_message)[0]
    admin_call = ctx.bot.send_message.await_args
    assert admin_call.args[0] == 1
    assert callbacks(admin_call.kwargs["reply_markup"]) == ["adm:ok:2", "adm:no:2"]


async def test_allowed_users_skip_approval(store):
    ctx = make_ctx(store, cfg=make_cfg(allowed_users=frozenset({2})))
    await start(ctx, 1)
    await start(ctx, 2, username="bob")
    assert (await store.get_user(2)).status == "active"
    assert ctx.bot.send_message.await_count == 0


async def test_start_in_group_is_refused(store):
    ctx = make_ctx(store)
    upd = message_update("/start", chat_type="group")
    await onboarding.cmd_start(upd, ctx)
    assert await store.get_user(1) is None
    upd.message.reply_text.assert_awaited_once()


async def test_admin_admits_user(store):
    ctx = make_ctx(store)
    await start(ctx, 1)
    await start(ctx, 2, username="bob")
    await access.cb_admin(callback_update("adm:ok:2", user_id=1), ctx)
    assert (await store.get_user(2)).status == "active"
    chat_id, _ = bot_texts(ctx.bot)[-1]
    assert chat_id == 2
    assert callbacks(ctx.bot.send_message.await_args.kwargs["reply_markup"]) == ["ob:gl", "ob:glh", "ob:gh"]


async def test_non_admin_cannot_admit(store):
    ctx = make_ctx(store)
    await start(ctx, 1)
    await start(ctx, 2, username="bob")
    await start(ctx, 3, username="eve")
    await access.cb_admin(callback_update("adm:ok:2", user_id=3), ctx)
    assert (await store.get_user(2)).status == "pending"


async def test_blocked_user_start_reactivates(store):
    ctx = make_ctx(store)
    await start(ctx, 1)
    await store.update_user(1, status="blocked")
    await start(ctx, 1)
    assert (await store.get_user(1)).status == "active"


async def test_custom_gitlab_host_flow(store):
    ctx = make_ctx(store)
    await start(ctx)
    await onboarding.cb_onboard(callback_update("ob:glh"), ctx)
    assert ctx.user_data["await"] == {"kind": "host"}
    upd = message_update("https://GitLab.Company.com/group/")
    await inputs.on_text(upd, ctx)
    assert ctx.user_data["await"] == {"kind": "token", "provider": "gitlab", "host": "gitlab.company.com"}
    assert "https://gitlab.company.com/-/user_settings/personal_access_tokens" in texts(upd.effective_chat.send_message)[-1]


async def test_bad_host_is_rejected(store):
    ctx = make_ctx(store)
    await start(ctx)
    inputs.expect(ctx, "host")
    upd = message_update("my gitlab")
    await inputs.on_text(upd, ctx)
    assert "await" not in ctx.user_data
    assert "gitlab.company.com" in texts(upd.effective_chat.send_message)[-1]


async def test_token_is_deleted_verified_and_stored_encrypted(store):
    provider = FakeProvider()
    provider.items = [item(Role.REVIEWER, 1), item(Role.REVIEWER, 2), item(Role.AUTHOR, 3, author="me")]
    ctx = make_ctx(store, provider)
    await start(ctx)
    inputs.expect(ctx, "token", provider="gitlab", host="gitlab.example.com")
    upd = message_update("glpat-secretsecret123")
    await inputs.on_text(upd, ctx)
    upd.message.delete.assert_awaited_once()
    [acc] = await store.accounts_for(1)
    assert acc.username == "me" and acc.host == "gitlab.example.com"
    assert "glpat" not in acc.token_enc
    assert ctx.bot_data["box"].open(acc.token_enc) == "glpat-secretsecret123"
    sent = texts(upd.effective_chat.send_message)
    assert "@me" in sent[0] and "<b>2</b>" in sent[0]
    assert all("glpat" not in s for s in sent)
    assert "ob:tz:Europe/Moscow" in callbacks(upd.effective_chat.send_message.await_args.kwargs["reply_markup"])


async def test_rejected_token_is_deleted_and_not_stored(store):
    provider = FakeProvider()
    provider.whoami_error = AuthError("401", status=401)
    ctx = make_ctx(store, provider)
    await start(ctx)
    inputs.expect(ctx, "token", provider="gitlab", host="gitlab.example.com")
    upd = message_update("glpat-wrongwrongwrong")
    await inputs.on_text(upd, ctx)
    upd.message.delete.assert_awaited_once()
    assert await store.accounts_for(1) == []
    assert "gitlab.example.com" in texts(upd.effective_chat.send_message)[0]


async def test_readonly_token_warns(store):
    provider = FakeProvider()
    provider.identity = Identity("me", frozenset({"read_api"}))
    ctx = make_ctx(store, provider)
    await start(ctx)
    await store.update_user(1, tz="UTC")
    inputs.expect(ctx, "token", provider="gitlab", host="gitlab.example.com")
    upd = message_update("glpat-readonlyreadonly")
    await inputs.on_text(upd, ctx)
    assert "<b>api</b>" in texts(upd.effective_chat.send_message)[0]


async def test_undeletable_token_message_warns(store):
    ctx = make_ctx(store, FakeProvider())
    await start(ctx)
    await store.update_user(1, tz="UTC")
    inputs.expect(ctx, "token", provider="gitlab", host="gitlab.example.com")
    upd = message_update("glpat-secretsecret123")
    upd.message.delete.side_effect = BadRequest("message can't be deleted")
    await inputs.on_text(upd, ctx)
    assert "удали его вручную" in texts(upd.effective_chat.send_message)[0]


async def test_token_pasted_out_of_flow_is_deleted(store):
    ctx = make_ctx(store, FakeProvider())
    await start(ctx)
    upd = message_update("ghp_" + "x" * 36)
    await inputs.on_text(upd, ctx)
    upd.message.delete.assert_awaited_once()
    assert await store.accounts_for(1) == []
    assert "токен" in texts(upd.effective_chat.send_message)[0]


async def test_timezone_button_and_free_text(store):
    ctx = make_ctx(store)
    await start(ctx)
    await onboarding.cb_onboard(callback_update("ob:tz:Europe/Berlin"), ctx)
    assert (await store.get_user(1)).tz == "Europe/Berlin"
    await onboarding.cb_onboard(callback_update("ob:tzo"), ctx)
    await inputs.on_text(message_update("Asia/Yekaterinburg"), ctx)
    assert (await store.get_user(1)).tz == "Asia/Yekaterinburg"
    inputs.expect(ctx, "tz")
    upd = message_update("Mars/Olympus")
    await inputs.on_text(upd, ctx)
    assert (await store.get_user(1)).tz == "Asia/Yekaterinburg"
    assert "Europe/Moscow" in texts(upd.effective_chat.send_message)[-1]


async def test_unknown_text_gets_hint(store):
    ctx = make_ctx(store)
    await start(ctx)
    upd = message_update("hello?")
    await inputs.on_text(upd, ctx)
    assert "/help" in texts(upd.message.reply_text)[0]


async def test_unregistered_user_is_told_to_start(store):
    ctx = make_ctx(store)
    upd = message_update("hello?")
    await inputs.on_text(upd, ctx)
    assert "/start" in texts(upd.effective_chat.send_message)[0]
