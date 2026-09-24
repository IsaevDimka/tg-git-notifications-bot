"""Compare the stored snapshot of a merge request with fresh Details and emit Events."""

from app.models import Details, Event, Kind, ReviewItem, Role, mentions_user
from app.timeutil import iso


def snapshot(d: Details) -> dict:
    return {
        "notes": sorted({n.id for t in d.threads for n in t.notes}, key=lambda x: (len(x), x)),
        "resolved": {t.id: t.resolved for t in d.threads if t.resolvable},
        "approved_by": sorted(d.approved_by),
        "changes_requested_by": sorted(d.changes_requested_by),
        "pending_reviewers": list(d.pending_reviewers),
        "has_conflicts": d.has_conflicts,
        "open_threads": sum(1 for t in d.threads if t.resolvable and not t.resolved),
        "pipeline": d.pipeline,
        "approvals_left": d.approvals_left,
    }


def diff(old: dict, d: Details, item: ReviewItem, me: str) -> list[Event]:
    return [
        *_notes(old, d, item, me),
        *_resolutions(old, d, item, me),
        *_author_events(old, d, item, me),
        *_rerequest(old, d, item, me),
    ]


def _rerequest(old: dict, d: Details, item: ReviewItem, me: str) -> list[Event]:
    if item.role is not Role.REVIEWER or me not in d.pending_reviewers:
        return []
    if me in old.get("pending_reviewers", ()):
        return []
    return [Event(Kind.REVIEW_REQUESTED, dedup=f"rr:{item.key}:{iso(item.updated_at)}", item=item, actor=item.author)]


def _notes(old: dict, d: Details, item: ReviewItem, me: str) -> list[Event]:
    seen = set(old.get("notes", ()))
    out: list[Event] = []
    for th in d.threads:
        for idx, note in enumerate(th.notes):
            if note.id in seen or note.author == me:
                continue
            mine = [n for n in th.notes[:idx] if n.author == me]
            common = dict(
                dedup=f"note:{item.host}:{note.id}",
                item=item,
                actor=note.author,
                note=note,
                thread_id=th.id,
                resolvable=th.resolvable and not th.resolved,
            )
            if mine:
                out.append(Event(Kind.REPLY_TO_ME, quote=mine[-1], **common))
            elif mentions_user(note.body, me):
                out.append(Event(Kind.MENTION, title=item.title, **common))
            else:
                out.append(Event(Kind.NEW_COMMENT, **common))
    return out


def _resolutions(old: dict, d: Details, item: ReviewItem, me: str) -> list[Event]:
    before = old.get("resolved", {})
    out: list[Event] = []
    for th in d.threads:
        if not th.resolvable or not th.notes or th.notes[0].author != me or th.id not in before:
            continue
        if before[th.id] == th.resolved:
            continue
        kind = Kind.THREAD_RESOLVED if th.resolved else Kind.THREAD_REOPENED
        out.append(
            Event(
                kind,
                dedup=f"res:{item.host}:{th.id}:{int(th.resolved)}:{len(th.notes)}",
                item=item,
                quote=th.notes[0],
                thread_id=th.id,
            )
        )
    return out


def _author_events(old: dict, d: Details, item: ReviewItem, me: str) -> list[Event]:
    if item.role is not Role.AUTHOR:
        return []
    out: list[Event] = []
    approved = sorted(d.approved_by - set(old.get("approved_by", ())) - {me})
    if approved:
        out.append(
            Event(Kind.APPROVED, dedup=f"appr:{item.key}:{','.join(approved)}", item=item, actors=tuple(approved))
        )
    asked = sorted(d.changes_requested_by - set(old.get("changes_requested_by", ())) - {me})
    if asked:
        out.append(
            Event(
                Kind.CHANGES_REQUESTED,
                dedup=f"cr:{item.key}:{','.join(asked)}:{len(old.get('notes', ()))}",
                item=item,
                actors=tuple(asked),
            )
        )
    if d.has_conflicts and not old.get("has_conflicts", False):
        out.append(Event(Kind.CONFLICT, dedup=f"conf:{item.key}:{item.updated_at.isoformat()}", item=item))
    return out
