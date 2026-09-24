"""The bot's command menu, shared by startup and the per-chat /lang switch."""

from telegram import BotCommand

from app.i18n import t

COMMANDS = ("inbox", "mr", "my", "watch", "mute", "accounts", "settings", "lang", "help", "start")


def bot_commands(lang: str) -> list[BotCommand]:
    return [BotCommand(c, t(lang, f"cmd.{c}")) for c in COMMANDS]
