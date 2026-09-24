"""Send pending events to one user: respects quiet hours and mutes, batches the morning catch-up."""

import logging
from datetime import datetime, timedelta

from telegram.constants import ParseMode
from telegram.error import BadRequest, Forbidden, NetworkError, RetryAfter

from app.core.noise import is_noise
from app.core.quiet import is_quiet
from app.core.render import BURST_MIN, NO_PREVIEW, burst_kinds, render, render_burst, render_digest
from app.models import Kind
from app.storage.store import Store, StoredEvent, User

log = logging.getLogger(__name__)
DIGEST_AFTER = timedelta(minutes=10)


async def _send(bot, chat_id: int, text: str, markup=None) -> None:
    try:
        await bot.send_message(
            chat_id, text, parse_mode=ParseMode.HTML, reply_markup=markup, link_preview_options=NO_PREVIEW
        )
    except BadRequest as err:  # permanent for this message — drop it, keep the queue moving
        log.warning("Telegram rejected a message for chat %s: %s", chat_id, err)


def _bursts(live: list[StoredEvent]) -> list[list[StoredEvent]]:
    """Group comments by (MR, author); groups of BURST_MIN+ become one message, the rest stay single."""
    kinds = burst_kinds()
    groups: dict[tuple, list[StoredEvent]] = {}
    for e in live:
        ev = e.event
        key = (ev.item_key, ev.actor) if ev.item and ev.kind in kinds else ("single", e.id)
        groups.setdefault(key, []).append(e)
    out: list[list[StoredEvent]] = []
    for key, group in groups.items():
        if key[0] != "single" and len(group) >= BURST_MIN:
            out.append(group)
        else:
            out += [[e] for e in group]
    return out


# Live activity may break quiet hours on urgent MRs; reminders and summaries never do.
URGENT_KINDS = frozenset({
    Kind.REVIEW_REQUESTED, Kind.REREVIEW, Kind.REPLY_TO_ME, Kind.MENTION, Kind.NEW_COMMENT, Kind.PING,
    Kind.CHANGES_REQUESTED, Kind.CONFLICT,
})


def _urgent(ev, urgent_labels: frozenset[str]) -> bool:
    return ev.kind in URGENT_KINDS and bool(ev.item and urgent_labels & {label.lower() for label in ev.item.labels})


def _redundant(user: User, ev) -> bool:
    """The morning summary already lists reviews waiting on me — don't also nag MR by MR."""
    return ev.kind is Kind.STALE_REVIEW and user.digest_enabled


async def deliver_user(bot, store: Store, user: User, now: datetime, urgent_labels: frozenset[str] = frozenset()) -> int:
    quiet = is_quiet(user, now)
    if quiet and not urgent_labels:
        return 0
    pending = await store.pending_events(user.tg_id, now)
    muted = [e.id for e in pending if e.event.kind in user.muted_kinds]
    if muted:
        await store.mark_delivered(muted, now)
    noise = [
        e.id
        for e in pending
        if e.event.kind not in user.muted_kinds and (is_noise(user, e.event) or _redundant(user, e.event))
    ]
    if noise:  # swallowed quietly and kept out of unread counts
        await store.mark_delivered(noise, now)
        await store.mark_read(noise, now)
    skip = set(muted) | set(noise)
    live = [e for e in pending if e.id not in skip]
    if quiet:  # only urgent labels (blocker/hotfix) break through; the rest waits for the morning
        live = [e for e in live if _urgent(e.event, urgent_labels)]
    waited = [] if quiet else [e for e in live if e.snoozed_until is None and now - e.created_at >= DIGEST_AFTER]
    sent = 0
    try:
        if len(waited) >= 2:
            await _send(bot, user.chat_id, render_digest([e.event for e in waited], user.lang))
            await store.mark_delivered([e.id for e in waited], now)
            sent += 1
            batched = {e.id for e in waited}
            live = [e for e in live if e.id not in batched]
        for group in _bursts(live):
            if len(group) == 1:
                text, markup = render(group[0].event, group[0].id, user.lang, now)
            else:
                text, markup = render_burst([e.event for e in group], group[-1].id, user.lang)
            await _send(bot, user.chat_id, text, markup)
            await store.mark_delivered([e.id for e in group], now)
            sent += 1
    except Forbidden:
        log.info("user %s blocked the bot", user.tg_id)
        await store.update_user(user.tg_id, status="blocked")
        await store.mark_delivered([e.id for e in pending], now)
    except (NetworkError, RetryAfter) as err:
        log.warning("delivery to %s postponed: %s", user.tg_id, err)
    return sent


async def deliver_all(bot, store: Store, now: datetime, urgent_labels: frozenset[str] = frozenset()) -> int:
    """One user's failure must never hold up everybody after them."""
    sent = 0
    for user in await store.active_users():
        try:
            sent += await deliver_user(bot, store, user, now, urgent_labels)
        except Exception:
            log.exception("delivery failed for user %s", user.tg_id)
    return sent
