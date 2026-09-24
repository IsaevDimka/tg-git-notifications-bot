"""/status — is the bot actually working for me? /test — what notifications look like."""

from datetime import datetime, timedelta

from telegram.constants import ParseMode

from app import timeutil
from app.bot import access, deps
from app.core.quiet import is_quiet
from app.core.render import NO_PREVIEW, clip, esc, fmt_age, render
from app.i18n import t
from app.models import Event, Kind, Note, ReviewItem, Role
from app.storage.store import Account, User, Watched
from app.timeutil import parse_ts

CHUNK = 3800


def _account_block(a: Account, ws: list[Watched], lang: str, now: datetime) -> list[str]:
    lines = [t(lang, "status.account", host=esc(a.host), username=esc(a.username))]
    if a.last_error == "auth":
        lines.append(t(lang, "status.error", error=t(lang, "acc.err_auth")))
    elif a.last_error:
        lines.append(t(lang, "status.error", error=esc(clip(a.last_error, 120))))
    if a.last_ok_at:
        lines.append(t(lang, "status.ok", ago=fmt_age(now - parse_ts(a.last_ok_at), lang)))
    elif not a.last_error:
        lines.append(t(lang, "status.never"))
    mine = [w for w in ws if w.account_id == a.id]
    lines.append(t(lang, "status.counts", review=sum(w.role is Role.REVIEWER for w in mine),
                   mine=sum(w.role is Role.AUTHOR for w in mine), watching=sum(w.role is Role.WATCHER for w in mine)))
    if a.rate_remaining is not None:
        lines.append(t(lang, "status.rate", n=a.rate_remaining))
    return lines


def render_status(user: User, accounts: list[Account], ws: list[Watched], pending: int, lang: str,
                  now: datetime) -> str:
    lines = [t(lang, "status.header"), ""]
    if not accounts:
        lines.append(t(lang, "status.no_accounts"))
    for a in accounts:
        lines += [*_account_block(a, ws, lang, now), ""]
    lines.append(t(lang, "status.queue", count=pending))
    if is_quiet(user, now):
        lines.append(t(lang, "status.quiet_now"))
    return "\n".join(lines)


async def cmd_status(update, ctx) -> None:
    user = await access.current_user(update, ctx)
    if user is None:
        return
    st, now = deps.store(ctx), timeutil.now()
    text = render_status(user, await st.accounts_for(user.tg_id), await st.watched_for_user(user.tg_id),
                         len(await st.pending_events(user.tg_id, now)), user.lang, now)
    await update.effective_chat.send_message(text, parse_mode=ParseMode.HTML)


def sample_marker(kind: Kind, lang: str) -> str:
    return f"<i>{t(lang, f'short.{kind}')}</i>"


def _sample(kind: Kind, now: datetime) -> Event:
    url = "https://gitlab.example.com/team/app/-/merge_requests/42"
    it = ReviewItem(kind="gitlab", host="gitlab.example.com", project_id="1", iid=42, title="Add login page",
                    url=url, author="alice", project="team/app", role=Role.REVIEWER,
                    created_at=now - timedelta(days=2), updated_at=now)
    if kind is Kind.TOKEN_BROKEN:
        return Event(kind, dedup="sample", title="gitlab.example.com")
    return Event(kind, dedup="sample", item=it, actor="alice", note=Note("2", "alice", "Can we cache this?", now,
                 f"{url}#note_2"), quote=Note("1", "you", "Why a second query here?", now, f"{url}#note_1"),
                 thread_id="t", actors=("bob",), since=now - timedelta(days=2))


async def cmd_test(update, ctx) -> None:
    user = await access.current_user(update, ctx)
    if user is None:
        return
    now = timeutil.now()
    chunks, current = [], t(user.lang, "test.intro")
    for kind in Kind:
        text, _ = render(_sample(kind, now), 0, user.lang, now)
        block = f"{sample_marker(kind, user.lang)}\n{text}"
        if len(current) + len(block) + 8 > CHUNK:
            chunks.append(current)
            current = block
        else:
            current += f"\n\n———\n\n{block}"
    chunks.append(current)
    for chunk in chunks:
        await update.effective_chat.send_message(chunk, parse_mode=ParseMode.HTML, link_preview_options=NO_PREVIEW)
