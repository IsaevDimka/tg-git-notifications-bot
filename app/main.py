"""Entry point: wire handlers, start the poll and delivery loops, run Telegram long polling."""

import asyncio
import logging

import httpx
from telegram import Update
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
    filters,
)

from app import timeutil
from app.bot import access, actions, inputs, onboarding, settings, status, views, watch
from app.bot.commands import bot_commands
from app.bot.daily import send_daily
from app.config import Config, ensure_writable, load_config
from app.core.delivery import deliver_all
from app.core.poller import poll_due
from app.crypto import TokenBox
from app.health import safe_beat
from app.logs import setup_logging
from app.providers import make_provider
from app.storage.store import Store

log = logging.getLogger("app")
POLL_TICK = 15
DELIVERY_TICK = 20
COMMAND_HANDLERS = (
    ("start", onboarding.cmd_start),
    ("help", onboarding.cmd_help),
    ("mr", views.cmd_mr),
    ("my", views.cmd_my),
    ("inbox", views.cmd_inbox),
    ("settings", settings.cmd_settings),
    ("lang", settings.cmd_lang),
    ("mute", settings.cmd_mute),
    ("watch", watch.cmd_watch),
    ("status", status.cmd_status),
    ("invite", onboarding.cmd_invite),
    ("test", status.cmd_test),
    ("accounts", settings.cmd_accounts),
)
CALLBACKS = (
    ("^ob:", onboarding.cb_onboard),
    ("^adm:", access.cb_admin),
    ("^a:", actions.cb_action),
    ("^mr:", views.cb_mr),
    ("^ib:", views.cb_inbox),
    ("^st:", settings.cb_settings),
    ("^acc:", settings.cb_accounts),
    ("^wt:", watch.cb_watch),
)


async def set_commands(bot) -> None:
    await bot.set_my_commands(bot_commands("en"))
    await bot.set_my_commands(bot_commands("ru"), language_code="ru")


async def _poll_loop(app: Application) -> None:
    store, box, cfg = app.bot_data["store"], app.bot_data["box"], app.bot_data["cfg"]
    make = app.bot_data["make_provider"]
    while True:
        try:
            await poll_due(store, lambda acc: make(acc.kind, acc.host, box.open(acc.token_enc)), timeutil.now())
        except Exception:
            log.exception("poll cycle failed")
        safe_beat(cfg.data_dir)
        await asyncio.sleep(POLL_TICK)


async def _delivery_loop(app: Application) -> None:
    store = app.bot_data["store"]
    while True:
        try:
            await deliver_all(app.bot, store, timeutil.now(), app.bot_data["cfg"].urgent_labels)
            await send_daily(app.bot, store, timeutil.now())
        except Exception:
            log.exception("delivery cycle failed")
        await asyncio.sleep(DELIVERY_TICK)


def make_http() -> httpx.AsyncClient:
    # Redirects are never followed: GitLab's PRIVATE-TOKEN header would be re-sent to the new host,
    # and a redirected POST silently becomes a GET that "succeeds".
    return httpx.AsyncClient(timeout=20, follow_redirects=False)


async def _post_init(app: Application) -> None:
    cfg: Config = app.bot_data["cfg"]
    http = make_http()
    app.bot_data.update(
        http=http,
        box=TokenBox.from_dir(cfg.data_dir),
        store=await Store.open(cfg.data_dir / "bot.db"),
        make_provider=lambda kind, host, token: make_provider(kind, host, token, http),
    )
    await set_commands(app.bot)
    app.bot_data["tasks"] = [asyncio.create_task(_poll_loop(app)), asyncio.create_task(_delivery_loop(app))]
    log.info("started, data in %s", cfg.data_dir)


async def _post_shutdown(app: Application) -> None:
    for task in app.bot_data.get("tasks", []):
        task.cancel()
    if http := app.bot_data.get("http"):
        await http.aclose()
    if store := app.bot_data.get("store"):
        await store.close()


async def _on_error(update, ctx) -> None:
    log.error("handler failed", exc_info=ctx.error)


def build_app(cfg: Config) -> Application:
    app = (
        ApplicationBuilder()
        .token(cfg.telegram_token)
        .post_init(_post_init)
        .post_shutdown(_post_shutdown)
        .build()
    )
    app.bot_data["cfg"] = cfg
    for name, handler in COMMAND_HANDLERS:
        # /start explains itself in groups; everything else shows private MR data → private chats only
        only = None if name == "start" else filters.ChatType.PRIVATE
        app.add_handler(CommandHandler(name, handler, filters=only))
    for pattern, handler in CALLBACKS:
        app.add_handler(CallbackQueryHandler(handler, pattern=pattern))
    app.add_handler(
        MessageHandler(
            filters.UpdateType.MESSAGE & filters.TEXT & ~filters.COMMAND & filters.ChatType.PRIVATE, inputs.on_text
        )
    )
    app.add_error_handler(_on_error)
    return app


def main() -> None:
    cfg = load_config()
    setup_logging(cfg.log_level)
    ensure_writable(cfg.data_dir)
    build_app(cfg).run_polling(allowed_updates=Update.ALL_TYPES)
