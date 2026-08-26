"""Админка: /broadcast с тест-режимом, /stats, /reload, /links."""
import asyncio
import logging

from aiogram import Bot, F, Router
from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramRetryAfter,
)
from aiogram.dispatcher.event.bases import SkipHandler
from aiogram.filters import Command, CommandObject, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

import audience
import bonuses
import db
import gate as gate_mod
import relay
from config import ADMIN_IDS, BROADCAST_SLEEP, GEOS, ready_geos

router = Router()
log = logging.getLogger(__name__)

SAMPLE_SIZE = 10


class Broadcast(StatesGroup):
    choosing = State()
    waiting_message = State()
    confirming = State()


def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


# --------------------------------------------------------------------------
# Клавиатуры админки
# --------------------------------------------------------------------------
def confirm_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🧪 Test to myself", callback_data="bctest")],
            [
                InlineKeyboardButton(
                    text=f"🎯 Test on {SAMPLE_SIZE} users", callback_data="bcsample"
                )
            ],
            [InlineKeyboardButton(text="✏️ Rewrite", callback_data="bcredo")],
            [
                InlineKeyboardButton(text="🚀 Send to everyone", callback_data="bcgo"),
                InlineKeyboardButton(text="✖️ Cancel", callback_data="bccancel"),
            ],
        ]
    )


# --------------------------------------------------------------------------
# Статистика и сервис
# --------------------------------------------------------------------------
@router.message(Command("stats"))
async def cmd_stats(message: Message):
    if not is_admin(message.from_user.id):
        return
    s = await db.stats()
    t = s["total"]
    total = t["total"] or 0
    passed = t["passed"] or 0
    conv = f"{passed / total * 100:.1f}%" if total else "—"

    lines = [
        "<b>📊 Stats</b>",
        f"Started the bot: <b>{total}</b>",
        f"Passed the gate: <b>{passed}</b> ({conv})",
        f"Blocked the bot: <b>{t['blocked'] or 0}</b>",
        f"Last 24h: <b>{s['last_24h']}</b>",
        "",
        "<b>By GEO:</b>",
    ]
    for r in s["by_geo"]:
        g = GEOS.get(r["geo"] or "")
        name = f"{g.flag} {g.title}" if g else (r["geo"] or "—")
        lines.append(f"{name}: {r['total']} / passed {r['passed'] or 0}")

    rows = await db.breakdown()
    if rows:
        lines += ["", "<b>GEO × vertical:</b>"]
        for r in rows:
            geo = (r["geo"] or "—").upper()
            vert = r["vertical"] or "no vertical"
            lines.append(f"{geo} / {vert}: {r['total']} / passed {r['passed'] or 0}")

    if s["by_source"]:
        lines += ["", "<b>By source:</b>"]
        for r in s["by_source"]:
            lines.append(f"<code>{r['source']}</code>: {r['total']} / {r['passed'] or 0}")

    if s["top_bonuses"]:
        lines += ["", "<b>Top bonuses by clicks:</b>"]
        for r in s["top_bonuses"]:
            lines.append(f"{r['bonus_id']}: {r['clicks']} ({r['uniq']} unique)")

    await message.answer("\n".join(lines))


@router.message(Command("reload"))
async def cmd_reload(message: Message):
    """Перечитать bonuses.json без рестарта бота."""
    if not is_admin(message.from_user.id):
        return
    data = bonuses.load()
    total = sum(len(v) for v in data.values())
    parts = [f"{geo}: {len(items)}" for geo, items in data.items()]
    soon = bonuses.expiring_soon(7)
    text = f"♻️ Bonuses loaded: <b>{total}</b>\n" + "\n".join(parts)
    blank = bonuses.LAST_LOAD.get("blank", 0)
    if blank:
        text += (
            f"\n\n⚠️ Skipped <b>{blank}</b> offer(s) with no bonus terms — "
            "fill in the title to make them live."
        )
    if soon:
        text += "\n\n⏳ <b>Expiring within a week:</b>\n" + "\n".join(
            f"• {b.brand} — {b.title} (until {b.expires})" for b in soon
        )
    await message.answer(text)


