import json

from app.core.diff import diff, snapshot
from app.models import Kind, Role
from tests.factories import details, item, note, thread

ME = "me"


def _kinds(events):
    return [e.kind for e in events]


def test_snapshot_is_json_serialisable_and_complete():
    d = details(thread("t1", note(1, "a"), note(2, "b")), thread("t2", note(3, "c"), resolved=True),
                approved=["x"], pending=["y"], pipeline="failed")
    snap = json.loads(json.dumps(snapshot(d)))
    assert snap["notes"] == ["1", "2", "3"]
    assert snap["resolved"] == {"t1": False, "t2": True}
    assert snap["approved_by"] == ["x"]
    assert snap["pending_reviewers"] == ["y"]
    assert snap["open_threads"] == 1
    assert snap["pipeline"] == "failed"
    assert snap["approvals_left"] is None


def test_new_comment_on_review_mr():
    old = snapshot(details())
    d = details(thread("t1", note(1, "alice", "please look at README")))
    [ev] = diff(old, d, item(Role.REVIEWER), ME)
    assert ev.kind is Kind.NEW_COMMENT
    assert ev.actor == "alice" and ev.thread_id == "t1" and ev.resolvable
    assert ev.dedup == "note:gitlab.example.com:1"


def test_reply_in_my_thread_quotes_my_last_note():
    old = snapshot(details(thread("t1", note(1, ME, "why?"), note(2, "alice", "because"), note(3, ME, "hmm"))))
    d = details(thread("t1", note(1, ME, "why?"), note(2, "alice", "because"), note(3, ME, "hmm"),
                       note(4, "alice", "fixed")))
    [ev] = diff(old, d, item(Role.REVIEWER), ME)
    assert ev.kind is Kind.REPLY_TO_ME
    assert ev.quote.id == "3" and ev.note.id == "4"


def test_mention_in_foreign_thread():
    old = snapshot(details())
    d = details(thread("t1", note(1, "bob", "@me can you check?")))
    assert _kinds(diff(old, d, item(Role.REVIEWER), ME)) == [Kind.MENTION]


def test_own_and_already_seen_notes_are_ignored():
    old = snapshot(details(thread("t1", note(1, "alice"))))
    d = details(thread("t1", note(1, "alice"), note(2, ME, "ok")))
    assert diff(old, d, item(Role.REVIEWER), ME) == []


def test_my_thread_resolved_and_reopened():
    base = [note(1, ME, "rename"), note(2, "alice", "done")]
    old = snapshot(details(thread("t1", *base)))
    resolved = diff(old, details(thread("t1", *base, resolved=True)), item(Role.REVIEWER), ME)
    assert _kinds(resolved) == [Kind.THREAD_RESOLVED]
    assert resolved[0].quote.id == "1"
    old2 = snapshot(details(thread("t1", *base, resolved=True)))
    assert _kinds(diff(old2, details(thread("t1", *base)), item(Role.REVIEWER), ME)) == [Kind.THREAD_REOPENED]


def test_resolution_of_foreign_or_brand_new_threads_is_ignored():
    old = snapshot(details(thread("t1", note(1, "bob"))))
    d = details(thread("t1", note(1, "bob"), resolved=True), thread("t2", note(2, ME), resolved=True))
    assert diff(old, d, item(Role.REVIEWER), ME) == []


def test_author_gets_approvals_changes_and_conflict():
    old = snapshot(details(approved=["bob"]))
    d = details(approved=["bob", "carol", ME], cr=["dave"], conflicts=True)
    events = diff(old, d, item(Role.AUTHOR, author=ME), ME)
    assert _kinds(events) == [Kind.APPROVED, Kind.CHANGES_REQUESTED, Kind.CONFLICT]
    assert events[0].actors == ("carol",)
    assert events[1].actors == ("dave",)


def test_conflict_reported_once():
    old = snapshot(details(conflicts=True))
    assert diff(old, details(conflicts=True), item(Role.AUTHOR, author=ME), ME) == []


def test_reviewer_does_not_get_approval_events():
    old = snapshot(details())
    assert diff(old, details(approved=["bob"]), item(Role.REVIEWER), ME) == []
