"""Buttons under notifications: reply, resolve, approve, ping, snooze, mark as read."""

from datetime import UTC, datetime, timedelta

import httpx
from telegram import ForceReply, InlineKeyboardMarkup

from app import timeutil
from app.bot import access, deps, inputs
from app.bot.keyboards import btn
from app.core.quiet import user_zone
from app.core.render import keyboard
from app.i18n import t
from app.providers.base import WRITE_SCOPES, AuthError, ProviderError
from app.storage.store import User

ACTION_ERRORS = (ProviderError, httpx.HTTPError)


def parse(data: str) -> tuple[str, int, str]:
    parts = data.split(":")
    return parts[1], int(parts[2]), (parts[3] if len(parts) > 3 else "")


def snooze_until(option: str, now: datetime, tz: str | None) -> datetime:
    if option == "2h":
        return now + timedelta(hours=2)
    local = now.astimezone(user_zone(tz)) + timedelta(days=1)
    return local.replace(hour=10, minute=0, second=0, microsecond=0).astimezone(UTC)


def error_text(lang: str, kind: str, exc: Exception) -> str:
    if isinstance(exc, AuthError):
        return t(lang, "act.auth")
    if isinstance(exc, ProviderError) and exc.status == 403:
        return t(lang, "act.forbidden", scope=WRITE_SCOPES[kind])
    return t(lang, "act.failed", error=str(exc)[:150] or type(exc).__name__)


def _confirm(lang: str, label_key: str, action: str, event_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[btn(t(lang, label_key), f"a:{action}:{event_id}"), btn(t(lang, "btn.cancel"), f"a:x:{event_id}")]]
    )


def _snooze_menu(lang: str, event_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                btn(t(lang, "btn.snooze_2h"), f"a:sz:{event_id}:2h"),
                btn(t(lang, "btn.snooze_tomorrow"), f"a:sz:{event_id}:tm"),
            ],
            [btn(t(lang, "btn.cancel"), f"a:x:{event_id}")],
        ]
    )


async def cb_action(update, ctx) -> None:
    q = update.callback_query
    user = await access.current_user(update, ctx)
    if user is None:
        await q.answer()
        return
    act, event_id, option = parse(q.data)
    st, lang, now = deps.store(ctx), user.lang, timeutil.now()
    stored = await st.get_event(event_id, user.tg_id)
    if stored is None:
        await q.answer(t(lang, "act.gone"), show_alert=True)
        return
    ev = stored.event

    if act == "read":
        await st.mark_read([event_id], now)
        await q.answer(t(lang, "act.read"))
        await q.edit_message_reply_markup(reply_markup=None)
        return
    if act == "x":
        await q.answer()
        await q.edit_message_reply_markup(reply_markup=keyboard(ev, event_id, lang))
        return
    if act == "snooze":
        await q.answer()
        await q.edit_message_reply_markup(reply_markup=_snooze_menu(lang, event_id))
        return
    if act == "sz":
        until = snooze_until(option, now, user.tz)
        await st.snooze(event_id, until)
        when = until.astimezone(user_zone(user.tz)).strftime("%d.%m %H:%M")
        await q.answer(t(lang, "act.snoozed", when=when))
        await q.edit_message_reply_markup(reply_markup=None)
        return
    if act == "approve":
        await q.answer()
        await q.edit_message_reply_markup(reply_markup=_confirm(lang, "btn.confirm_approve", "approve!", event_id))
        return
    if act == "ping":
        await q.answer()
        await q.edit_message_reply_markup(reply_markup=_confirm(lang, "btn.confirm_ping", "ping!", event_id))
        return

    account = await st.get_account(stored.account_id)
    if account is None or ev.item is None:
        await q.answer(t(lang, "act.gone"), show_alert=True)
        return
    if act == "reply":
        inputs.expect(ctx, "reply", event_id=event_id)
        await q.answer()
        await update.effective_chat.send_message(
            t(lang, "act.reply_prompt", ref=ev.item.ref), reply_markup=ForceReply(selective=True)
        )
        return

    provider = deps.provider_for(ctx, account)
    if act == "resolve":
        call, done = provider.resolve(ev.item, ev.thread_id), "act.resolved"
    elif act == "approve!":
        call, done = provider.approve(ev.item), "act.approved"
    elif act == "ping!":
        body = t(lang, "ping.body", mentions=" ".join(f"@{a}" for a in ev.actors))
        call, done = provider.comment(ev.item, body), "act.pinged"
    else:
        await q.answer()
        return
    try:
        await call
    except ACTION_ERRORS as e:
        await q.answer(error_text(lang, account.kind, e), show_alert=True)
        return
    await st.mark_read([event_id], now)
    await q.answer(t(lang, done))
    await q.edit_message_reply_markup(reply_markup=None)


async def on_reply(update, ctx, user: User, pending: dict) -> None:
    st, lang, chat = deps.store(ctx), user.lang, update.effective_chat
    stored = await st.get_event(pending["event_id"], user.tg_id)
    account = await st.get_account(stored.account_id) if stored else None
    if stored is None or account is None or stored.event.item is None:
        await chat.send_message(t(lang, "act.gone"))
        return
    ev = stored.event
    try:
        await deps.provider_for(ctx, account).reply(ev.item, ev.thread_id, update.message.text or "")
    except ACTION_ERRORS as e:
        await chat.send_message(error_text(lang, account.kind, e))
        return
    await st.mark_read([stored.id], timeutil.now())
    await chat.send_message(t(lang, "act.replied", ref=ev.item.ref))


inputs.register("reply", on_reply)
