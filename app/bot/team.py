"""/load — who is holding reviews (numbers only), /stats — your review activity; also the Friday report."""

from collections import Counter
from datetime import datetime, timedelta

from telegram.constants import ParseMode

from app import timeutil
from app.bot import access, deps
from app.core.noise import visible
from app.core.render import esc, fmt_age
from app.i18n import t
from app.models import Ball, Role
from app.storage.store import Watched

MAX_DAYS = 90


def render_load(mine: list[Watched], peers: list[tuple[str, list[Watched]]], lang: str, now: datetime) -> str:
    """mine: the viewer's watched items; peers: (git username, their watched items) of colleagues who share."""
    waiting_on = Counter(
        name for w in mine if w.role is Role.AUTHOR and w.ball is Ball.THEM for name in w.snapshot.get("pending_reviewers", ())
    )
    lines = [t(lang, "load.header")]
    if peers:
        lines += ["", t(lang, "load.team")]
        rows = []
        for name, ws in peers:
            mine_to_review = [w for w in ws if w.role is Role.REVIEWER and w.ball is Ball.ME]
            if mine_to_review:
                oldest = fmt_age(now - min(w.ball_since for w in mine_to_review), lang)
                rows.append((len(mine_to_review), t(lang, "load.line_oldest", name=esc(name), count=len(mine_to_review), age=oldest)))
            else:
                rows.append((0, t(lang, "load.line", name=esc(name), count=0)))
        lines += [text for _, text in sorted(rows, key=lambda r: -r[0])]
    if waiting_on:
        lines += ["", t(lang, "load.mine")]
        lines += [t(lang, "load.line", name=esc(name), count=n) for name, n in waiting_on.most_common()]
    if len(lines) == 1:
        return t(lang, "load.empty")
    return "\n".join([*lines, "", t(lang, "load.privacy")])


def render_stats(counts: dict[str, int], ws: list[Watched], days: int, lang: str, now: datetime,
                 header_key: str = "stats.header") -> str:
    c = counts.get
    waiting = [w for w in ws if w.role is Role.REVIEWER and w.ball is Ball.ME]
    own = [w for w in ws if w.role is Role.AUTHOR]
    oldest = t(lang, "stats.oldest", age=fmt_age(now - min(w.ball_since for w in waiting), lang)) if waiting else ""
    return "\n".join([
        t(lang, header_key, days=days),
        "",
        t(lang, "stats.reviews", requested=c("review_requested", 0), rereview=c("rereview", 0)),
        t(lang, "stats.talk", replies=c("reply_to_me", 0), mentions=c("mention", 0)),
        t(lang, "stats.mine", merged=c("merged", 0), approved=c("approved", 0), changes=c("changes_requested", 0)),
        "",
        t(lang, "stats.now", waiting=len(waiting), oldest=oldest, own=len(own),
          pending=sum(1 for w in own if w.ball is Ball.THEM)),
    ])


async def stats_text(store, user, days: int, now: datetime, header_key: str = "stats.header") -> str:
    counts = await store.event_counts(user.tg_id, now - timedelta(days=days))
    ws = visible(user, await store.watched_for_user(user.tg_id))
    return render_stats(counts, ws, days, user.lang, now, header_key)


async def cmd_load(update, ctx) -> None:
    user = await access.current_user(update, ctx)
    if user is None:
        return
    st = deps.store(ctx)
    mine = visible(user, await st.watched_for_user(user.tg_id))
    my_hosts = {(a.kind, a.host) for a in await st.accounts_for(user.tg_id)}
    peers = []
    for peer in await st.active_users():
        if not peer.share_load:
            continue
        shared = [a for a in await st.accounts_for(peer.tg_id) if (a.kind, a.host) in my_hosts]
        if shared:
            peers.append((shared[0].username, await st.watched_for_user(peer.tg_id)))
    await update.effective_chat.send_message(
        render_load(mine, peers, user.lang, timeutil.now()), parse_mode=ParseMode.HTML
    )


async def cmd_stats(update, ctx) -> None:
    """/stats [days] — default 7, at most 90."""
    user = await access.current_user(update, ctx)
    if user is None:
        return
    args = list(getattr(ctx, "args", None) or [])
    days = min(int(args[0]), MAX_DAYS) if args and args[0].isdigit() and int(args[0]) > 0 else 7
    text = await stats_text(deps.store(ctx), user, days, timeutil.now())
    await update.effective_chat.send_message(text, parse_mode=ParseMode.HTML)
