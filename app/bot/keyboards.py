"""Inline keyboards shared by several handlers."""

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from app.i18n import t

TZ_CHOICES = ("Europe/Moscow", "Europe/Kyiv", "Europe/Berlin", "Asia/Tbilisi", "Asia/Almaty", "UTC")


def btn(text: str, data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text, callback_data=data)


def connect_keyboard(lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [btn(t(lang, "btn.gitlab_com"), "ob:gl"), btn(t(lang, "btn.gitlab_self"), "ob:glh")],
            [btn(t(lang, "btn.github"), "ob:gh")],
        ]
    )


def tz_keyboard(lang: str) -> InlineKeyboardMarkup:
    buttons = [btn(zone, f"ob:tz:{zone}") for zone in TZ_CHOICES]
    rows = [buttons[i : i + 2] for i in range(0, len(buttons), 2)]
    rows.append([btn(t(lang, "btn.tz_other"), "ob:tzo")])
    return InlineKeyboardMarkup(rows)
