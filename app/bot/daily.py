"""Daily summary: once a day at the user's chosen local time — what waits for them."""

import logging
from datetime import datetime, time

from telegram.constants import ParseMode

from app.bot.views import mr_line
from app.core.noise import visible
from app.core.quiet import user_zone
from app.core.render import NO_PREVIEW
from app.i18n import t
from app.models import Ball, Role
from app.storage.store import Store, User, Watched

log = logging.getLogger(__name__)
SECTION_MAX = 5


def _due(user: User, now: datetime, enabled: bool, at: str, last: str | None) -> str | None:
    """A time the user picked explicitly wins over quiet hours; quiet weekends still apply."""
    if not enabled:
        return None
    local = now.astimezone(user_zone(user.tz))
    if user.quiet_enabled and user.quiet_weekends and local.weekday() >= 5:
        return None
    if local.time() < time.fromisoformat(at):
        return None
    today = local.date().isoformat()
    return None if last == today else today


def digest_due(user: User, now: datetime) -> str | None:
    """The user's local date if today's morning summary should go out now, else None."""
    return _due(user, now, user.digest_enabled, user.digest_time, user.digest_last)


def evening_due(user: User, now: datetime) -> str | None:
    """Same for the (opt-in) evening summary of what still waits for the user's answer."""
    return _due(user, now, user.evening_enabled, user.evening_time, user.evening_last)


def _newest_first(ws: list[Watched]) -> list[Watched]:
    return sorted(ws, key=lambda w: -w.item.updated_at.timestamp())


def render_daily(ws: list[Watched], lang: str, now: datetime, evening: bool = False) -> str | None:
    review = _newest_first([w for w in ws if w.role is Role.REVIEWER and w.ball is Ball.ME])
    mine = _newest_first([w for w in ws if w.role is Role.AUTHOR and w.ball is Ball.ME])
    waiting = [] if evening else [w for w in ws if w.role is Role.AUTHOR and w.ball is Ball.THEM]
    if not (review or mine or waiting):
        return None
    lines = [t(lang, "daily.evening_header" if evening else "daily.header")]
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
    """Morning and (opt-in) evening summaries; each at most once per local day."""
    sent = 0
    for user in await store.active_users():
        for evening, due, last_field in ((False, digest_due, "digest_last"), (True, evening_due, "evening_last")):
            today = due(user, now)
            if today is None:
                continue
            try:
                ws = visible(user, await store.watched_for_user(user.tg_id))
                text = render_daily(ws, user.lang, now, evening=evening)
                if text:
                    await bot.send_message(
                        user.chat_id, text, parse_mode=ParseMode.HTML, link_preview_options=NO_PREVIEW
                    )
                    sent += 1
                await store.update_user(user.tg_id, **{last_field: today})
            except Exception:
                log.exception("summary failed for user %s", user.tg_id)
    return sent
