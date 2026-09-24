"""Polling: provider state → stored Events. Cheap list call every tick; details only on change."""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

from app.core.ball import whose_ball
from app.core.diff import diff, snapshot
from app.models import Ball, Details, Event, Kind, Note, ReviewItem, Role
from app.providers.base import AuthError, Provider, ProviderError
from app.storage.store import Account, Store, Watched
from app.timeutil import iso, parse_ts

log = logging.getLogger(__name__)
ESCALATE_AFTER = timedelta(hours=24)
REFRESH_EVERY = timedelta(hours=1)  # conflicts / resolutions don't always bump updated_at


@dataclass(frozen=True)
class PollResult:
    emitted: int
    mentions_cursor: datetime


async def _emit(store: Store, account: Account, events: list[Event], now: datetime) -> int:
    emitted = 0
    for ev in events:
        if await store.add_event(account.tg_id, account.id, ev, now) is not None:
            emitted += 1
    return emitted


def _escalation(w: Watched, now: datetime, synced: bool) -> list[Event]:
    if not synced or w.role is not Role.AUTHOR or w.ball is not Ball.THEM or w.item.draft:
        return []
    if now - w.ball_since < ESCALATE_AFTER:
        return []
    pending = tuple(w.snapshot.get("pending_reviewers", ()))
    if not pending:
        return []
    return [
        Event(
            Kind.WAITING_ON_REVIEWER,
            dedup=f"wait:{w.key}:{now.date().isoformat()}",
            item=w.item,
            actors=pending,
            since=w.ball_since,
        )
    ]


def _refreshed(w: Watched) -> datetime:
    stamp = w.snapshot.get("refreshed_at")
    return parse_ts(stamp) if stamp else w.updated_at_remote


def _already_reviewed(d: Details, me: str) -> bool:
    """A newly listed MR I've already approved or commented on isn't news (e.g. a drive-by GitHub review)."""
    return (
        me in d.approved_by
        or me in d.changes_requested_by
        or any(n.author == me for t in d.threads for n in t.notes)
    )


async def _poll_item(
    provider: Provider, store: Store, account: Account, item: ReviewItem, now: datetime
) -> tuple[list[Event], Watched | None]:
    me = account.username
    old = await store.get_watched(account.id, item.key)
    unchanged = old is not None and item.updated_at <= old.updated_at_remote and item.role is old.role
    if unchanged and now - _refreshed(old) < REFRESH_EVERY:
        return _escalation(old, now, account.synced), None
    d = await provider.details(item)
    ball = whose_ball(item, d, me)
    events: list[Event] = []
    if old is None:
        since = item.updated_at
        if account.synced and item.role is Role.REVIEWER and not _already_reviewed(d, me):
            events.append(Event(Kind.REVIEW_REQUESTED, dedup=f"rr:{item.key}", item=item, actor=item.author))
    else:
        since = old.ball_since if ball == old.ball else item.updated_at
        if account.synced:
            events += diff(old.snapshot, d, item, me)
    snap = {**snapshot(d), "refreshed_at": iso(now), "approved_by_me": me in d.approved_by}
    w = Watched(account.id, item.key, item.role, item, item.updated_at, snap, ball, since)
    return events + _escalation(w, now, account.synced), w


async def _gone(provider: Provider, w: Watched) -> tuple[list[Event], bool]:
    """Returns (events, forget?) for a watched item that is no longer listed."""
    if w.role is not Role.AUTHOR:
        return [], True
    try:
        fresh = await provider.fetch_item(w.item)
    except ProviderError as e:
        log.warning("cannot re-check %s: %s", w.key, e)
        return [], False
    if fresh.state == "opened":
        return [], True
    kind = Kind.MERGED if fresh.state == "merged" else Kind.CLOSED
    return [Event(kind, dedup=f"{fresh.state}:{fresh.key}", item=fresh)], True


async def _mentions(provider: Provider, account: Account, now: datetime) -> tuple[list[Event], datetime]:
    if not account.synced:
        return [], now
    since = parse_ts(account.mentions_cursor) if account.mentions_cursor else now
    try:
        found = await provider.mentions(account.username, since)
    except AuthError:
        raise
    except ProviderError as e:
        log.warning("mentions unavailable for %s: %s", provider.host, e)
        return [], since
    events = [
        Event(
            Kind.MENTION,
            dedup=f"note:{provider.host}:{m.note_id}",
            actor=m.author,
            note=Note(m.note_id, m.author, m.body, m.created_at, m.url),
            title=m.title,
        )
        for m in found
    ]
    return events, max([since, *(m.created_at for m in found)])


async def poll_account(provider: Provider, store: Store, account: Account, now: datetime) -> PollResult:
    items = await provider.list_items(account.username)
    listed = {i.key for i in items}
    emitted = 0
    for item in items:
        try:
            events, watched = await _poll_item(provider, store, account, item, now)
        except AuthError:
            raise
        except ProviderError as e:
            log.warning("skipping %s: %s", item.key, e)
            continue
        emitted += await _emit(store, account, events, now)
        if watched is not None:
            await store.put_watched(watched)
    for w in await store.list_watched(account.id):
        if w.key in listed:
            continue
        events, forget = await _gone(provider, w)
        emitted += await _emit(store, account, events, now)
        if forget:
            await store.delete_watched(account.id, w.key)
    events, cursor = await _mentions(provider, account, now)
    emitted += await _emit(store, account, events, now)
    return PollResult(emitted, cursor)


async def poll_one(store: Store, provider: Provider, account: Account, now: datetime) -> None:
    try:
        res = await poll_account(provider, store, account, now)
    except AuthError:
        await store.update_account(account.id, last_poll_at=iso(now), last_error="auth")
        return
    except Exception as e:  # noqa: BLE001 — one account must never stop the loop
        log.exception("poll failed for account %s", account.id)
        await store.update_account(account.id, last_poll_at=iso(now), last_error=(str(e) or type(e).__name__)[:200])
        return
    await store.update_account(
        account.id,
        synced=1,
        last_poll_at=iso(now),
        last_ok_at=iso(now),
        last_error=None,
        mentions_cursor=iso(res.mentions_cursor),
    )


async def poll_due(store: Store, make: Callable[[Account], Provider], now: datetime) -> int:
    accounts = await store.due_accounts(now)
    for account in accounts:
        try:
            provider = make(account)
        except Exception as e:  # noqa: BLE001 — undecryptable token (secret.key replaced)
            log.warning("account %s unusable: %s", account.id, type(e).__name__)
            await store.update_account(account.id, last_poll_at=iso(now), last_error="auth")
            continue
        await poll_one(store, provider, account, now)
    return len(accounts)
