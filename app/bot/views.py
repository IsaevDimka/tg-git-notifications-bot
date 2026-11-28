"""/mr and /inbox — watched merge requests, coloured by whose move it is."""

import html
from collections.abc import Sequence
from datetime import datetime
from urllib.parse import urlencode

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.error import BadRequest

from app import timeutil
from app.bot import access, deps
from app.bot.keyboards import btn
from app.core.noise import visible
from app.core.render import NO_PREVIEW, clip, esc, fmt_age
from app.i18n import t
from app.models import Ball, Role
from app.storage.store import Account, User, Watched

BALL_ICON = {Ball.ME: "🔴", Ball.THEM: "⚪", Ball.NONE: "✅"}
CI_LABEL = {"success": "CI ✓", "failed": "CI ✗", "running": "CI …"}
MR_PAGE_SIZE = 10
LIST_MAX = 15
INBOX_BUTTONS = 12


def _ordered(ws: list[Watched]) -> list[Watched]:
    return sorted(ws, key=lambda w: (w.ball is not Ball.ME, -w.item.updated_at.timestamp()))


def mr_line(w: Watched, lang: str, now: datetime, unread: int = 0, *, show_host: bool = False) -> str:
    it, snap = w.item, w.snapshot
    project = f"{it.host}/{it.project}" if show_host else it.project
    meta = [f"<code>{esc(clip(project, 90))}</code>", fmt_age(now - it.created_at, lang)]
    approved, left = len(snap.get("approved_by", ())), snap.get("approvals_left")
    if left:
        meta.append(f"👍 {approved}/{approved + left}")
    elif approved:
        meta.append(f"👍 {approved}")
    if snap.get("open_threads"):
        meta.append(f"💬 {snap['open_threads']}")
    if snap.get("awaiting_resolve"):
        meta.append(f"🧵 {snap['awaiting_resolve']}")
    if ci := CI_LABEL.get(snap.get("pipeline") or ""):
        meta.append(ci)
    if unread:
        meta.append(t(lang, "inbox.unread", count=unread))
    draft = "📝 " if it.draft else ""
    title = f"{it.ref} · {esc(clip(it.title, 80))}"
    link = f'<a href="{html.escape(it.url)}">{title}</a>'
    return f"{BALL_ICON[w.ball]} {draft}{link}\n    " + " · ".join(meta)


def render_mr_list(
    ws: list[Watched], tab: str, lang: str, now: datetime, *, page: int = 1
) -> tuple[str, int, int]:
    role = Role.REVIEWER if tab == "rev" else Role.AUTHOR
    rows = _ordered([w for w in ws if w.role is role])
    hidden = 0
    if tab == "rev":
        hidden = sum(1 for w in rows if w.snapshot.get("approved_by_me"))
        rows = [w for w in rows if not w.snapshot.get("approved_by_me")]
    footer = [t(lang, "mr.hidden_approved", count=hidden)] if hidden else []
    if not rows:
        empty = t(lang, "mr.empty_review" if tab == "rev" else "mr.empty_own")
        return "\n".join([empty, *footer]), 1, 1

    pages = (len(rows) + MR_PAGE_SIZE - 1) // MR_PAGE_SIZE
    page = min(max(page, 1), pages)
    title = t(lang, "mr.tab_review" if tab == "rev" else "mr.tab_own")
    header = [f"<b>{title}</b> ({len(rows)})", t(lang, "mr.legend")]
    if pages > 1:
        header.append(t(lang, "mr.page", page=page, pages=pages))
    show_host = len({w.item.host for w in rows}) > 1
    start = (page - 1) * MR_PAGE_SIZE
    entries = [mr_line(w, lang, now, show_host=show_host) for w in rows[start : start + MR_PAGE_SIZE]]
    return "\n\n".join(["\n".join(header), *entries, *footer]), page, pages


