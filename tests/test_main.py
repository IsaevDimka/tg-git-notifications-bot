import os
import time

from telegram.ext import CallbackQueryHandler, CommandHandler, MessageHandler

from app import health
from app.bot.commands import COMMANDS
from app.i18n import t
from app.main import build_app
from tests.tg import make_cfg

# One sample of every callback_data family the bot emits (keyboards in Tasks 8–12).
SAMPLE_CALLBACKS = [
    "ob:gl", "ob:tz:UTC", "adm:ok:1", "a:read:1", "a:sz:1:2h", "mr:rev", "ib:all", "ib:r:0",
    "st:k:mention", "st:lg:ru", "st:dg", "acc:add", "acc:rm!:1", "wt:rm:0",
]


def _handlers(app):
    return [h for group in app.handlers.values() for h in group]


def test_every_command_is_registered():
    app = build_app(make_cfg(telegram_token="123456:TEST"))
    commands = {c for h in _handlers(app) if isinstance(h, CommandHandler) for c in h.commands}
    assert set(COMMANDS) <= commands


def test_every_callback_family_has_exactly_one_handler():
    app = build_app(make_cfg(telegram_token="123456:TEST"))
    patterns = [h.pattern for h in _handlers(app) if isinstance(h, CallbackQueryHandler)]
    for data in SAMPLE_CALLBACKS:
        assert sum(1 for p in patterns if p.match(data)) == 1, data


def test_free_text_handler_registered():
    app = build_app(make_cfg(telegram_token="123456:TEST"))
    assert any(isinstance(h, MessageHandler) for h in _handlers(app))


def test_every_command_has_a_menu_description():
    for lang in ("ru", "en"):
        for command in COMMANDS:
            assert t(lang, f"cmd.{command}") != f"cmd.{command}"


def test_health_follows_heartbeat(tmp_path):
    assert not health.check(tmp_path)
    health.beat(tmp_path)
    assert health.check(tmp_path)
    stale = time.time() - 600
    os.utime(tmp_path / "heartbeat", (stale, stale))
    assert not health.check(tmp_path)


def test_http_client_does_not_follow_redirects():
    from app.main import make_http

    assert make_http().follow_redirects is False


def test_new_commands_present():
    assert {"my", "lang"} <= set(COMMANDS)


def test_heartbeat_failure_does_not_raise(tmp_path):
    missing = tmp_path / "gone"  # directory vanished / disk read-only
    assert health.safe_beat(missing) is False
    assert health.safe_beat(tmp_path) is True and health.check(tmp_path)


def _update(chat_type: str, *, edited: bool = False, text: str = "/mr"):
    from datetime import UTC, datetime

    from telegram import Chat, Message, MessageEntity, Update, User

    entities = [MessageEntity(MessageEntity.BOT_COMMAND, 0, len(text))] if text.startswith("/") else []
    msg = Message(
        message_id=1,
        date=datetime.now(UTC),
        chat=Chat(id=-5 if chat_type != "private" else 5, type=chat_type),
        from_user=User(id=5, first_name="D", is_bot=False),
        text=text,
        entities=entities,
    )
    return Update(update_id=1, edited_message=msg) if edited else Update(update_id=1, message=msg)


def test_private_data_commands_ignore_group_chats():
    app = build_app(make_cfg(telegram_token="123456:TEST"))
    commands = [h for h in _handlers(app) if isinstance(h, CommandHandler) and "start" not in h.commands]
    assert commands
    for h in commands:
        assert not h.filters.check_update(_update("group")), h.commands
        assert h.filters.check_update(_update("private")), h.commands


def test_edited_messages_do_not_reach_the_text_handler():
    app = build_app(make_cfg(telegram_token="123456:TEST"))
    [text_handler] = [h for h in _handlers(app) if isinstance(h, MessageHandler)]
    assert text_handler.filters.check_update(_update("private", text="hello"))
    assert not text_handler.filters.check_update(_update("private", edited=True, text="hello"))
