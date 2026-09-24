"""Free-text answers to our prompts. One pending prompt per user in ctx.user_data["await"]."""

import re
from collections.abc import Awaitable, Callable

from telegram.error import TelegramError

from app.bot import access
from app.i18n import pick_lang, t

InputHandler = Callable[..., Awaitable[None]]
_HANDLERS: dict[str, InputHandler] = {}
_PREFIXED_TOKEN = re.compile(r"(?:glpat-|gh[pousr]_|github_pat_)[A-Za-z0-9_-]{10,}")  # anywhere in the text
_BARE_TOKEN = re.compile(r"^\s*[A-Za-z0-9_-]{20,}\s*$")  # old-style GitLab token sent on its own


def register(kind: str, handler: InputHandler) -> None:
    _HANDLERS[kind] = handler


def expect(ctx, kind: str, **data) -> None:
    ctx.user_data["await"] = {"kind": kind, **data}


def looks_like_token(text: str | None) -> bool:
    text = text or ""
    return bool(_PREFIXED_TOKEN.search(text) or _BARE_TOKEN.match(text))


async def delete_quietly(message) -> bool:
    try:
        await message.delete()
        return True
    except TelegramError:
        return False


async def on_text(update, ctx) -> None:
    message = update.message
    if message is None:  # edited messages etc. — never consume a pending prompt for them
        return
    pending = ctx.user_data.pop("await", None)
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
