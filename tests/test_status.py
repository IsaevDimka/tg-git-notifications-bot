from datetime import timedelta

from app.bot import status
from app.models import Ball, Kind, Role
from app.storage.store import Watched
from app.timeutil import iso
from tests.factories import NOW, item
from tests.tg import make_ctx, message_update, texts


async def test_status_shows_accounts_counts_and_queue(store):
    await store.register_user(1, 1, "me", "en", False, 180, NOW)
    acc = await store.add_account(1, "gitlab", "gitlab.example.com", "me", "s", NOW)
    await store.update_account(acc.id, last_ok_at=iso(NOW - timedelta(minutes=2)), rate_remaining=1987)
    for iid, role in ((1, Role.REVIEWER), (2, Role.REVIEWER), (3, Role.AUTHOR), (4, Role.WATCHER)):
        it = item(role, iid)
        await store.put_watched(Watched(acc.id, it.key, role, it, it.updated_at, {}, Ball.ME, NOW))
    text = status.render_status(await store.get_user(1), await store.accounts_for(1),
                                await store.watched_for_user(1), pending=3, lang="en", now=NOW)
    assert "gitlab.example.com" in text and "@me" in text
    assert "to review: 2" in text and "mine: 1" in text and "following: 1" in text
    assert "1987" in text and "2 min" in text and "waiting to be sent: 3" in text


async def test_cmd_test_samples_every_kind_within_telegram_limits(store):
    await store.register_user(1, 1, "me", "en", False, 180, NOW)
    ctx = make_ctx(store)
    upd = message_update("/test", lang="en")
    await status.cmd_test(upd, ctx)
    sent = texts(upd.effective_chat.send_message)
    assert 1 <= len(sent) <= 4 and all(len(s) < 4096 for s in sent)
    joined = "\n".join(sent)
    for kind in Kind:
        assert status.sample_marker(kind, "en") in joined, kind


async def test_cmd_status_replies(store):
    await store.register_user(1, 1, "me", "en", False, 180, NOW)
    ctx = make_ctx(store)
    upd = message_update("/status", lang="en")
    await status.cmd_status(upd, ctx)
    assert "No accounts" in texts(upd.effective_chat.send_message)[0]
