"""Noise filters: bot accounts, drafts under review, muted projects."""

import re

from app.core.render import burst_kinds
from app.models import Event, Kind, Role
from app.storage.store import User, Watched

_BOT = re.compile(r"\[bot\]$|^renovate|^dependabot|^(project|group)_\d+_bot|[-_]bot$", re.IGNORECASE)


def is_bot(username: str) -> bool:
    return bool(_BOT.search(username or ""))


def is_noise(user: User, ev: Event) -> bool:
    it = ev.item
    if it and it.project in user.muted_projects:
        return True
    # Only chatter is filtered: review requests and reminders from bot-authored MRs are real tasks.
    if ev.kind in burst_kinds() and user.mute_bots and ev.actor and is_bot(ev.actor):
        return True
    # Plain comments on drafts I review wait; replies to me and mentions still come through.
    return bool(user.mute_drafts and it and it.draft and it.role is Role.REVIEWER and ev.kind is Kind.NEW_COMMENT)


def visible(user: User, ws: list[Watched]) -> list[Watched]:
    """Watched items minus muted projects — for /mr, /inbox and the daily summary."""
    return [w for w in ws if w.item.project not in user.muted_projects]
