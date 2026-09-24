"""Send pending events to one user: respects quiet hours and mutes, batches the morning catch-up."""

import logging
from datetime import datetime, timedelta

from telegram.constants import ParseMode
from telegram.error import BadRequest, Forbidden, NetworkError, RetryAfter

from app.core.quiet import is_quiet
from app.core.render import NO_PREVIEW, render, render_digest
from app.storage.store import Store, User

log = logging.getLogger(__name__)
DIGEST_AFTER = timedelta(minutes=10)


async def _send(bot, chat_id: int, text: str, markup=None) -> None:
    try:
        await bot.send_message(
            chat_id, text, parse_mode=ParseMode.HTML, reply_markup=markup, link_preview_options=NO_PREVIEW
        )
    except BadRequest as err:  # permanent for this message — drop it, keep the queue moving
        log.warning("Telegram rejected a message for chat %s: %s", chat_id, err)


async def deliver_user(bot, store: Store, user: User, now: datetime) -> int:
    if is_quiet(user, now):
        return 0
    pending = await store.pending_events(user.tg_id, now)
    muted = [e.id for e in pending if e.event.kind in user.muted_kinds]
    if muted:
        await store.mark_delivered(muted, now)
    live = [e for e in pending if e.event.kind not in user.muted_kinds]
    waited = [e for e in live if e.snoozed_until is None and now - e.created_at >= DIGEST_AFTER]
    sent = 0
    try:
        if len(waited) >= 2:
            await _send(bot, user.chat_id, render_digest([e.event for e in waited], user.lang))
            await store.mark_delivered([e.id for e in waited], now)
            sent += 1
            batched = {e.id for e in waited}
            live = [e for e in live if e.id not in batched]
        for e in live:
            text, markup = render(e.event, e.id, user.lang, now)
            await _send(bot, user.chat_id, text, markup)
            await store.mark_delivered([e.id], now)
            sent += 1
    except Forbidden:
        log.info("user %s blocked the bot", user.tg_id)
        await store.update_user(user.tg_id, status="blocked")
        await store.mark_delivered([e.id for e in pending], now)
    except (NetworkError, RetryAfter) as err:
        log.warning("delivery to %s postponed: %s", user.tg_id, err)
    return sent
