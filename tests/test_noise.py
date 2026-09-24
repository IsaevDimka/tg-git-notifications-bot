from dataclasses import replace

import pytest

from app.core.noise import is_bot, is_noise
from app.models import Event, Kind, Role
from app.storage.store import User
from tests.factories import item, note

USER = User(
    tg_id=1, chat_id=1, username="me", lang="en", tz="UTC", status="active", is_admin=False, poll_interval=180,
    quiet_enabled=False, quiet_from="22:00", quiet_to="09:00", quiet_weekends=False, muted_kinds=frozenset(),
)


@pytest.mark.parametrize(
    ("name", "bot"),
    [
        ("renovate", True), ("renovate-bot", True), ("dependabot[bot]", True), ("github-actions[bot]", True),
        ("project_42_bot_a1b2c3", True), ("group_7_bot", True), ("ci_bot", True),
        ("alice", False), ("robert", False), ("bottle", False), ("d.isaev", False),
    ],
)
def test_is_bot(name, bot):
    assert is_bot(name) is bot


def comment(actor="alice", **item_kw):
    return Event(Kind.NEW_COMMENT, dedup="d", item=item(**item_kw), actor=actor, note=note(1, actor))


def test_noise_rules():
    assert is_noise(USER, comment(actor="renovate"))
    assert not is_noise(replace(USER, mute_bots=False), comment(actor="renovate"))
    assert is_noise(USER, comment(draft=True))
    assert not is_noise(replace(USER, mute_drafts=False), comment(draft=True))
    assert not is_noise(USER, comment(role=Role.AUTHOR, draft=True, author="me"))
    assert is_noise(replace(USER, muted_projects=frozenset({"g/app"})), comment())
    assert not is_noise(USER, comment())
    assert not is_noise(USER, Event(Kind.TOKEN_BROKEN, dedup="t", title="gitlab.example.com"))
