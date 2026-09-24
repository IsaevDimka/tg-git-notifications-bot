"""/start wizard: choose a host → send a token (deleted at once) → choose a time zone."""

import html
import re
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx
from telegram import ForceReply
from telegram.constants import ParseMode

from app import timeutil
from app.bot import access, deps, inputs
from app.bot.keyboards import connect_keyboard, tz_keyboard
from app.core.render import NO_PREVIEW, esc
from app.i18n import pick_lang, t
from app.models import Role
from app.providers.base import WRITE_SCOPES, AuthError, ProviderError
from app.storage.store import User

_HOST = re.compile(r"[a-z0-9-]+(?:\.[a-z0-9-]+)+(?::\d{1,5})?")


def normalize_host(text: str) -> str | None:
    host = re.sub(r"^[a-z]+://", "", text.strip().lower()).split("/")[0]
    return host if _HOST.fullmatch(host) else None


def token_url(kind: str, host: str) -> str:
    if kind == "github":
        return "https://github.com/settings/tokens/new?description=tg-git-notifications-bot&scopes=repo"
    return f"https://{host}/-/user_settings/personal_access_tokens?name=tg-git-notifications-bot&scopes=api"


async def cmd_start(update, ctx) -> None:
    chat = update.effective_chat
    if chat.type != "private":
        lang = pick_lang(update.effective_user.language_code)
        await update.effective_message.reply_text(t(lang, "start.private_only"))
        return
    user, created = await access.register(update, ctx)
    if user.status == "pending":
        if created:
            await access.notify_admins(ctx.bot, deps.store(ctx), user)
        await chat.send_message(t(user.lang, "access.pending"))
        return
    if user.status != "active":
        return
    await chat.send_message(
        t(user.lang, "start.hello"), parse_mode=ParseMode.HTML, reply_markup=connect_keyboard(user.lang)
    )


async def cmd_help(update, ctx) -> None:
    user = await access.current_user(update, ctx)
    if user is not None:
        await update.effective_chat.send_message(t(user.lang, "help.text"), parse_mode=ParseMode.HTML)


async def ask_token(chat, ctx, lang: str, kind: str, host: str) -> None:
    inputs.expect(ctx, "token", provider=kind, host=host)
    await chat.send_message(
        t(lang, f"ob.ask_token_{kind}", url=html.escape(token_url(kind, host))),
        parse_mode=ParseMode.HTML,
        link_preview_options=NO_PREVIEW,
        reply_markup=ForceReply(selective=True),
    )


async def ask_tz(chat, lang: str) -> None:
    await chat.send_message(t(lang, "ob.ask_tz"), reply_markup=tz_keyboard(lang))


async def _save_tz(chat, ctx, user: User, zone: str) -> None:
    try:
        ZoneInfo(zone)
    except (ZoneInfoNotFoundError, ValueError, OSError):
        await chat.send_message(t(user.lang, "ob.bad_tz"), parse_mode=ParseMode.HTML)
        return
    await deps.store(ctx).update_user(user.tg_id, tz=zone)
    await chat.send_message(t(user.lang, "ob.done", tz=esc(zone)), parse_mode=ParseMode.HTML)


async def cb_onboard(update, ctx) -> None:
    q = update.callback_query
    await q.answer()
    user = await access.current_user(update, ctx)
    if user is None:
        return
    chat, lang, data = update.effective_chat, user.lang, q.data
    if data == "ob:gl":
        await ask_token(chat, ctx, lang, "gitlab", "gitlab.com")
    elif data == "ob:gh":
        await ask_token(chat, ctx, lang, "github", "github.com")
    elif data == "ob:glh":
        inputs.expect(ctx, "host")
        await chat.send_message(t(lang, "ob.ask_host"), parse_mode=ParseMode.HTML, reply_markup=ForceReply(selective=True))
    elif data == "ob:tzo":
        inputs.expect(ctx, "tz")
        await chat.send_message(
            t(lang, "ob.ask_tz_text"), parse_mode=ParseMode.HTML, reply_markup=ForceReply(selective=True)
        )
    elif data.startswith("ob:tz:"):
        await _save_tz(chat, ctx, user, data.removeprefix("ob:tz:"))


async def on_host(update, ctx, user: User, pending: dict) -> None:
    host = normalize_host(update.message.text or "")
    if host is None:
        await update.effective_chat.send_message(
            t(user.lang, "ob.bad_host"), parse_mode=ParseMode.HTML, reply_markup=connect_keyboard(user.lang)
        )
        return
    await ask_token(update.effective_chat, ctx, user.lang, "gitlab", host)


async def on_token(update, ctx, user: User, pending: dict) -> None:
    token = (update.message.text or "").strip()
    deleted = await inputs.delete_quietly(update.message)  # first thing, whatever happens next
    chat, lang = update.effective_chat, user.lang
    kind, host = pending["provider"], pending["host"]
    provider = deps.provider(ctx, kind, host, token)
    try:
        ident = await provider.whoami()
    except AuthError:
        await chat.send_message(
            t(lang, "token.invalid", host=esc(host)), parse_mode=ParseMode.HTML, reply_markup=connect_keyboard(lang)
        )
        return
    except (ProviderError, httpx.HTTPError):
        await chat.send_message(
            t(lang, "token.unreachable", host=esc(host)), parse_mode=ParseMode.HTML,
            reply_markup=connect_keyboard(lang),
        )
        return
    st = deps.store(ctx)
    await st.add_account(user.tg_id, kind, host, ident.username, deps.box(ctx).seal(token), timeutil.now())
    try:
        count = sum(1 for i in await provider.list_items(ident.username) if i.role is Role.REVIEWER)
    except (ProviderError, httpx.HTTPError):
        count = 0
    text = t(lang, "token.ok", username=esc(ident.username), host=esc(host), count=count)
    scope = WRITE_SCOPES[kind]
    if ident.scopes is not None and scope not in ident.scopes:
        text += t(lang, "token.readonly", scope=scope)
    if not deleted:
        text += t(lang, "token.delete_failed")
    await chat.send_message(text, parse_mode=ParseMode.HTML)
    if not user.tz:
        await ask_tz(chat, lang)


async def on_tz(update, ctx, user: User, pending: dict) -> None:
    await _save_tz(update.effective_chat, ctx, user, (update.message.text or "").strip())


inputs.register("host", on_host)
inputs.register("token", on_token)
inputs.register("tz", on_tz)
