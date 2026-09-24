"""Daily summary: once a day at the user's chosen local time — what waits for them."""

import logging
from datetime import datetime, time

from telegram.constants import ParseMode

from app.bot.views import mr_line
from app.core.noise import visible
from app.core.quiet import is_quiet, user_zone
from app.core.render import NO_PREVIEW
from app.i18n import t
from app.models import Ball, Role
from app.storage.store import Store, User, Watched

log = logging.getLogger(__name__)
SECTION_MAX = 5


def digest_due(user: User, now: datetime) -> str | None:
    """The user's local date if today's summary should go out now, else None."""
    if not user.digest_enabled or is_quiet(user, now):
        return None
    local = now.astimezone(user_zone(user.tz))
    if local.time() < time.fromisoformat(user.digest_time):
        return None
    today = local.date().isoformat()
    return None if user.digest_last == today else today


def _newest_first(ws: list[Watched]) -> list[Watched]:
    return sorted(ws, key=lambda w: -w.item.updated_at.timestamp())


def render_daily(ws: list[Watched], lang: str, now: datetime) -> str | None:
    review = _newest_first([w for w in ws if w.role is Role.REVIEWER and w.ball is Ball.ME])
    mine = _newest_first([w for w in ws if w.role is Role.AUTHOR and w.ball is Ball.ME])
    waiting = [w for w in ws if w.role is Role.AUTHOR and w.ball is Ball.THEM]
    if not (review or mine or waiting):
        return None
    lines = [t(lang, "daily.header")]
    for key, rows in (("daily.review", review), ("daily.mine", mine)):
        if rows:
            lines += ["", t(lang, key, count=len(rows))]
            lines += [mr_line(w, lang, now) for w in rows[:SECTION_MAX]]
            if len(rows) > SECTION_MAX:
                lines.append(t(lang, "mr.more", count=len(rows) - SECTION_MAX))
    if waiting:
        lines += ["", t(lang, "daily.waiting", count=len(waiting))]
    lines += ["", t(lang, "daily.footer")]
    return "\n".join(lines)


async def send_daily(bot, store: Store, now: datetime) -> int:
    sent = 0
    for user in await store.active_users():
        today = digest_due(user, now)
        if today is None:
            continue
        try:
            text = render_daily(visible(user, await store.watched_for_user(user.tg_id)), user.lang, now)
            if text:
                await bot.send_message(user.chat_id, text, parse_mode=ParseMode.HTML, link_preview_options=NO_PREVIEW)
                sent += 1
            await store.update_user(user.tg_id, digest_last=today)
        except Exception:
            log.exception("daily summary failed for user %s", user.tg_id)
    return sent
