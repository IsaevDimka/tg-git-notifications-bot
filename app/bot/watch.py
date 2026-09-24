"""/watch <MR link> — follow someone else's merge request: approvals, merged, closed."""

import re

import httpx
from telegram import InlineKeyboardMarkup
from telegram.constants import ParseMode

from app import timeutil
from app.bot import access, deps
from app.bot.keyboards import btn
from app.core.render import NO_PREVIEW, clip, esc
from app.i18n import t
from app.models import Role
from app.providers.base import ProviderError
from app.storage.store import User

_GITHUB = re.compile(r"^https?://github\.com/([^/\s]+/[^/\s]+)/pull/(\d+)", re.IGNORECASE)
_GITLAB = re.compile(r"^https?://([^/\s]+)/(.+?)/-/merge_requests/(\d+)", re.IGNORECASE)
LIST_MAX = 20


def parse_mr_url(url: str) -> tuple[str, str, str, int] | None:
    """(kind, host, project path, iid) for a GitLab MR or GitHub PR link, else None."""
    url = url.strip()
    if m := _GITHUB.match(url):
        return "github", "github.com", m[1], int(m[2])
    if m := _GITLAB.match(url):
        return "gitlab", m[1].lower(), m[2], int(m[3])
    return None


async def _refs(ctx, user: User) -> list[tuple[int, str, str, int]]:
    """(account_id, kind, project, iid) across all of the user's accounts."""
    st = deps.store(ctx)
    out = []
    for account in await st.accounts_for(user.tg_id):
        out += [(account.id, account.kind, project, iid) for project, iid in await st.watch_refs(account.id)]
    return out


def _list_view(user: User, refs: list[tuple[int, str, str, int]]) -> tuple[str, InlineKeyboardMarkup | None]:
    if not refs:
        return t(user.lang, "watch.empty"), None
    lines = [t(user.lang, "watch.header"), ""]
    buttons = []
    for i, (_, kind, project, iid) in enumerate(refs[:LIST_MAX]):
        ref = f"{project}{'#' if kind == 'github' else '!'}{iid}"
        lines.append(f"👁 <code>{esc(ref)}</code>")
        buttons.append([btn(f"✖ {ref}", f"wt:rm:{i}")])
    return "\n".join(lines), InlineKeyboardMarkup(buttons)


async def cmd_watch(update, ctx) -> None:
    user = await access.current_user(update, ctx)
    if user is None:
        return
    chat, lang = update.effective_chat, user.lang
    args = list(getattr(ctx, "args", None) or [])
    if not args:
        refs = await _refs(ctx, user)
        ctx.user_data["watch_refs"] = refs
        text, markup = _list_view(user, refs)
        await chat.send_message(text, parse_mode=ParseMode.HTML, reply_markup=markup)
        return
    parsed = parse_mr_url(args[0])
    if parsed is None:
        await chat.send_message(t(lang, "watch.usage"), parse_mode=ParseMode.HTML)
        return
    kind, host, project, iid = parsed
    st = deps.store(ctx)
    account = next((a for a in await st.accounts_for(user.tg_id) if a.kind == kind and a.host == host), None)
    if account is None:
        await chat.send_message(t(lang, "watch.no_account", host=esc(host)), parse_mode=ParseMode.HTML)
        return
    try:
        followed = await deps.provider_for(ctx, account).get_by_ref(project, iid, Role.WATCHER)
    except (ProviderError, httpx.HTTPError, ValueError, KeyError):
        await chat.send_message(t(lang, "watch.failed"), parse_mode=ParseMode.HTML)
        return
    await st.add_watch_ref(account.id, project, iid, timeutil.now())
    await chat.send_message(
        t(lang, "watch.added", ref=followed.ref, title=esc(clip(followed.title, 120))),
        parse_mode=ParseMode.HTML,
        link_preview_options=NO_PREVIEW,
    )


async def cb_watch(update, ctx) -> None:
    q = update.callback_query
    await q.answer()
    user = await access.current_user(update, ctx)
    if user is None:
        return
    refs = ctx.user_data.get("watch_refs", [])
    index = int(q.data.removeprefix("wt:rm:"))
    st = deps.store(ctx)
    if index < len(refs):
        account_id, _, project, iid = refs[index]
        account = await st.get_account(account_id)
        if account is not None and account.tg_id == user.tg_id:
            await st.delete_watch_ref(account_id, project, iid)
    refs = await _refs(ctx, user)
    ctx.user_data["watch_refs"] = refs
    text, markup = _list_view(user, refs)
    await q.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=markup)
