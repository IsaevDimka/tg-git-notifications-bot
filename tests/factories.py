from datetime import UTC, datetime

from app.models import Details, Note, ReviewItem, Role, Thread
from app.timeutil import parse_ts

NOW = datetime(2026, 9, 24, 12, 0, tzinfo=UTC)


def item(
    role: Role = Role.REVIEWER,
    iid: int = 1,
    *,
    author: str = "alice",
    updated: str = "2026-09-24T10:00:00Z",
    created: str = "2026-09-20T10:00:00Z",
    kind: str = "gitlab",
    host: str = "gitlab.example.com",
    draft: bool = False,
    state: str = "opened",
    title: str = "Add login",
) -> ReviewItem:
    return ReviewItem(
        kind=kind,
        host=host,
        project_id="7",
        iid=iid,
        title=title,
        url=f"https://{host}/g/app/-/merge_requests/{iid}",
        author=author,
        project="g/app",
        role=role,
        created_at=parse_ts(created),
        updated_at=parse_ts(updated),
        state=state,
        draft=draft,
    )


def note(id: int | str, author: str, body: str = "looks fine", at: str = "2026-09-24T10:00:00Z") -> Note:
    return Note(id=str(id), author=author, body=body, created_at=parse_ts(at), url=f"https://x/mr#note_{id}")


def thread(id: str, *notes: Note, resolvable: bool = True, resolved: bool = False) -> Thread:
    return Thread(id=id, resolvable=resolvable, resolved=resolved, notes=tuple(notes))


def details(
    *threads: Thread,
    approved=(),
    cr=(),
    pending=(),
    conflicts: bool = False,
    approvals_left: int | None = None,
    state: str = "opened",
    pipeline: str | None = None,
    head_sha: str | None = None,
) -> Details:
    return Details(
        state=state,
        threads=tuple(threads),
        approved_by=frozenset(approved),
        changes_requested_by=frozenset(cr),
        pending_reviewers=tuple(pending),
        has_conflicts=conflicts,
        approvals_left=approvals_left,
        pipeline=pipeline,
        head_sha=head_sha,
    )
