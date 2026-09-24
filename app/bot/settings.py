"""/settings (notification types, polling, quiet hours, language, time zone) and /accounts."""

from datetime import datetime

from telegram import BotCommandScopeChat, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.error import BadRequest

from app import timeutil
from app.bot import access, deps, onboarding
from app.bot.commands import bot_commands
from app.bot.keyboards import btn, connect_keyboard
from app.core.render import clip, esc, fmt_age
from app.i18n import t
from app.models import Kind
from app.storage.store import Account, User
from app.timeutil import parse_ts

INTERVALS_MIN = (1, 3, 5, 10, 15)
QUIET_PRESETS = (("22:00", "09:00"), ("23:00", "08:00"), ("20:00", "10:00"), ("00:00", "07:00"))
DIGEST_TIMES = ("09:00", "10:00", "11:00", "12:00")
LANGS = ("ru", "en")


def interval_options(min_seconds: int) -> list[int]:
    return [m for m in INTERVALS_MIN if m * 60 >= min_seconds] or [-(-min_seconds // 60)]


def apply(user: User, action: str, min_poll: int) -> dict:
    if action.startswith("k:"):
        kind = str(Kind(action.removeprefix("k:")))
        return {"muted_kinds": user.muted_kinds ^ {kind}}
    if action == "iv":
        options = interval_options(min_poll)
        current = user.poll_interval // 60
        return {"poll_interval": next((m for m in options if m > current), options[0]) * 60}
    if action == "q":
        return {"quiet_enabled": not user.quiet_enabled}
    if action == "qp":
        current = (user.quiet_from, user.quiet_to)
        idx = QUIET_PRESETS.index(current) if current in QUIET_PRESETS else -1
        start, end = QUIET_PRESETS[(idx + 1) % len(QUIET_PRESETS)]
        return {"quiet_from": start, "quiet_to": end, "quiet_enabled": True}
    if action == "qw":
        return {"quiet_weekends": not user.quiet_weekends}
    if action == "lang":
        return {"lang": "en" if user.lang == "ru" else "ru"}
    if action.startswith("lg:") and action.removeprefix("lg:") in LANGS:
        return {"lang": action.removeprefix("lg:")}
    if action == "mb":
        return {"mute_bots": not user.mute_bots}
    if action == "md":
        return {"mute_drafts": not user.mute_drafts}
    if action == "dg":
        return {"digest_enabled": not user.digest_enabled}
    if action == "dgt":
        idx = DIGEST_TIMES.index(user.digest_time) if user.digest_time in DIGEST_TIMES else -1
        return {"digest_time": DIGEST_TIMES[(idx + 1) % len(DIGEST_TIMES)], "digest_enabled": True}
    raise ValueError(f"unknown settings action {action!r}")


def settings_view(user: User) -> tuple[str, InlineKeyboardMarkup]:
    lang = user.lang
    toggles = [
        btn(("🔕 " if kind in user.muted_kinds else "✅ ") + t(lang, f"kind.{kind}"), f"st:k:{kind}") for kind in Kind
    ]
    rows = [toggles[i : i + 2] for i in range(0, len(toggles), 2)]
    rows.append([btn(t(lang, "st.interval", n=user.poll_interval // 60), "st:iv")])
    digest = t(lang, "st.digest_on", time=user.digest_time) if user.digest_enabled else t(lang, "st.digest_off")
    rows.append([btn(digest, "st:dg"), btn(t(lang, "st.digest_time"), "st:dgt")])
    quiet = (
        t(lang, "st.quiet_on", start=user.quiet_from, end=user.quiet_to)
        if user.quiet_enabled
        else t(lang, "st.quiet_off")
    )
    rows.append([btn(quiet, "st:q"), btn(t(lang, "st.quiet_period"), "st:qp")])
    rows.append([btn(t(lang, "st.weekends_on" if user.quiet_weekends else "st.weekends_off"), "st:qw")])
    rows.append([
        btn(t(lang, "st.bots_muted" if user.mute_bots else "st.bots_shown"), "st:mb"),
        btn(t(lang, "st.drafts_muted" if user.mute_drafts else "st.drafts_shown"), "st:md"),
    ])
    rows.append([btn(t(lang, "st.lang"), "st:lang"), btn(t(lang, "st.tz", tz=user.tz or "UTC"), "st:tz")])
    rows.append([btn(t(lang, "st.accounts"), "st:acc")])
    return t(lang, "st.header"), InlineKeyboardMarkup(rows)


def _account_line(a: Account, lang: str, now: datetime) -> str:
    host, username = esc(a.host), esc(a.username)
    if a.last_error == "auth":
        return t(lang, "acc.line_err", host=host, username=username, error=t(lang, "acc.err_auth"))
    if a.last_error:
        return t(lang, "acc.line_err", host=host, username=username, error=esc(clip(a.last_error, 80)))
    if a.last_ok_at:
        ago = fmt_age(now - parse_ts(a.last_ok_at), lang)
        return t(lang, "acc.line_ok", host=host, username=username, ago=ago)
    return t(lang, "acc.line_new", host=host, username=username)


def accounts_view(accounts: list[Account], lang: str, now: datetime) -> tuple[str, InlineKeyboardMarkup]:
    body = [_account_line(a, lang, now) for a in accounts] or [t(lang, "acc.empty")]
    rows = [[btn(t(lang, "btn.remove", host=a.host), f"acc:rm:{a.id}")] for a in accounts]
    rows.append([btn(t(lang, "btn.add_account"), "acc:add")])
    return "\n".join([t(lang, "acc.header"), "", *body]), InlineKeyboardMarkup(rows)


async def _edit(q, text: str, markup) -> None:
    try:
        await q.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=markup)
    except BadRequest:
        pass


async def cmd_settings(update, ctx) -> None:
    user = await access.current_user(update, ctx)
    if user is None:
        return
    text, markup = settings_view(user)
    await update.effective_chat.send_message(text, parse_mode=ParseMode.HTML, reply_markup=markup)


async def _send_accounts(update, ctx, user: User) -> None:
    text, markup = accounts_view(await deps.store(ctx).accounts_for(user.tg_id), user.lang, timeutil.now())
    await update.effective_chat.send_message(text, parse_mode=ParseMode.HTML, reply_markup=markup)


async def cb_settings(update, ctx) -> None:
    q = update.callback_query
    await q.answer()
    user = await access.current_user(update, ctx)
    if user is None:
        return
    action = q.data.removeprefix("st:")
    if action == "tz":
        await onboarding.ask_tz(update.effective_chat, user.lang)
        return
    if action == "acc":
        await _send_accounts(update, ctx, user)
        return
    if action.startswith("mp:"):
        projects = ctx.user_data.get("mute_projects", [])
        index = int(action.removeprefix("mp:"))
        if index < len(projects):
            user = await _toggle_project(ctx, user, projects[index])
        text, markup = _mute_view(user, projects)
        await _edit(q, text, markup)
        return
    st = deps.store(ctx)
    await st.update_user(user.tg_id, **apply(user, action, deps.cfg(ctx).min_poll_interval))
    fresh = await st.get_user(user.tg_id)
    if fresh.lang != user.lang:  # make the command menu in this chat follow the chosen language
        await ctx.bot.set_my_commands(bot_commands(fresh.lang), scope=BotCommandScopeChat(fresh.chat_id))
    if action.startswith("lg:"):
        await _edit(q, t(fresh.lang, "lang.set"), None)
        return
    text, markup = settings_view(fresh)
    await _edit(q, text, markup)


async def cmd_lang(update, ctx) -> None:
    user = await access.current_user(update, ctx)
    if user is None:
        return
    markup = InlineKeyboardMarkup([[btn("🇷🇺 Русский", "st:lg:ru"), btn("🇬🇧 English", "st:lg:en")]])
    await update.effective_chat.send_message(t(user.lang, "lang.choose"), reply_markup=markup)


async def cmd_accounts(update, ctx) -> None:
    user = await access.current_user(update, ctx)
    if user is not None:
        await _send_accounts(update, ctx, user)


async def cb_accounts(update, ctx) -> None:
    q = update.callback_query
    user = await access.current_user(update, ctx)
    if user is None:
        await q.answer()
        return
    st, lang = deps.store(ctx), user.lang
    parts = q.data.split(":")
    action = parts[1]
    if action == "add":
        await q.answer()
        await update.effective_chat.send_message(
            t(lang, "start.hello"), parse_mode=ParseMode.HTML, reply_markup=connect_keyboard(lang)
        )
        return
    if action == "rm":
        await q.answer()
        acc = await st.get_account(int(parts[2]))
        if acc is None or acc.tg_id != user.tg_id:
            return
        markup = InlineKeyboardMarkup(
            [[btn(t(lang, "btn.confirm_rm"), f"acc:rm!:{acc.id}"), btn(t(lang, "btn.cancel"), "acc:list")]]
        )
        await _edit(q, t(lang, "acc.confirm_rm", host=esc(acc.host)), markup)
        return
    if action == "rm!":
        removed = await st.delete_account(int(parts[2]), user.tg_id)
        await q.answer(t(lang, "acc.removed") if removed else None)
    else:
        await q.answer()
    text, markup = accounts_view(await st.accounts_for(user.tg_id), lang, timeutil.now())
    await _edit(q, text, markup)


MUTE_BUTTONS = 30


async def _toggle_project(ctx, user: User, project: str) -> User:
    st = deps.store(ctx)
    await st.update_user(user.tg_id, muted_projects=user.muted_projects ^ {project})
    return await st.get_user(user.tg_id)


def _mute_view(user: User, projects: list[str]) -> tuple[str, InlineKeyboardMarkup | None]:
    if not projects:
        return t(user.lang, "mute.empty"), None
    buttons = [
        btn(("🔕 " if p in user.muted_projects else "🔔 ") + p, f"st:mp:{i}") for i, p in enumerate(projects[:MUTE_BUTTONS])
    ]
    return t(user.lang, "mute.header"), InlineKeyboardMarkup([[b] for b in buttons])


async def cmd_mute(update, ctx) -> None:
    """/mute — pick projects to silence; /mute group/project — toggle one directly."""
    user = await access.current_user(update, ctx)
    if user is None:
        return
    args = list(getattr(ctx, "args", None) or [])
    if args:
        project = args[0].strip()
        user = await _toggle_project(ctx, user, project)
        key = "mute.done" if project in user.muted_projects else "mute.undone"
        await update.effective_chat.send_message(t(user.lang, key, project=esc(project)), parse_mode=ParseMode.HTML)
        return
    watched = await deps.store(ctx).watched_for_user(user.tg_id)
    projects = sorted({w.item.project for w in watched} | set(user.muted_projects))
    ctx.user_data["mute_projects"] = projects
    text, markup = _mute_view(user, projects)
    await update.effective_chat.send_message(text, parse_mode=ParseMode.HTML, reply_markup=markup)
