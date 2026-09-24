"""Who may use this instance: first /start becomes admin; everyone else waits for an admin."""

from telegram import InlineKeyboardMarkup
from telegram.constants import ParseMode

from app import timeutil
from app.bot import deps
from app.bot.keyboards import btn, connect_keyboard
from app.core.render import esc
from app.i18n import pick_lang, t
from app.storage.store import Store, User


async def register(update, ctx) -> tuple[User, bool]:
    tg = update.effective_user
    cfg, st = deps.cfg(ctx), deps.store(ctx)
    user, created = await st.register_user(
        tg.id,
        update.effective_chat.id,
        tg.username or tg.full_name or str(tg.id),
        pick_lang(tg.language_code),
        tg.id in cfg.allowed_users,
        cfg.default_poll_interval,
        timeutil.now(),
    )
    if user.status == "blocked":  # they unblocked the bot and pressed /start again
        await st.update_user(tg.id, status="active")
        user = await st.get_user(tg.id)
    return user, created


async def current_user(update, ctx) -> User | None:
    tg = update.effective_user
    user = await deps.store(ctx).get_user(tg.id)
    chat = update.effective_chat
    if user is None:
        await chat.send_message(t(pick_lang(tg.language_code), "access.need_start"))
        return None
    if user.status == "pending":
        await chat.send_message(t(user.lang, "access.pending"))
        return None
    if user.status != "active":
        return None
    return user


async def notify_admins(bot, st: Store, user: User) -> None:
    for admin in await st.admins():
        markup = InlineKeyboardMarkup(
            [[
                btn(t(admin.lang, "btn.admit"), f"adm:ok:{user.tg_id}"),
                btn(t(admin.lang, "btn.reject"), f"adm:no:{user.tg_id}"),
            ]]
        )
        await bot.send_message(
            admin.chat_id,
            t(admin.lang, "admin.request", name=esc(user.username), tg_id=user.tg_id),
            parse_mode=ParseMode.HTML,
            reply_markup=markup,
        )


async def cb_admin(update, ctx) -> None:
    q = update.callback_query
    await q.answer()
    st = deps.store(ctx)
    admin = await st.get_user(update.effective_user.id)
    if admin is None or not admin.is_admin or admin.status != "active":
        return
    _, verdict, raw_id = q.data.split(":")
    target = await st.get_user(int(raw_id))
    if target is None:
        return
    admitted = verdict == "ok"
    await st.update_user(target.tg_id, status="active" if admitted else "rejected")
    await q.edit_message_text(
        t(admin.lang, "admin.approved" if admitted else "admin.rejected", name=esc(target.username)),
        parse_mode=ParseMode.HTML,
    )
    if admitted:
        await ctx.bot.send_message(
            target.chat_id, t(target.lang, "access.approved"), reply_markup=connect_keyboard(target.lang)
        )
    else:
        await ctx.bot.send_message(target.chat_id, t(target.lang, "access.rejected"))