@router.message(Command("check"))
async def cmd_check(message: Message, bot: Bot):
    """
    Диагностика гейта: проверяет каждый канал и показывает точную причину,
    если проверка подписки не работает.
    """
    if not is_admin(message.from_user.id):
        return

    me = await bot.get_me()
    lines = ["<b>🔧 Gate diagnostics</b>", f"Bot: @{me.username} (<code>{me.id}</code>)", ""]

    for g in GEOS.values():
        lines.append(f"<b>{g.flag} {g.title}</b>")
        if not g.is_gated:
            if g.is_ready:
                lines.append("  🔓 No gate — open access (GATE_FREE_GEOS)")
            else:
                lines.append("  ⚠️ No channels configured — this GEO is disabled")
            lines.append("")
            continue
        for ch in g.channels:
            lines.append(f"<code>{ch.chat_id}</code> — {ch.title}")

            # 1. Виден ли вообще чат
            try:
                chat = await bot.get_chat(ch.chat_id)
                lines.append(f"  chat: ✅ {chat.title} ({chat.type})")
            except Exception as e:  # noqa: BLE001
                lines.append(f"  chat: ❌ <code>{type(e).__name__}: {e}</code>")
                lines.append("  ⚠️ Wrong chat_id, or the bot is not in this channel")
                lines.append("")
                continue

            # 2. Админ ли бот в канале
            ok_bot, status_bot = await gate_mod.check_member(bot, ch.chat_id, me.id)
            if status_bot in ("administrator", "creator"):
                lines.append("  bot rights: ✅ admin")
            else:
                lines.append(f"  bot rights: ❌ <code>{status_bot}</code>")
                lines.append("  ⚠️ Make the bot an admin of this channel")

            # 3. Что API отвечает про тебя
            ok_you, status_you = await gate_mod.check_member(
                bot, ch.chat_id, message.from_user.id
            )
            mark = "✅" if ok_you else "❌"
            lines.append(f"  you: {mark} <code>{status_you}</code>")
            lines.append("")

    lines.append(
        "If <code>chat</code> fails — the chat_id is wrong.\n"
        "If <code>bot rights</code> is not admin — getChatMember is blocked "
        "and everyone gets rejected."
    )
    await message.answer("\n".join(lines))


@router.message(Command("links"))
async def cmd_links(message: Message, bot: Bot):
    """Готовые deep-link ссылки под рекламные кампании."""
    if not is_admin(message.from_user.id):
        return
    me = await bot.get_me()
    lines = ["<b>🔗 Links for ad campaigns</b>", ""]
    for g in ready_geos():
        lines.append(f"{g.flag} <b>{g.title}</b>")
        lines.append(f"<code>https://t.me/{me.username}?start={g.code}</code>")
        lines.append(
            f"<code>https://t.me/{me.username}?start={g.code}_fb_casino</code>"
        )
        lines.append(
            f"<code>https://t.me/{me.username}?start={g.code}_fb_betting</code>"
        )
        lines.append("")
    lines.append(
        "Everything after the GEO code goes into the <code>source</code> field — "
        "you can append a creative label, e.g. "
        "<code>lv_fb_casino_cr3</code>."
    )
    await message.answer("\n".join(lines))




# --------------------------------------------------------------------------
# Переписка с юзерами
# --------------------------------------------------------------------------
def fmt_user(u: dict) -> str:
    uname = f"@{u['username']}" if u.get("username") else "—"
    name = u.get("first_name") or "—"
    geo = (u.get("geo") or "—").upper()
    src = u.get("source") or "—"
    mark = "✅" if u.get("gate_passed") else "⛔️"
    if u.get("status") == "blocked":
        mark = "🚫"
    line = f"{mark} <b>{name}</b> {uname}\n    <code>{u['user_id']}</code> · {geo} · <code>{src}</code>"
    if u.get("msgs_in"):
        line += f" · 💬 {u['msgs_in']}"
    return line


