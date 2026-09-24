"""Provider-neutral domain types. Providers produce them; core and storage consume them."""

import re
from dataclasses import asdict, dataclass
from datetime import datetime
from enum import StrEnum

from app.timeutil import iso, parse_ts


class Role(StrEnum):
    REVIEWER = "reviewer"
    AUTHOR = "author"
    WATCHER = "watcher"  # followed by link via /watch — someone else's MR


class Ball(StrEnum):
    ME = "me"
    THEM = "them"
    NONE = "none"


class Kind(StrEnum):
    REVIEW_REQUESTED = "review_requested"
    NEW_COMMENT = "new_comment"
    REPLY_TO_ME = "reply_to_me"
    THREAD_RESOLVED = "thread_resolved"
    THREAD_REOPENED = "thread_reopened"
    MENTION = "mention"
    APPROVED = "approved"
    CHANGES_REQUESTED = "changes_requested"
    MERGED = "merged"
    CLOSED = "closed"
    CONFLICT = "conflict"
    WAITING_ON_REVIEWER = "waiting_on_reviewer"
    TOKEN_BROKEN = "token_broken"
    REREVIEW = "rereview"
    STALE_REVIEW = "stale_review"
    PING = "ping"  # a colleague in this bot asked you to look at an MR


@dataclass(frozen=True)
class ReviewItem:
    kind: str  # "gitlab" | "github"
    host: str
    project_id: str  # GitLab numeric id as str; GitHub "owner/repo"
    iid: int
    title: str
    url: str
    author: str
    project: str  # full path for display
    role: Role
    created_at: datetime
    updated_at: datetime
    state: str = "opened"  # opened | merged | closed
    draft: bool = False
    labels: tuple[str, ...] = ()

    @property
    def key(self) -> str:
        return f"{self.host}:{self.project_id}:{self.iid}"

    @property
    def ref(self) -> str:
        return f"{'#' if self.kind == 'github' else '!'}{self.iid}"

    @property
    def project_short(self) -> str:
        return self.project.rsplit("/", 1)[-1]


@dataclass(frozen=True)
class Note:
    id: str
    author: str
    body: str
    created_at: datetime
    url: str


@dataclass(frozen=True)
class Thread:
    id: str
    resolvable: bool
    resolved: bool
    notes: tuple[Note, ...]


@dataclass(frozen=True)
class Details:
    state: str
    threads: tuple[Thread, ...]
    approved_by: frozenset[str]
    changes_requested_by: frozenset[str]
    pending_reviewers: tuple[str, ...]
    has_conflicts: bool
    approvals_left: int | None  # None = unknown (GitHub)
    pipeline: str | None  # success | failed | running | None
    head_sha: str | None = None  # last commit; a change after my review means "look again"


@dataclass(frozen=True)
class Mention:
    note_id: str
    author: str
    body: str
    url: str
    title: str
    created_at: datetime


@dataclass(frozen=True)
class Event:
    kind: Kind
    dedup: str  # unique per user; the same note always yields the same dedup key
    item: ReviewItem | None = None
    actor: str = ""
    note: Note | None = None
    quote: Note | None = None
    thread_id: str = ""
    resolvable: bool = False
    actors: tuple[str, ...] = ()
    title: str = ""
    since: datetime | None = None

    @property
    def item_key(self) -> str:
        if self.item:
            return self.item.key
        return f"mention:{self.dedup}" if self.kind is Kind.MENTION else f"notice:{self.dedup}"


def mentions_user(body: str, username: str) -> bool:
    pattern = rf"(?<![\w.@-])@{re.escape(username)}(?![\w-]|\.\w)"
    return re.search(pattern, body, re.IGNORECASE) is not None


def item_to_dict(i: ReviewItem) -> dict:
    return {**asdict(i), "role": str(i.role), "created_at": iso(i.created_at), "updated_at": iso(i.updated_at)}


def item_from_dict(d: dict) -> ReviewItem:
    return ReviewItem(
        **{
            **d,
            "role": Role(d["role"]),
            "labels": tuple(d.get("labels", ())),
            "created_at": parse_ts(d["created_at"]),
            "updated_at": parse_ts(d["updated_at"]),
        }
    )


def _note_to_dict(n: Note | None) -> dict | None:
    return None if n is None else {**asdict(n), "created_at": iso(n.created_at)}


def _note_from_dict(d: dict | None) -> Note | None:
    return None if d is None else Note(**{**d, "created_at": parse_ts(d["created_at"])})


def event_to_payload(ev: Event) -> dict:
    return {
        "item": item_to_dict(ev.item) if ev.item else None,
        "actor": ev.actor,
        "note": _note_to_dict(ev.note),
        "quote": _note_to_dict(ev.quote),
        "thread_id": ev.thread_id,
        "resolvable": ev.resolvable,
        "actors": list(ev.actors),
        "title": ev.title,
        "since": iso(ev.since) if ev.since else None,
    }


def event_from_payload(kind: str, dedup: str, p: dict) -> Event:
    return Event(
        kind=Kind(kind),
        dedup=dedup,
        item=item_from_dict(p["item"]) if p.get("item") else None,
        actor=p.get("actor", ""),
        note=_note_from_dict(p.get("note")),
        quote=_note_from_dict(p.get("quote")),
        thread_id=p.get("thread_id", ""),
        resolvable=p.get("resolvable", False),
        actors=tuple(p.get("actors", ())),
        title=p.get("title", ""),
        since=parse_ts(p["since"]) if p.get("since") else None,
    )
