"""Event → Telegram HTML + inline keyboard. Everything user-supplied is escaped and clipped."""

import html
from datetime import datetime, timedelta

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, LinkPreviewOptions

from app.i18n import t
from app.models import Event, Kind

NO_PREVIEW = LinkPreviewOptions(is_disabled=True)
BODY_LIMIT = 600
QUOTE_LIMIT = 200
TITLE_LIMIT = 120
DIGEST_MAX = 15
BURST_MIN = 3  # this many comments from one person on one MR in one go → a single message
BURST_QUOTES = 3
_THREAD_KINDS = {Kind.NEW_COMMENT, Kind.REPLY_TO_ME, Kind.MENTION}


def esc(s: str | None) -> str:
    return html.escape(s or "", quote=False)


def clip(s: str | None, n: int) -> str:
    s = (s or "").strip()
    return s if len(s) <= n else s[: n - 1].rstrip() + "…"


def fmt_age(delta: timedelta, lang: str) -> str:
    minutes = int(delta.total_seconds() // 60)
    if minutes < 60:
        return t(lang, "age.minutes", n=max(minutes, 1))
    if minutes < 48 * 60:
        return t(lang, "age.hours", n=minutes // 60)
    return t(lang, "age.days", n=minutes // 1440)


def _btn(lang: str, key: str, data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(t(lang, key), callback_data=data)


def _url(ev: Event) -> str:
    url = ev.note.url if ev.note else (ev.item.url if ev.item else "")
    return url if url.startswith(("https://", "http://")) else ""


def _link(ev: Event) -> str:
    if ev.item:
        it = ev.item
        title = esc(clip(it.title, TITLE_LIMIT))
        return f'<a href="{html.escape(it.url)}">{it.ref} — {title}</a>\n<code>{esc(it.project)}</code>'
    url = _url(ev)
    title = esc(clip(ev.title, TITLE_LIMIT)) or esc(url)
    return f'<a href="{html.escape(url)}">{title}</a>' if url else title


def keyboard(ev: Event, event_id: int, lang: str) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if ev.item and ev.thread_id and ev.kind in _THREAD_KINDS:
        row = [_btn(lang, "btn.reply", f"a:reply:{event_id}")]
        if ev.resolvable:
            row.append(_btn(lang, "btn.resolve", f"a:resolve:{event_id}"))
        rows.append(row)
    if ev.kind in (Kind.REVIEW_REQUESTED, Kind.STALE_REVIEW):
        rows.append([_btn(lang, "btn.approve", f"a:approve:{event_id}")])
    if ev.kind is Kind.WAITING_ON_REVIEWER:
        rows.append([_btn(lang, "btn.ping", f"a:ping:{event_id}")])
    last: list[InlineKeyboardButton] = []
    if url := _url(ev):
        last.append(InlineKeyboardButton(t(lang, "btn.open"), url=url))
    last += [_btn(lang, "btn.snooze", f"a:snooze:{event_id}"), _btn(lang, "btn.read", f"a:read:{event_id}")]
    rows.append(last)
    return InlineKeyboardMarkup(rows)


def render(ev: Event, event_id: int, lang: str, now: datetime) -> tuple[str, InlineKeyboardMarkup]:
    header = t(
        lang,
        f"ev.{ev.kind}",
        actor=esc(f"@{ev.actor}" if ev.actor else ""),
        actors=esc(", ".join(f"@{a}" for a in ev.actors)),
        age=fmt_age(now - ev.since, lang) if ev.since else "",
    )
    parts = [header, "", _link(ev)]
    if ev.quote and ev.quote.body.strip():
        parts += ["", t(lang, "ev.your_comment"), f"<blockquote>{esc(clip(ev.quote.body, QUOTE_LIMIT))}</blockquote>"]
    if ev.note and ev.kind in _THREAD_KINDS and ev.note.body.strip():
        parts += ["", f"<blockquote>{esc(clip(ev.note.body, BODY_LIMIT))}</blockquote>"]
    return "\n".join(parts), keyboard(ev, event_id, lang)


def render_digest(events: list[Event], lang: str) -> str:
    lines = [t(lang, "digest.header", count=len(events)), ""]
    for ev in events[:DIGEST_MAX]:
        if ev.item:
            link = f'<a href="{html.escape(ev.item.url)}">{ev.item.ref}</a> {esc(clip(ev.item.title, 60))}'
        else:
            link = f'<a href="{html.escape(_url(ev))}">{esc(clip(ev.title, 60))}</a>'
        who = f" · @{esc(ev.actor)}" if ev.actor else ""
        lines.append(f"{t(lang, f'short.{ev.kind}')} · {link}{who}")
    if len(events) > DIGEST_MAX:
        lines.append(t(lang, "digest.more", count=len(events) - DIGEST_MAX))
    return "\n".join(lines)


def burst_kinds() -> frozenset[Kind]:
    return frozenset(_THREAD_KINDS)


def render_burst(events: list[Event], last_id: int, lang: str) -> tuple[str, InlineKeyboardMarkup]:
    """Several comments by one person on one MR (e.g. a submitted GitLab review) as one message."""
    first = events[0]
    parts = [t(lang, "ev.burst", actor=esc(f"@{first.actor}"), count=len(events)), "", _link(first)]
    replies = sum(1 for e in events if e.kind is Kind.REPLY_TO_ME)
    if replies:
        parts += ["", t(lang, "ev.burst_replies", count=replies)]
    parts += [
        f"<blockquote>{esc(clip(e.note.body, 200))}</blockquote>"
        for e in events[:BURST_QUOTES]
        if e.note and e.note.body.strip()
    ]
    if len(events) > BURST_QUOTES:
        parts.append(t(lang, "digest.more", count=len(events) - BURST_QUOTES))
    row: list[InlineKeyboardButton] = []
    if url := _url(first):
        row.append(InlineKeyboardButton(t(lang, "btn.open"), url=url))
    row.append(_btn(lang, "btn.read", f"a:ri:{last_id}"))
    return "\n".join(parts), InlineKeyboardMarkup([row])
