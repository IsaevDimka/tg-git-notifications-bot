from app.bot import onboarding
from tests.factories import NOW
from tests.tg import make_ctx, message_update, texts


async def test_invite_link_lets_a_colleague_skip_approval(store):
    ctx = make_ctx(store)
    ctx.bot.username = "LuckyGitlabBot"
    await onboarding.cmd_start(message_update("/start", user_id=1), ctx)  # admin
    upd = message_update("/invite", user_id=1, lang="en")
    await onboarding.cmd_invite(upd, ctx)
    text = texts(upd.effective_chat.send_message)[0]
    token = text.split("start=inv_")[1].split()[0].split("<")[0]
    assert "https://t.me/LuckyGitlabBot?start=inv_" in text

    ctx.args = [f"inv_{token}"]
    await onboarding.cmd_start(message_update("/start", user_id=2, username="bob"), ctx)
    assert (await store.get_user(2)).status == "active"
    assert ctx.bot.send_message.await_count == 0  # no approval request to the admin

    await onboarding.cmd_start(message_update("/start", user_id=3, username="eve"), ctx)  # same token reused
    assert (await store.get_user(3)).status == "pending"


async def test_invite_for_already_pending_user_admits_them(store):
    ctx = make_ctx(store)
    ctx.bot.username = "LuckyGitlabBot"
    await onboarding.cmd_start(message_update("/start", user_id=1), ctx)
    await onboarding.cmd_start(message_update("/start", user_id=2, username="bob"), ctx)
    assert (await store.get_user(2)).status == "pending"
    await store.create_invite("tok", 1, NOW)
    ctx.args = ["inv_tok"]
    await onboarding.cmd_start(message_update("/start", user_id=2, username="bob"), ctx)
    assert (await store.get_user(2)).status == "active"
