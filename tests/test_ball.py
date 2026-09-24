from app.core.ball import awaits_me, whose_ball
from app.models import Ball, Role
from tests.factories import details, item, note, thread

ME = "me"


def test_reviewer_untouched_mr_is_my_move():
    assert whose_ball(item(Role.REVIEWER), details(), ME) is Ball.ME


def test_reviewer_after_my_comment_it_is_theirs():
    d = details(thread("t1", note(1, ME, "why?")))
    assert whose_ball(item(Role.REVIEWER), d, ME) is Ball.THEM


def test_reviewer_author_replied_in_my_thread_is_mine_again():
    d = details(thread("t1", note(1, ME, "why?"), note(2, "alice", "because")))
    assert whose_ball(item(Role.REVIEWER), d, ME) is Ball.ME


def test_reviewer_approved_and_nothing_open_is_nobodys():
    d = details(thread("t1", note(1, ME), note(2, "alice"), resolved=True), approved=[ME])
    assert whose_ball(item(Role.REVIEWER), d, ME) is Ball.NONE


def test_reviewer_approved_but_author_answered_open_thread_is_mine():
    d = details(thread("t1", note(1, ME), note(2, "alice")), approved=[ME])
    assert whose_ball(item(Role.REVIEWER), d, ME) is Ball.ME


def test_reviewer_requested_changes_waits_for_author():
    assert whose_ball(item(Role.REVIEWER), details(cr=[ME]), ME) is Ball.THEM


def test_reviewer_draft_is_nobodys():
    assert whose_ball(item(Role.REVIEWER, draft=True), details(), ME) is Ball.NONE


def test_author_waiting_for_reviewers():
    assert whose_ball(item(Role.AUTHOR, author=ME), details(pending=["bob"]), ME) is Ball.THEM


def test_author_conflict_is_mine():
    assert whose_ball(item(Role.AUTHOR, author=ME), details(conflicts=True), ME) is Ball.ME


def test_author_open_thread_from_reviewer_is_mine():
    d = details(thread("t1", note(1, "bob", "rename this")))
    assert whose_ball(item(Role.AUTHOR, author=ME), d, ME) is Ball.ME


def test_author_answered_thread_waits_for_reviewer():
    d = details(thread("t1", note(1, "bob"), note(2, ME, "done")))
    assert whose_ball(item(Role.AUTHOR, author=ME), d, ME) is Ball.THEM


def test_author_non_resolvable_comment_does_not_block():
    d = details(thread("ic1", note(1, "bob", "nice"), resolvable=False))
    assert whose_ball(item(Role.AUTHOR, author=ME), d, ME) is Ball.THEM


def test_author_all_approvals_collected_is_mine_to_merge():
    d = details(approved=["bob"], approvals_left=0)
    assert whose_ball(item(Role.AUTHOR, author=ME), d, ME) is Ball.ME


def test_author_no_approval_rules_is_not_ready_to_merge():
    assert whose_ball(item(Role.AUTHOR, author=ME), details(approvals_left=0), ME) is Ball.THEM


def test_closed_or_merged_is_nobodys():
    assert whose_ball(item(Role.AUTHOR, author=ME), details(state="merged"), ME) is Ball.NONE


def test_awaits_me_requires_participation_and_open_thread():
    assert awaits_me(thread("t", note(1, ME), note(2, "a")), ME)
    assert not awaits_me(thread("t", note(1, "a"), note(2, "b")), ME)
    assert not awaits_me(thread("t", note(1, ME), note(2, "a"), resolved=True), ME)
    assert not awaits_me(thread("t", note(1, "a"), note(2, ME)), ME)


def test_author_changes_requested_without_threads_is_mine():
    assert whose_ball(item(Role.AUTHOR, author=ME), details(cr=["dave"]), ME) is Ball.ME


def test_author_after_rerequest_waits_for_reviewer():
    assert whose_ball(item(Role.AUTHOR, author=ME), details(cr=["dave"], pending=["dave"]), ME) is Ball.THEM


def test_reviewer_rerequested_is_my_move_even_after_taking_part():
    d = details(thread("t1", note(1, "alice"), note(2, ME)), cr=[ME], pending=[ME])
    assert whose_ball(item(Role.REVIEWER), d, ME) is Ball.ME


def test_watcher_never_holds_the_ball():
    assert whose_ball(item(Role.WATCHER), details(), ME) is Ball.NONE
