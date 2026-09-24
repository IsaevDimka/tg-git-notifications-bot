from dataclasses import replace
from datetime import timedelta

import pytest

from app.bot import settings
from app.models import Kind
from app.storage.store import Account
from app.timeutil import iso
from tests.factories import NOW
from tests.tg import callback_update, callbacks, make_ctx, message_update, texts


async def make_user(store):
    user, _ = await store.register_user(1, 1, "me", "en", False, 180, NOW)
    return user


def account(**kw) -> Account:
    base = dict(id=1, tg_id=1, kind="gitlab", host="gitlab.example.com", username="me", token_enc="s", synced=True,
                last_poll_at=None, last_ok_at=None, last_error=None, mentions_cursor=None)
    return Account(**{**base, **kw})


async def test_apply_toggles_and_cycles(store):
    user = await make_user(store)
    muted = settings.apply(user, "k:new_comment", 60)["muted_kinds"]
    assert muted == frozenset({"new_comment"})
    assert settings.apply(replace(user, muted_kinds=muted), "k:new_comment", 60)["muted_kinds"] == frozenset()
    assert settings.apply(user, "iv", 60) == {"poll_interval": 300}
    assert settings.apply(replace(user, poll_interval=900), "iv", 60) == {"poll_interval": 60}
    assert settings.apply(replace(user, poll_interval=180), "iv", 300) == {"poll_interval": 300}
    assert settings.apply(user, "q", 60) == {"quiet_enabled": False}
    assert settings.apply(user, "qp", 60) == {"quiet_from": "23:00", "quiet_to": "08:00", "quiet_enabled": True}
    odd = replace(user, quiet_from="21:30", quiet_to="07:15")
    assert settings.apply(odd, "qp", 60)["quiet_from"] == "22:00"
    assert settings.apply(user, "qw", 60) == {"quiet_weekends": False}
    assert settings.apply(user, "lang", 60) == {"lang": "ru"}
    with pytest.raises(ValueError):
        settings.apply(user, "k:nonsense", 60)
    with pytest.raises(ValueError):
        settings.apply(user, "reboot", 60)


def test_interval_options_respect_minimum():
    assert settings.interval_options(60) == [1, 3, 5, 10, 15]
    assert settings.interval_options(300) == [5, 10, 15]
    assert settings.interval_options(3600) == [60]


async def test_settings_view_has_every_kind_and_controls(store):
    user = await make_user(store)
    text, markup = settings.settings_view(replace(user, muted_kinds=frozenset({"mention"})))
    data = callbacks(markup)
    for kind in Kind:
        assert f"st:k:{kind}" in data
    for control in ("st:iv", "st:q", "st:qp", "st:qw", "st:lang", "st:tz", "st:acc"):
        assert control in data
    labels = [b.text for row in markup.inline_keyboard for b in row]
    assert any(label.startswith("🔕") and "Mentions" in label for label in labels)
    assert "⏱ Check every 3 min" in labels


async def test_cb_settings_persists_and_rerenders(store):
    await make_user(store)
    ctx = make_ctx(store)
    upd = callback_update("st:k:new_comment", lang="en")
    await settings.cb_settings(upd, ctx)
    assert (await store.get_user(1)).muted_kinds == frozenset({"new_comment"})
    upd.callback_query.edit_message_text.assert_awaited_once()


async def test_cb_settings_tz_opens_picker(store):
    await make_user(store)
    ctx = make_ctx(store)
    upd = callback_update("st:tz", lang="en")
    await settings.cb_settings(upd, ctx)
    assert "ob:tz:UTC" in callbacks(upd.effective_chat.send_message.await_args.kwargs["reply_markup"])


def test_accounts_view_lines():
    accounts = [
        account(id=1, last_ok_at=iso(NOW - timedelta(minutes=2))),
        account(id=2, host="github.com", kind="github"),
        account(id=3, host="git.corp", last_error="auth"),
        account(id=4, host="git.other", last_error="github.com: HTTP 502 on /graphql"),
    ]
    text, markup = settings.accounts_view(accounts, "en", NOW)
    assert "checked 2 min ago" in text
    assert "first check soon" in text
    assert "token stopped working" in text
    assert "HTTP 502" in text
    assert callbacks(markup) == ["acc:rm:1", "acc:rm:2", "acc:rm:3", "acc:rm:4", "acc:add"]


async def test_remove_account_with_confirmation(store):
    await make_user(store)
    acc = await store.add_account(1, "gitlab", "gitlab.example.com", "me", "s", NOW)
    ctx = make_ctx(store)
    upd = callback_update(f"acc:rm:{acc.id}", lang="en")
    await settings.cb_accounts(upd, ctx)
    markup = upd.callback_query.edit_message_text.await_args.kwargs["reply_markup"]
    assert callbacks(markup) == [f"acc:rm!:{acc.id}", "acc:list"]
    assert await store.accounts_for(1) != []
    await settings.cb_accounts(callback_update(f"acc:rm!:{acc.id}", lang="en"), ctx)
    assert await store.accounts_for(1) == []