@router.message(Command("inbox"))
async def cmd_inbox(message: Message):
    """Кто писал боту."""
    if not is_admin(message.from_user.id):
        return
    users = await db.recent_users(20, only_wrote=True)
    if not users:
        await message.answer("Nobody has messaged yet.")
        return
    lines = ["<b>📨 Who messaged the bot</b>", ""]
    lines += [fmt_user(u) for u in users]
    lines.append("")
    lines.append("Reply: <code>/msg ID text</code> or reply to a forwarded message")
    await message.answer("\n".join(lines))


@router.message(Command("users"))
async def cmd_users(message: Message, command: CommandObject):
    """Последние юзеры бота."""
    if not is_admin(message.from_user.id):
        return
    try:
        limit = min(int((command.args or "20").strip()), 50)
    except ValueError:
        limit = 20
    users = await db.recent_users(limit)
    if not users:
        await message.answer("The database is empty.")
        return
    lines = [f"<b>👥 Last {len(users)}</b>", ""]
    lines += [fmt_user(u) for u in users]
    await message.answer("\n".join(lines))


@router.message(Command("find"))
async def cmd_find(message: Message, command: CommandObject):
    """Поиск юзера по username, имени или ID."""
    if not is_admin(message.from_user.id):
        return
    q = (command.args or "").strip()
    if not q:
        await message.answer("Usage: <code>/find username</code> or <code>/find 12345</code>")
        return
    users = await db.find_users(q)
    if not users:
        await message.answer("Nothing found.")
        return
    lines = [f"<b>🔍 Found: {len(users)}</b>", ""]
    lines += [fmt_user(u) for u in users]
    await message.answer("\n".join(lines))


@router.message(Command("dialog"))
async def cmd_dialog(message: Message, command: CommandObject):
    """История переписки с юзером."""
    if not is_admin(message.from_user.id):
        return
    arg = (command.args or "").strip()
    if not arg.isdigit():
        await message.answer("Usage: <code>/dialog 12345678</code>")
        return
    uid = int(arg)
    rows = await db.get_dialog(uid, 30)
    if not rows:
        await message.answer("No conversation yet.")
        return
    lines = [f"<b>💬 Conversation with {uid}</b>", ""]
    for r in rows:
        arrow = "◀️" if r["direction"] == "in" else "▶️"
        stamp = (r["created_at"] or "")[:16].replace("T", " ")
        lines.append(f"{arrow} <i>{stamp}</i>\n{r['text'] or '[media]'}")
    await message.answer("\n\n".join(lines[:1] + lines[1:])[:4000])


@router.message(Command("msg"))
async def cmd_msg(message: Message, command: CommandObject, bot: Bot):
    """Написать юзеру напрямую: /msg 12345678 текст."""
    if not is_admin(message.from_user.id):
        return
    args = (command.args or "").strip().split(maxsplit=1)
    if len(args) < 2 or not args[0].isdigit():
        await message.answer("Usage: <code>/msg 12345678 Hello!</code>")
        return
    uid, text = int(args[0]), args[1]
    try:
        await bot.send_message(uid, text)
    except TelegramForbiddenError:
        await db.mark_blocked(uid)
        await message.answer("⛔️ User blocked the bot — marked in the database")
        return
    except Exception as e:  # noqa: BLE001
        await message.answer(f"❌ Not sent: {e}")
        return
    await db.log_message(uid, "out", text)
    await message.answer("✅ Sent")


@router.message(StateFilter(None), F.reply_to_message)
async def reply_to_user(message: Message, bot: Bot):
    """
    Reply на пересланное сообщение → уходит юзеру.

    StateFilter(None) — чтобы не перехватывать сообщения админа,
    когда он в процессе создания рассылки.
    """
    if not is_admin(message.from_user.id):
        raise SkipHandler
    uid = await db.get_relay(message.chat.id, message.reply_to_message.message_id)
    if not uid:
        raise SkipHandler  # reply не на пересланное — пусть обработают дальше
    ok, text = await relay.to_user(bot, message, uid)
    await message.reply(text)


