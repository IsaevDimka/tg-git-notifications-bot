"""Noise filters: bot accounts, drafts under review, muted projects."""

import re

from app.models import Event, Role
from app.storage.store import User, Watched

_BOT = re.compile(r"\[bot\]$|^renovate|^dependabot|^(project|group)_\d+_bot|[-_]bot$", re.IGNORECASE)


def is_bot(username: str) -> bool:
    return bool(_BOT.search(username or ""))


def is_noise(user: User, ev: Event) -> bool:
    it = ev.item
    if it and it.project in user.muted_projects:
        return True
    if user.mute_bots and ev.actor and is_bot(ev.actor):
        return True
    return bool(user.mute_drafts and it and it.draft and it.role is Role.REVIEWER)


def visible(user: User, ws: list[Watched]) -> list[Watched]:
    """Watched items minus muted projects — for /mr, /inbox and the daily summary."""
    return [w for w in ws if w.item.project not in user.muted_projects]