async def test_cannot_remove_someone_elses_account(store):
    await make_user(store)
    await store.register_user(2, 2, "eve", "en", True, 180, NOW)
    acc = await store.add_account(1, "gitlab", "gitlab.example.com", "me", "s", NOW)
    ctx = make_ctx(store)
    await settings.cb_accounts(callback_update(f"acc:rm!:{acc.id}", user_id=2, lang="en"), ctx)
    assert len(await store.accounts_for(1)) == 1


async def test_cmd_accounts_lists(store):
    await make_user(store)
    await store.add_account(1, "github", "github.com", "me", "s", NOW)
    ctx = make_ctx(store)
    upd = message_update("/accounts", lang="en")
    await settings.cmd_accounts(upd, ctx)
    assert "github.com" in texts(upd.effective_chat.send_message)[0]


async def test_apply_digest_and_language(store):
    user = await make_user(store)
    assert settings.apply(user, "dg", 60) == {"digest_enabled": False}
    assert settings.apply(user, "dgt", 60) == {"digest_time": "11:00", "digest_enabled": True}
    assert settings.apply(replace(user, digest_time="12:00"), "dgt", 60)["digest_time"] == "09:00"
    assert settings.apply(user, "lg:ru", 60) == {"lang": "ru"}
    with pytest.raises(ValueError):
        settings.apply(user, "lg:de", 60)


async def test_settings_view_has_digest_controls(store):
    user = await make_user(store)
    _, markup = settings.settings_view(user)
    assert "st:dg" in callbacks(markup) and "st:dgt" in callbacks(markup)
    labels = [b.text for row in markup.inline_keyboard for b in row]
    assert "☀️ Daily summary: 10:00" in labels


async def test_cmd_lang_offers_both_languages(store):
    await make_user(store)
    ctx = make_ctx(store)
    upd = message_update("/lang", lang="en")
    await settings.cmd_lang(upd, ctx)
    assert callbacks(upd.effective_chat.send_message.await_args.kwargs["reply_markup"]) == ["st:lg:ru", "st:lg:en"]


async def test_choosing_language_switches_texts_and_menu(store):
    await make_user(store)
    ctx = make_ctx(store)
    upd = callback_update("st:lg:ru", lang="en")
    await settings.cb_settings(upd, ctx)
    assert (await store.get_user(1)).lang == "ru"
    assert "Русский" in upd.callback_query.edit_message_text.await_args.args[0]
    call = ctx.bot.set_my_commands.await_args
    assert call.kwargs["scope"].chat_id == 1
    assert any(c.command == "my" for c in call.args[0])


async def test_apply_noise_toggles(store):
    user = await make_user(store)
    assert settings.apply(user, "mb", 60) == {"mute_bots": False}
    assert settings.apply(user, "md", 60) == {"mute_drafts": False}
    _, markup = settings.settings_view(user)
    assert "st:mb" in callbacks(markup) and "st:md" in callbacks(markup)


async def test_mute_lists_projects_and_toggles(store):
    from app.models import Ball, Role
    from app.storage.store import Watched
    from tests.factories import item

    await make_user(store)
    acc = await store.add_account(1, "gitlab", "gitlab.example.com", "me", "s", NOW)
    for iid, project in ((1, "g/app"), (2, "g/lib")):
        it = replace(item(Role.REVIEWER, iid), project=project)
        await store.put_watched(Watched(acc.id, it.key, Role.REVIEWER, it, it.updated_at, {}, Ball.ME, NOW))
    ctx = make_ctx(store)
    upd = message_update("/mute", lang="en")
    await settings.cmd_mute(upd, ctx)
    lib = settings.project_ref("g/lib")
    assert callbacks(upd.effective_chat.send_message.await_args.kwargs["reply_markup"]) == [
        f"st:mp:{settings.project_ref('g/app')}", f"st:mp:{lib}"]
    it = replace(item(Role.REVIEWER, 3), project="a/first")  # the list changes before the old button is tapped
    await store.put_watched(Watched(acc.id, it.key, Role.REVIEWER, it, it.updated_at, {}, Ball.ME, NOW))
    await settings.cb_settings(callback_update(f"st:mp:{lib}", lang="en"), ctx)
    assert (await store.get_user(1)).muted_projects == frozenset({"g/lib"})
    ctx.args = ["g/lib"]
    await settings.cmd_mute(message_update("/mute g/lib", lang="en"), ctx)
    assert (await store.get_user(1)).muted_projects == frozenset()


async def test_apply_evening_summary(store):
    user = await make_user(store)
    assert settings.apply(user, "ev", 60) == {"evening_enabled": True}
    assert settings.apply(user, "evt", 60) == {"evening_time": "19:00", "evening_enabled": True}
    _, markup = settings.settings_view(user)
    assert "st:ev" in callbacks(markup) and "st:evt" in callbacks(markup)
