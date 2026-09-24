"""Whose move is it on a merge request? /inbox shows only the ones where it's yours."""

from app.models import Ball, Details, ReviewItem, Role, Thread


def _open(thread: Thread) -> bool:
    return bool(thread.notes) and not (thread.resolvable and thread.resolved)


def awaits_me(thread: Thread, me: str) -> bool:
    """An open thread I took part in, where somebody else spoke last."""
    return _open(thread) and thread.notes[-1].author != me and any(n.author == me for n in thread.notes)


def whose_ball(item: ReviewItem, d: Details, me: str) -> Ball:
    if d.state != "opened":
        return Ball.NONE
    if item.role is Role.AUTHOR:
        return _as_author(d, me)
    if item.draft:
        return Ball.NONE
    return _as_reviewer(d, me)


def _as_author(d: Details, me: str) -> Ball:
    if d.has_conflicts:
        return Ball.ME
    if d.changes_requested_by - set(d.pending_reviewers) - {me}:  # not yet re-requested
        return Ball.ME
    if any(t.resolvable and _open(t) and t.notes[-1].author != me for t in d.threads):
        return Ball.ME
    if d.approvals_left == 0 and d.approved_by:
        return Ball.ME
    return Ball.THEM


def _as_reviewer(d: Details, me: str) -> Ball:
    if any(awaits_me(t, me) for t in d.threads):
        return Ball.ME
    if me in d.pending_reviewers:  # (re-)requested and not reviewed since
        return Ball.ME
    if me in d.approved_by:
        return Ball.NONE
    if me in d.changes_requested_by:
        return Ball.THEM
    took_part = any(n.author == me for t in d.threads for n in t.notes)
    return Ball.THEM if took_part else Ball.ME
