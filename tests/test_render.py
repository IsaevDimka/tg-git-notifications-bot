from datetime import timedelta

import pytest

from app.core.render import BODY_LIMIT, DIGEST_MAX, fmt_age, keyboard, render, render_digest
from app.i18n import _catalog
from app.models import Event, Kind, Role
from tests.factories import NOW, item, note


def _buttons(markup):
    return [b for row in markup.inline_keyboard for b in row]


def _data(markup):
    return [b.callback_data for b in _buttons(markup) if b.callback_data]


@pytest.mark.parametrize("lang", ["ru", "en"])
def test_every_kind_has_all_texts(lang):
    cat = _catalog(lang)
    for kind in Kind:
        for prefix in ("ev", "short", "kind"):
            assert f"{prefix}.{kind}" in cat, f"{prefix}.{kind}"


@pytest.mark.parametrize("lang", ["ru", "en"])
@pytest.mark.parametrize("kind", list(Kind))
def test_every_kind_renders(kind, lang):
    ev = Event(kind, dedup="d", item=item(), actor="alice", note=note(1, "alice"), quote=note(0, "me"),
               thread_id="t1", resolvable=True, actors=("bob",), since=NOW - timedelta(hours=30))
    text, markup = render(ev, 7, lang, NOW)
    assert "{" not in text
    assert "a:read:7" in _data(markup)


def test_reply_shows_quote_and_thread_buttons():
    ev = Event(Kind.REPLY_TO_ME, dedup="d", item=item(Role.REVIEWER, 42), actor="alice",
               note=note(2, "alice", "fixed"), quote=note(1, "me", "why?"), thread_id="t1", resolvable=True)
    text, markup = render(ev, 5, "en", NOW)
    assert "@alice" in text and "why?" in text and "fixed" in text and "!42" in text
    assert _data(markup) == ["a:reply:5", "a:resolve:5", "a:snooze:5", "a:read:5"]
    assert _buttons(markup)[2].url == "https://x/mr#note_2"


def test_review_request_has_approve_and_waiting_has_ping():
    rr = Event(Kind.REVIEW_REQUESTED, dedup="d", item=item(), actor="alice")
    wait = Event(Kind.WAITING_ON_REVIEWER, dedup="w", item=item(Role.AUTHOR), actors=("bob",),
                 since=NOW - timedelta(days=2))
    assert "a:approve:1" in _data(keyboard(rr, 1, "ru"))
    text, markup = render(wait, 2, "ru", NOW)
    assert "a:ping:2" in _data(markup) and "2 дн." in text and "@bob" in text


def test_mention_without_item_links_to_note():
    ev = Event(Kind.MENTION, dedup="d", actor="bob", note=note(9, "bob", "@me hi"), title="Bug in login")
    text, markup = render(ev, 3, "en", NOW)
    assert "Bug in login" in text
    assert "a:reply:3" not in _data(markup)


def test_render_escapes_and_truncates_hostile_body():
    body = "<script>alert(1)</script>" + "x" * 10_000
    ev = Event(Kind.NEW_COMMENT, dedup="d", item=item(title="<b>pwn</b>"), actor="eve",
               note=note(1, "eve", body), thread_id="t1")
    text, _ = render(ev, 1, "en", NOW)
    assert "<script>" not in text and "&lt;script&gt;" in text
    assert "<b>pwn</b>" not in text
    assert len(text) < BODY_LIMIT + 600
    assert len(text) < 4096


def test_empty_body_does_not_render_empty_blockquote():
    ev = Event(Kind.NEW_COMMENT, dedup="d", item=item(), actor="eve", note=note(1, "eve", ""), thread_id="t1")
    text, _ = render(ev, 1, "en", NOW)
    assert "<blockquote></blockquote>" not in text


def test_digest_is_bounded():
    events = [Event(Kind.NEW_COMMENT, dedup=str(i), item=item(iid=i), actor="a") for i in range(40)]
    text = render_digest(events, "en")
    assert "40 events" in text
    assert f"{40 - DIGEST_MAX} more" in text
    assert len(text) < 4096


def test_fmt_age():
    assert fmt_age(timedelta(minutes=5), "en") == "5 min"
    assert fmt_age(timedelta(hours=30), "en") == "30 h"
    assert fmt_age(timedelta(days=3), "ru") == "3 дн."