def mr_tabs(
    tab: str, lang: str, *, page: int = 1, pages: int = 1, accounts: Sequence[Account] = ()
) -> InlineKeyboardMarkup:
    def label(key: str, name: str) -> str:
        return ("• " if tab == name else "") + t(lang, key)

    rows = [[btn(label("mr.tab_review", "rev"), "mr:rev"), btn(label("mr.tab_own_short", "own"), "mr:own")]]
    nav = []
    if page > 1:
        nav.append(btn(t(lang, "mr.prev"), f"mr:{tab}:{page - 1}"))
    if page < pages:
        nav.append(btn(t(lang, "mr.next"), f"mr:{tab}:{page + 1}"))
    if nav:
        rows.append(nav)

    gitlab_accounts = [account for account in accounts if account.kind == "gitlab"]
    for account in gitlab_accounts:
        host = account.host if len(gitlab_accounts) > 1 else "GitLab"
        base = {"scope": "all", "state": "opened"}
        if tab == "rev":
            searches = [
                ("mr.gitlab_review", {
                    "reviewer_username": account.username,
                    "not[approved_by_usernames][]": account.username,
                }),
            ]
        else:
            searches = [
                ("mr.gitlab_author", {"author_username": account.username}),
                ("mr.gitlab_assignee", {"assignee_username": account.username}),
            ]
        rows.append([
            InlineKeyboardButton(
                t(lang, key, host=host),
                url=f"https://{account.host}/dashboard/merge_requests?{urlencode(base | filters)}",
            )
            for key, filters in searches
        ])
    return InlineKeyboardMarkup(rows)


def render_inbox(
    ws: list[Watched], counts: dict[str, int], mention_count: int, lang: str, now: datetime
) -> tuple[str, list[Watched], bool]:
    mine = _ordered([w for w in ws if w.ball is Ball.ME])
    if not mine and not mention_count:
        return t(lang, "inbox.empty"), [], True
    lines = [t(lang, "inbox.header", count=len(mine)), ""]
    show_host = len({w.item.host for w in mine}) > 1
    for w in mine[:LIST_MAX]:
        lines.extend([mr_line(w, lang, now, counts.get(w.key, 0), show_host=show_host), ""])
    if len(mine) > LIST_MAX:
        lines.append(t(lang, "mr.more", count=len(mine) - LIST_MAX))
    if mention_count:
        lines.append(t(lang, "inbox.mentions", count=mention_count))
    return "\n".join(lines).rstrip(), mine[:INBOX_BUTTONS], False


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


async def _mr_view(ctx, user: User, tab: str, *, page: int = 1):
    st = deps.store(ctx)
    accounts = await st.accounts_for(user.tg_id)
    if not accounts:
        return t(user.lang, "mr.no_accounts"), None
    ws = visible(user, await st.watched_for_user(user.tg_id))
    text, page, pages = render_mr_list(ws, tab, user.lang, timeutil.now(), page=page)
    return text, mr_tabs(tab, user.lang, page=page, pages=pages, accounts=accounts)


async def cmd_mr(update, ctx) -> None:
    user = await access.current_user(update, ctx)
    if user is None:
        return
    text, markup = await _mr_view(ctx, user, "rev")
    await update.effective_chat.send_message(
        text, parse_mode=ParseMode.HTML, reply_markup=markup, link_preview_options=NO_PREVIEW
    )


async def cmd_my(update, ctx) -> None:
    user = await access.current_user(update, ctx)
    if user is None:
        return
    text, markup = await _mr_view(ctx, user, "own")
    await update.effective_chat.send_message(
        text, parse_mode=ParseMode.HTML, reply_markup=markup, link_preview_options=NO_PREVIEW
    )


async def cb_mr(update, ctx) -> None:
    q = update.callback_query
    await q.answer()
    user = await access.current_user(update, ctx)
    if user is None:
        return
    tab, _, requested_page = q.data.removeprefix("mr:").partition(":")
    if tab not in ("rev", "own"):
        return
    page = int(requested_page) if requested_page.isascii() and requested_page.isdecimal() else 1
    text, markup = await _mr_view(ctx, user, tab, page=page)
    await safe_edit(q, text, markup)


async def _inbox_view(ctx, user: User):
    st = deps.store(ctx)
    text, shown, empty = render_inbox(
        visible(user, await st.watched_for_user(user.tg_id)),
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
