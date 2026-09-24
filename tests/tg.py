"""Minimal stand-ins for python-telegram-bot Update / CallbackContext objects."""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from cryptography.fernet import Fernet

from app.config import Config
from app.crypto import TokenBox


def make_cfg(**overrides) -> Config:
    base = dict(
        telegram_token="1:test",
        data_dir=Path("/tmp/unused"),
        allowed_users=frozenset(),
        default_poll_interval=180,
        min_poll_interval=60,
        log_level="INFO",
    )
    return Config(**{**base, **overrides})


def make_ctx(store, provider=None, cfg: Config | None = None):
    bot = MagicMock()
    bot.send_message = AsyncMock()
    return SimpleNamespace(
        bot=bot,
        user_data={},
        bot_data={
            "store": store,
            "cfg": cfg or make_cfg(),
            "box": TokenBox(Fernet.generate_key()),
            "make_provider": lambda kind, host, token: provider,
        },
    )


def _people(user_id: int, username: str, lang: str, chat_type: str):
    user = SimpleNamespace(id=user_id, username=username, full_name=username.title(), language_code=lang)
    chat = SimpleNamespace(id=user_id, type=chat_type, send_message=AsyncMock())
    return user, chat


def message_update(text: str, user_id: int = 1, username: str = "dimka", lang: str = "ru",
                   chat_type: str = "private"):
    user, chat = _people(user_id, username, lang, chat_type)
    msg = SimpleNamespace(text=text, reply_text=AsyncMock(), delete=AsyncMock(return_value=True))
    return SimpleNamespace(message=msg, effective_message=msg, effective_user=user, effective_chat=chat,
                           callback_query=None)


def callback_update(data: str, user_id: int = 1, username: str = "dimka", lang: str = "ru"):
    user, chat = _people(user_id, username, lang, "private")
    query = SimpleNamespace(data=data, from_user=user, answer=AsyncMock(), edit_message_text=AsyncMock(),
                            edit_message_reply_markup=AsyncMock())
    return SimpleNamespace(message=None, effective_message=None, effective_user=user, effective_chat=chat,
                           callback_query=query)


def texts(mock) -> list[str]:
    """The text (first positional arg) of every await of chat.send_message / reply_text / edit_message_text."""
    return [c.args[0] for c in mock.await_args_list]


def bot_texts(bot) -> list[tuple[int, str]]:
    return [(c.args[0], c.args[1]) for c in bot.send_message.await_args_list]


def callbacks(markup) -> list[str]:
    return [b.callback_data for row in markup.inline_keyboard for b in row if b.callback_data]
