"""Free-text answers to our prompts. One pending prompt per user in ctx.user_data["await"]."""

import re
from collections.abc import Awaitable, Callable

from telegram.error import TelegramError

from app.bot import access
from app.i18n import pick_lang, t

InputHandler = Callable[..., Awaitable[None]]
_HANDLERS: dict[str, InputHandler] = {}
_TOKEN_SHAPE = re.compile(
    r"^\s*(?:glpat-|gh[pousr]_|github_pat_)[A-Za-z0-9_-]{10,}\s*$"
    r"|^\s*[A-Za-z0-9_-]{20,}\s*$"
)


def register(kind: str, handler: InputHandler) -> None:
    _HANDLERS[kind] = handler


def expect(ctx, kind: str, **data) -> None:
    ctx.user_data["await"] = {"kind": kind, **data}


def looks_like_token(text: str | None) -> bool:
    return bool(_TOKEN_SHAPE.match(text or ""))


async def delete_quietly(message) -> bool:
    try:
        await message.delete()
        return True
    except TelegramError:
        return False


async def on_text(update, ctx) -> None:
    pending = ctx.user_data.pop("await", None)
    message = update.message
    text = message.text or ""
    if (pending is None or pending["kind"] != "token") and looks_like_token(text):
        await delete_quietly(message)
        await update.effective_chat.send_message(
            t(pick_lang(update.effective_user.language_code), "token.unexpected")
        )
        return
    user = await access.current_user(update, ctx)
    if user is None:
        return
    handler = _HANDLERS.get(pending["kind"]) if pending else None
    if handler is None:
        await message.reply_text(t(user.lang, "hint.unknown_text"))
        return
    await handler(update, ctx, user, pending)
