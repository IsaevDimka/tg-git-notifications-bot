import json

from app.models import (
    Event,
    Kind,
    Role,
    event_from_payload,
    event_to_payload,
    item_from_dict,
    item_to_dict,
    mentions_user,
)
from tests.factories import NOW, item, note


def test_item_key_ref_and_short_project():
    gl = item(Role.REVIEWER, 42)
    gh = item(Role.AUTHOR, 7, kind="github", host="github.com")
    assert gl.key == "gitlab.example.com:7:42"
    assert gl.ref == "!42"
    assert gh.ref == "#7"
    assert gl.project_short == "app"


def test_item_roundtrip_through_json():
    original = item(Role.AUTHOR, 3, draft=True)
    restored = item_from_dict(json.loads(json.dumps(item_to_dict(original))))
    assert restored == original


def test_event_roundtrip_through_json():
    ev = Event(
        Kind.REPLY_TO_ME,
        dedup="note:h:9",
        item=item(),
        actor="alice",
        note=note(9, "alice", "fixed"),
        quote=note(8, "me", "why?"),
        thread_id="t1",
        resolvable=True,
        actors=("bob",),
        title="T",
        since=NOW,
    )
    payload = json.loads(json.dumps(event_to_payload(ev)))
    assert event_from_payload("reply_to_me", "note:h:9", payload) == ev


def test_mention_event_has_synthetic_item_key():
    ev = Event(Kind.MENTION, dedup="note:h:5", note=note(5, "bob"))
    assert ev.item_key == "mention:note:h:5"


def test_mentions_user():
    assert mentions_user("ping @dimka please", "dimka")
    assert mentions_user("thanks @Dimka.", "dimka")
    assert mentions_user("(@dimka)", "dimka")
    assert not mentions_user("@dimkaa", "dimka")
    assert not mentions_user("mail dimka@corp.com", "dimka")
    assert not mentions_user("@dimka.dev", "dimka")


def test_labels_roundtrip_and_old_rows_without_labels():
    labelled = item(Role.REVIEWER, 2, labels=("Blocker", "backend"))
    assert item_from_dict(json.loads(json.dumps(item_to_dict(labelled)))) == labelled
    legacy = item_to_dict(item())
    legacy.pop("labels")
    assert item_from_dict(legacy).labels == ()