# --------------------------------------------------------------------------
# Рассылка
# --------------------------------------------------------------------------
@router.message(Command("broadcast"))
async def cmd_broadcast(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    filters = audience.default_filters()
    await state.set_state(Broadcast.choosing)
    await state.update_data(filters=filters, sampled=[])
    text, kb = await audience.render(filters)
    await message.answer(text, reply_markup=kb)


@router.message(Command("audience"))
async def cmd_audience(message: Message):
    """Матрица сегментов без запуска рассылки."""
    if not is_admin(message.from_user.id):
        return
    rows = await db.breakdown()
    if not rows:
        await message.answer("The database is empty.")
        return
    lines = ["<b>🎯 Segments</b>", "", "<code>GEO  vertical    total   gate</code>"]
    for r in rows:
        geo = (r["geo"] or "—").upper()
        vert = r["vertical"] or "—"
        lines.append(
            f"<code>{geo:<4} {vert:<11} {r['total']:>5}  {r['passed'] or 0:>4}</code>"
        )
    await message.answer("\n".join(lines))


@router.callback_query(Broadcast.choosing, F.data.startswith("aud:"))
async def bc_filters(call: CallbackQuery, state: FSMContext):
    parts = call.data.split(":")
    action = parts[1]
    value = parts[2] if len(parts) > 2 else None
    data = await state.get_data()
    filters = data.get("filters") or audience.default_filters()

    if action == "cancel":
        await state.clear()
        await call.message.edit_text("Cancelled.")
        await call.answer()
        return

    if action == "next":
        size = await db.count_audience(filters)
        if not size:
            await call.answer("No one matches these filters", show_alert=True)
            return
        await state.update_data(size=size)
        await state.set_state(Broadcast.waiting_message)
        await call.message.edit_text(
            f"{audience.describe(filters)}\n\n"
            f"📨 Recipients: <b>{size}</b>\n\n"
            "Send the message — text, photo or video. "
            "It will go out exactly as you send it."
        )
        await call.answer()
        return

    filters = audience.apply_action(filters, action, value)
    await state.update_data(filters=filters)
    text, kb = await audience.render(filters)
    try:
        await call.message.edit_text(text, reply_markup=kb)
    except Exception:  # noqa: BLE001
        pass
    await call.answer()


@router.message(Broadcast.waiting_message)
async def bc_preview(message: Message, state: FSMContext):
    await state.update_data(from_chat_id=message.chat.id, message_id=message.message_id)
    data = await state.get_data()
    await state.set_state(Broadcast.confirming)
    await message.answer(
        f"👆 This is how subscribers will see it.\n"
        f"Audience: <b>{data['size']}</b>\n\n"
        f"🧪 <b>Test to myself</b> — a copy goes to admins only\n"
        f"🎯 <b>Test on {SAMPLE_SIZE}</b> — goes to {SAMPLE_SIZE} real users, "
        f"they are excluded from the full send\n"
        f"✏️ <b>Rewrite</b> — send a new version",
        reply_markup=confirm_kb(),
    )


@router.callback_query(Broadcast.confirming, F.data == "bctest")
async def bc_test(call: CallbackQuery, state: FSMContext, bot: Bot):
    data = await state.get_data()
    ok = 0
    for aid in ADMIN_IDS:
        try:
            await bot.copy_message(
                chat_id=aid,
                from_chat_id=data["from_chat_id"],
                message_id=data["message_id"],
            )
            ok += 1
        except Exception as e:  # noqa: BLE001
            log.warning("Test message not delivered to admin %s: %s", aid, e)
        await asyncio.sleep(BROADCAST_SLEEP)
    await call.answer(f"🧪 Sent ({ok})")


@router.callback_query(Broadcast.confirming, F.data == "bcsample")
async def bc_sample(call: CallbackQuery, state: FSMContext, bot: Bot):
    data = await state.get_data()
    already = set(data.get("sampled") or [])
    users = [
        u
        for u in await db.get_audience(filters=data.get("filters"))
        if u not in already
    ]
    users = users[:SAMPLE_SIZE]
    if not users:
        await call.answer("No one to send to", show_alert=True)
        return

    sent = blocked = failed = 0
    for uid in users:
        try:
            await bot.copy_message(
                chat_id=uid,
                from_chat_id=data["from_chat_id"],
                message_id=data["message_id"],
            )
            sent += 1
        except TelegramForbiddenError:
            await db.mark_blocked(uid)
            blocked += 1
        except Exception:  # noqa: BLE001
            failed += 1
        await asyncio.sleep(BROADCAST_SLEEP)

    await state.update_data(sampled=list(already | set(users)))
    await call.answer(
        f"🎯 Delivered {sent} / blocked {blocked} / errors {failed}", show_alert=True
    )


@router.callback_query(Broadcast.confirming, F.data == "bcredo")
async def bc_redo(call: CallbackQuery, state: FSMContext):
    await state.set_state(Broadcast.waiting_message)
    await call.message.edit_text("Send the new version of the message.")
    await call.answer()


@router.callback_query(Broadcast.confirming, F.data == "bccancel")
async def bc_cancel(call: CallbackQuery, state: FSMContext):
    await state.clear()
    await call.message.edit_text("Cancelled.")
    await call.answer()


@router.callback_query(Broadcast.confirming, F.data == "bcgo")
async def bc_go(call: CallbackQuery, state: FSMContext, bot: Bot):
    data = await state.get_data()
    await state.clear()
    await call.message.edit_text("🚀 Broadcast started, a report will follow.")
    await call.answer()
    asyncio.create_task(
        run_broadcast(
            bot=bot,
            admin_id=call.from_user.id,
            filters=data.get("filters"),
            from_chat_id=data["from_chat_id"],
            message_id=data["message_id"],
            report_to=call.message.chat.id,
            exclude=set(data.get("sampled") or []),
        )
    )


async def run_broadcast(
    bot: Bot,
    admin_id: int,
    filters: dict | None,
    from_chat_id: int,
    message_id: int,
    report_to: int,
    exclude: set[int] | None = None,
):
    """Рассылка с троттлингом, ретраями и пометкой заблокировавших."""
    exclude = exclude or set()
    users = [u for u in await db.get_audience(filters=filters) if u not in exclude]
    label = audience.describe(filters or {}).replace("\n", " ") if filters else "all"
    bid = await db.log_broadcast_start(admin_id, label[:200])
    sent = failed = blocked = 0

    for uid in users:
        try:
            await bot.copy_message(
                chat_id=uid, from_chat_id=from_chat_id, message_id=message_id
            )
            sent += 1
        except TelegramRetryAfter as e:
            log.warning("Flood control, waiting %s sec", e.retry_after)
            await asyncio.sleep(e.retry_after)
            try:
                await bot.copy_message(
                    chat_id=uid, from_chat_id=from_chat_id, message_id=message_id
                )
                sent += 1
            except Exception:  # noqa: BLE001
                failed += 1
        except TelegramForbiddenError:
            await db.mark_blocked(uid)
            blocked += 1
        except TelegramBadRequest as e:
            log.error("BadRequest %s: %s", uid, e)
            failed += 1
        except Exception as e:  # noqa: BLE001
            log.exception("Send error for %s: %s", uid, e)
            failed += 1

        await asyncio.sleep(BROADCAST_SLEEP)

    await db.log_broadcast_finish(bid, sent, failed, blocked)
    await bot.send_message(
        report_to,
        f"✅ <b>Broadcast finished</b>\n"
        f"Delivered: <b>{sent}</b>\n"
        f"Blocked the bot: <b>{blocked}</b>\n"
        f"Errors: <b>{failed}</b>",
    )
