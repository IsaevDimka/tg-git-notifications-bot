"""/mr and /inbox — watched merge requests, coloured by whose move it is."""

import html
from datetime import datetime

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.error import BadRequest

from app import timeutil
from app.bot import access, deps
from app.bot.keyboards import btn
from app.core.render import NO_PREVIEW, clip, esc, fmt_age
from app.i18n import t
from app.models import Ball, Role
from app.storage.store import User, Watched

BALL_ICON = {Ball.ME: "🔴", Ball.THEM: "⚪", Ball.NONE: "✅"}
CI_LABEL = {"success": "CI ✓", "failed": "CI ✗", "running": "CI …"}
LIST_MAX = 15
INBOX_BUTTONS = 12


def _ordered(ws: list[Watched]) -> list[Watched]:
    return sorted(ws, key=lambda w: (w.ball is not Ball.ME, -w.item.updated_at.timestamp()))


def mr_line(w: Watched, lang: str, now: datetime, unread: int = 0) -> str:
    it, snap = w.item, w.snapshot
    meta = [f"<code>{esc(it.project_short)}</code>", fmt_age(now - it.created_at, lang)]
    approved, left = len(snap.get("approved_by", ())), snap.get("approvals_left")
    if left:
        meta.append(f"👍 {approved}/{approved + left}")
    elif approved:
        meta.append(f"👍 {approved}")
    if snap.get("open_threads"):
        meta.append(f"💬 {snap['open_threads']}")
    if ci := CI_LABEL.get(snap.get("pipeline") or ""):
        meta.append(ci)
    if unread:
        meta.append(t(lang, "inbox.unread", count=unread))
    draft = "📝 " if it.draft else ""
    link = f'<a href="{html.escape(it.url)}">{it.ref}</a> {esc(clip(it.title, 60))}'
    return f"{BALL_ICON[w.ball]} {draft}{link}\n      " + " · ".join(meta)


def render_mr_list(ws: list[Watched], tab: str, lang: str, now: datetime) -> str:
    role = Role.REVIEWER if tab == "rev" else Role.AUTHOR
    rows = _ordered([w for w in ws if w.role is role])
    if not rows:
        return t(lang, "mr.empty_review" if tab == "rev" else "mr.empty_own")
    title = t(lang, "mr.tab_review" if tab == "rev" else "mr.tab_own")
    lines = [f"<b>{title}</b> ({len(rows)})", t(lang, "mr.legend"), ""]
    lines += [mr_line(w, lang, now) for w in rows[:LIST_MAX]]
    if len(rows) > LIST_MAX:
        lines.append(t(lang, "mr.more", count=len(rows) - LIST_MAX))
    return "\n".join(lines)


def mr_tabs(tab: str, lang: str) -> InlineKeyboardMarkup:
    def label(key: str, name: str) -> str:
        return ("• " if tab == name else "") + t(lang, key)

    return InlineKeyboardMarkup([[btn(label("mr.tab_review", "rev"), "mr:rev"), btn(label("mr.tab_own", "own"), "mr:own")]])


def render_inbox(
    ws: list[Watched], counts: dict[str, int], mention_count: int, lang: str, now: datetime
) -> tuple[str, list[Watched], bool]:
    mine = _ordered([w for w in ws if w.ball is Ball.ME])
    if not mine and not mention_count:
        return t(lang, "inbox.empty"), [], True
    lines = [t(lang, "inbox.header", count=len(mine)), ""]
    lines += [mr_line(w, lang, now, counts.get(w.key, 0)) for w in mine[:LIST_MAX]]
    if len(mine) > LIST_MAX:
        lines.append(t(lang, "mr.more", count=len(mine) - LIST_MAX))
    if mention_count:
        lines += ["", t(lang, "inbox.mentions", count=mention_count)]
    return "\n".join(lines), mine[:INBOX_BUTTONS], False


def inbox_keyboard(shown: list[Watched], lang: str) -> InlineKeyboardMarkup:
    buttons = [InlineKeyboardButton(f"✓ {w.item.ref}", callback_data=f"ib:r:{i}") for i, w in enumerate(shown)]
    rows = [buttons[i : i + 3] for i in range(0, len(buttons), 3)]
    rows.append([btn(t(lang, "btn.read_all"), "ib:all")])
    return InlineKeyboardMarkup(rows)


async def safe_edit(q, text: str, markup) -> None:
    try:
        await q.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=markup, link_preview_options=NO_PREVIEW)
    except BadRequest:  # "message is not modified" and friends
        pass


async def _mr_view(ctx, user: User, tab: str):
    st = deps.store(ctx)
    if not await st.accounts_for(user.tg_id):
        return t(user.lang, "mr.no_accounts"), None
    ws = await st.watched_for_user(user.tg_id)
    return render_mr_list(ws, tab, user.lang, timeutil.now()), mr_tabs(tab, user.lang)


async def cmd_mr(update, ctx) -> None:
    user = await access.current_user(update, ctx)
    if user is None:
        return
    text, markup = await _mr_view(ctx, user, "rev")
    await update.effective_chat.send_message(
        text, parse_mode=ParseMode.HTML, reply_markup=markup, link_preview_options=NO_PREVIEW
    )


async def cb_mr(update, ctx) -> None:
    q = update.callback_query
    await q.answer()
    user = await access.current_user(update, ctx)
    if user is None:
        return
    text, markup = await _mr_view(ctx, user, "own" if q.data == "mr:own" else "rev")
    await safe_edit(q, text, markup)


async def _inbox_view(ctx, user: User):
    st = deps.store(ctx)
    text, shown, empty = render_inbox(
        await st.watched_for_user(user.tg_id),
        await st.unread_counts(user.tg_id),
        await st.unread_mention_count(user.tg_id),
        user.lang,
        timeutil.now(),
    )
    ctx.user_data["inbox_keys"] = [w.key for w in shown]
    return text, None if empty else inbox_keyboard(shown, user.lang)


async def cmd_inbox(update, ctx) -> None:
    user = await access.current_user(update, ctx)
    if user is None:
        return
    text, markup = await _inbox_view(ctx, user)
    await update.effective_chat.send_message(
        text, parse_mode=ParseMode.HTML, reply_markup=markup, link_preview_options=NO_PREVIEW
    )


async def cb_inbox(update, ctx) -> None:
    q = update.callback_query
    user = await access.current_user(update, ctx)
    if user is None:
        await q.answer()
        return
    st, now = deps.store(ctx), timeutil.now()
    if q.data == "ib:all":
        await st.mark_all_read(user.tg_id, now)
        await q.answer(t(user.lang, "inbox.done"))
    else:
        keys = ctx.user_data.get("inbox_keys", [])
        index = int(q.data.removeprefix("ib:r:"))
        if index < len(keys):
            await st.mark_item_read(user.tg_id, keys[index], now)
        await q.answer(t(user.lang, "act.read"))
    text, markup = await _inbox_view(ctx, user)
    await safe_edit(q, text, markup)
