from datetime import timedelta

from app.bot import team
from app.models import Ball, Event, Kind, Role
from app.storage.store import Watched
from tests.factories import NOW, item
from tests.tg import make_ctx, message_update, texts


def watched(iid, role, ball, account_id=1, snapshot=None, since=NOW - timedelta(days=2), **kw):
    it = item(role, iid, **kw)
    return Watched(account_id, it.key, role, it, it.updated_at, snapshot or {}, ball, since)


def test_render_load_counts_only_no_titles():
    mine = [
        watched(1, Role.AUTHOR, Ball.THEM, snapshot={"pending_reviewers": ["ivan", "petr"]}, author="me",
                title="Secret payroll change"),
        watched(2, Role.AUTHOR, Ball.THEM, snapshot={"pending_reviewers": ["ivan"]}, author="me"),
    ]
    peers = [("ivan", [watched(5, Role.REVIEWER, Ball.ME), watched(6, Role.REVIEWER, Ball.ME),
                       watched(7, Role.REVIEWER, Ball.NONE)]),
             ("anna", [])]
    text = team.render_load(mine, peers, "en", NOW)
    assert "@ivan — 2" in text and "@petr — 1" in text  # my MRs waiting on them
    assert "@ivan — 2 (oldest 2 d)" in text and "@anna — 0" in text  # team section
    assert "Secret payroll" not in text


def test_render_load_empty():
    assert "Nothing" in team.render_load([], [], "en", NOW)


def test_render_stats():
    counts = {"review_requested": 5, "rereview": 2, "reply_to_me": 7, "mention": 1, "merged": 3, "approved": 6,
              "changes_requested": 1}
    ws = [watched(1, Role.REVIEWER, Ball.ME, since=NOW - timedelta(days=3)),
          watched(2, Role.AUTHOR, Ball.THEM, author="me"), watched(3, Role.AUTHOR, Ball.ME, author="me")]
    text = team.render_stats(counts, ws, 7, "en", NOW)
    assert "last 7 days" in text
    assert "Review requests: 5" in text and "re-reviews: 2" in text
    assert "merged 3" in text and "approvals 6" in text
    assert "waiting for your review: 1 (oldest 3 d)" in text and "open: 2" in text


async def _users(store):
    await store.register_user(1, 1, "me", "en", False, 180, NOW)
    await store.register_user(2, 2, "ivan-tg", "en", True, 180, NOW)
    await store.register_user(3, 3, "shy-tg", "en", True, 180, NOW)
    await store.update_user(3, share_load=False)
    me = await store.add_account(1, "gitlab", "gitlab.example.com", "me", "s", NOW)
    ivan = await store.add_account(2, "gitlab", "gitlab.example.com", "ivan", "s", NOW)
    shy = await store.add_account(3, "gitlab", "gitlab.example.com", "shy", "s", NOW)
    return me, ivan, shy


async def test_cmd_load_respects_opt_out(store):
    me, ivan, shy = await _users(store)
    await store.put_watched(watched(5, Role.REVIEWER, Ball.ME, account_id=ivan.id))
    await store.put_watched(watched(6, Role.REVIEWER, Ball.ME, account_id=shy.id))
    ctx = make_ctx(store)
    upd = message_update("/load", lang="en")
    await team.cmd_load(upd, ctx)
    text = texts(upd.effective_chat.send_message)[0]
    assert "@ivan — 1" in text and "@shy" not in text


async def test_cmd_stats_with_days(store):
    me, _, _ = await _users(store)
    await store.add_event(1, me.id, Event(Kind.MERGED, dedup="m", item=item()), NOW)
    ctx = make_ctx(store)
    ctx.args = ["30"]
    upd = message_update("/stats 30", lang="en")
    await team.cmd_stats(upd, ctx)
    text = texts(upd.effective_chat.send_message)[0]
    assert "last 30 days" in text and "merged 1" in text
