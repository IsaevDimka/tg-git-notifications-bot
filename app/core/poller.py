"""Polling: provider state → stored Events. Cheap list call every tick; details only on change."""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

from app.core.ball import whose_ball
from app.core.diff import diff, rereview_state, snapshot
from app.models import Ball, Details, Event, Kind, Note, ReviewItem, Role
from app.providers.base import AuthError, Provider, ProviderError
from app.storage.store import Account, Store, Watched
from app.timeutil import iso, parse_ts

log = logging.getLogger(__name__)
ESCALATE_AFTER = timedelta(hours=24)
STALE_REVIEW_AFTER = timedelta(days=2)
REMINDER_MAX_AGE = timedelta(days=30)
REMINDERS_PER_TICK = 5  # a big backlog drains a few per poll instead of bursting
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


def _reminders(w: Watched, now: datetime, synced: bool) -> list[Event]:
    """Daily nudges while a move is overdue: my MR waits for reviewers (24 h+), or an MR waits for my
    review (2 d+). Stop after REMINDER_MAX_AGE so abandoned MRs don't nag forever."""
    if not synced or w.item.draft or w.ball is Ball.NONE:
        return []
    waited = now - w.ball_since
    if waited > REMINDER_MAX_AGE:
        return []
    day = now.date().isoformat()
    if w.role is Role.AUTHOR and w.ball is Ball.THEM and waited >= ESCALATE_AFTER:
        pending = tuple(w.snapshot.get("pending_reviewers", ()))
        if pending:
            return [
                Event(Kind.WAITING_ON_REVIEWER, dedup=f"wait:{w.key}:{day}", item=w.item, actors=pending,
                      since=w.ball_since)
            ]
    if w.role is Role.REVIEWER and w.ball is Ball.ME and waited >= STALE_REVIEW_AFTER:
        return [
            Event(Kind.STALE_REVIEW, dedup=f"stale:{w.key}:{day}", item=w.item, actor=w.item.author,
                  since=w.ball_since)
        ]
    return []


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
) -> tuple[list[Event], list[Event], Watched | None]:
    """Returns (events, reminders, watched-to-save)."""
    me = account.username
    old = await store.get_watched(account.id, item.key)
    unchanged = old is not None and item.updated_at <= old.updated_at_remote and item.role is old.role
    if unchanged and now - _refreshed(old) < REFRESH_EVERY:
        return [], _reminders(old, now, account.synced), None
    d = await provider.details(item)
    ball = whose_ball(item, d, me)
    events: list[Event] = []
    if old is None:
        since = item.updated_at
        # a draft isn't ready for review yet: the request is announced when it leaves draft (below)
        if account.synced and item.role is Role.REVIEWER and not item.draft and not _already_reviewed(d, me):
            events.append(Event(Kind.REVIEW_REQUESTED, dedup=f"rr:{item.key}", item=item, actor=item.author))
        rereview_since = None
    else:
        rerev_events, rereview_since = rereview_state(old.snapshot, d, item, me, now)
        if rereview_since:
            ball = Ball.ME
        since = old.ball_since if ball == old.ball else item.updated_at
        if account.synced:
            events += diff(old.snapshot, d, item, me) + rerev_events
            became_ready = old.item.draft and not item.draft
            if became_ready and item.role is Role.REVIEWER and not _already_reviewed(d, me):
                events.append(Event(Kind.REVIEW_REQUESTED, dedup=f"rr:{item.key}:ready", item=item, actor=item.author))
    snap = {
        **snapshot(d),
        "refreshed_at": iso(now),
        "approved_by_me": me in d.approved_by,
        "rereview_since": rereview_since,
    }
    w = Watched(account.id, item.key, item.role, item, item.updated_at, snap, ball, since)
    return events, _reminders(w, now, account.synced), w


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
    items = list(await provider.list_items(account.username))
    listed = {i.key for i in items}
    emitted = 0
    for project, iid in await store.watch_refs(account.id):  # /watch links
        try:
            followed = await provider.get_by_ref(project, iid, Role.WATCHER)
        except AuthError:
            raise
        except ProviderError as e:
            log.warning("watched %s!%s unavailable: %s", project, iid, e)
            continue
        if followed.key in listed:  # already mine or on my review — that role wins
            continue
        if followed.state != "opened":
            kind = Kind.MERGED if followed.state == "merged" else Kind.CLOSED
            emitted += await _emit(store, account, [Event(kind, dedup=f"{followed.state}:{followed.key}", item=followed)], now)
            await store.delete_watch_ref(account.id, project, iid)
            continue
        items.append(followed)
        listed.add(followed.key)
    reminders: list[Event] = []
    for item in items:
        try:
            events, due, watched = await _poll_item(provider, store, account, item, now)
        except AuthError:
            raise
        except ProviderError as e:
            log.warning("skipping %s: %s", item.key, e)
            continue
        emitted += await _emit(store, account, events, now)
        reminders += due
        if watched is not None:
            await store.put_watched(watched)
    sent_reminders = 0
    for reminder in reminders:
        if sent_reminders >= REMINDERS_PER_TICK:
            break
        if await store.add_event(account.tg_id, account.id, reminder, now) is not None:
            sent_reminders += 1
    emitted += sent_reminders
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


async def _mark_auth(store: Store, account: Account, now: datetime) -> None:
    """Record a dead token; tell the user once per breakage, not on every tick."""
    if account.last_error != "auth":
        notice = Event(Kind.TOKEN_BROKEN, dedup=f"auth:{account.id}:{iso(now)}", title=account.host)
        await store.add_event(account.tg_id, account.id, notice, now)
    await store.update_account(account.id, last_poll_at=iso(now), last_error="auth")


async def poll_one(store: Store, provider: Provider, account: Account, now: datetime) -> None:
    try:
        res = await poll_account(provider, store, account, now)
    except AuthError:
        await _mark_auth(store, account, now)
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
        rate_remaining=getattr(provider, "rate_remaining", None),
    )


async def poll_due(store: Store, make: Callable[[Account], Provider], now: datetime) -> int:
    accounts = await store.due_accounts(now)
    for account in accounts:
        try:
            provider = make(account)
        except Exception as e:  # noqa: BLE001 — undecryptable token (secret.key replaced)
            log.warning("account %s unusable: %s", account.id, type(e).__name__)
            await _mark_auth(store, account, now)
            continue
        await poll_one(store, provider, account, now)
    return len(accounts)
