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
import relay
from config import ADMIN_IDS, BROADCAST_SLEEP, GEOS

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
            [InlineKeyboardButton(text="🧪 Тест себе", callback_data="bctest")],
            [
                InlineKeyboardButton(
                    text=f"🎯 Тест на {SAMPLE_SIZE} юзеров", callback_data="bcsample"
                )
            ],
            [InlineKeyboardButton(text="✏️ Переписать", callback_data="bcredo")],
            [
                InlineKeyboardButton(text="🚀 Отправить всем", callback_data="bcgo"),
                InlineKeyboardButton(text="✖️ Отмена", callback_data="bccancel"),
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
        "<b>📊 Статистика</b>",
        f"Всего запустили бота: <b>{total}</b>",
        f"Прошли гейт: <b>{passed}</b> ({conv})",
        f"Заблокировали бота: <b>{t['blocked'] or 0}</b>",
        f"За последние 24ч: <b>{s['last_24h']}</b>",
        "",
        "<b>По ГЕО:</b>",
    ]
    for r in s["by_geo"]:
        g = GEOS.get(r["geo"] or "")
        name = f"{g.flag} {g.title}" if g else (r["geo"] or "—")
        lines.append(f"{name}: {r['total']} / прошли {r['passed'] or 0}")

    rows = await db.breakdown()
    if rows:
        lines += ["", "<b>ГЕО × вертикаль:</b>"]
        for r in rows:
            geo = (r["geo"] or "—").upper()
            vert = r["vertical"] or "без вертикали"
            lines.append(f"{geo} / {vert}: {r['total']} / прошли {r['passed'] or 0}")

    if s["by_source"]:
        lines += ["", "<b>По источникам:</b>"]
        for r in s["by_source"]:
            lines.append(f"<code>{r['source']}</code>: {r['total']} / {r['passed'] or 0}")

    if s["top_bonuses"]:
        lines += ["", "<b>Топ бонусов по кликам:</b>"]
        for r in s["top_bonuses"]:
            lines.append(f"{r['bonus_id']}: {r['clicks']} ({r['uniq']} уник.)")

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
    text = f"♻️ Загружено бонусов: <b>{total}</b>\n" + "\n".join(parts)
    if soon:
        text += "\n\n⏳ <b>Протухают в течение недели:</b>\n" + "\n".join(
            f"• {b.brand} — {b.title} (до {b.expires})" for b in soon
        )
    await message.answer(text)


@router.message(Command("links"))
async def cmd_links(message: Message, bot: Bot):
    """Готовые deep-link ссылки под рекламные кампании."""
    if not is_admin(message.from_user.id):
        return
    me = await bot.get_me()
    lines = ["<b>🔗 Ссылки для рекламы</b>", ""]
    for g in GEOS.values():
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
        "Всё после кода гео пишется в поле <code>source</code> — "
        "можно добавлять метку креатива, например "
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
        await message.answer("Пока никто не писал.")
        return
    lines = ["<b>📨 Кто писал боту</b>", ""]
    lines += [fmt_user(u) for u in users]
    lines.append("")
    lines.append("Ответить: <code>/msg ID текст</code> или reply на пересланное сообщение")
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
        await message.answer("База пуста.")
        return
    lines = [f"<b>👥 Последние {len(users)}</b>", ""]
    lines += [fmt_user(u) for u in users]
    await message.answer("\n".join(lines))


@router.message(Command("find"))
async def cmd_find(message: Message, command: CommandObject):
    """Поиск юзера по username, имени или ID."""
    if not is_admin(message.from_user.id):
        return
    q = (command.args or "").strip()
    if not q:
        await message.answer("Как пользоваться: <code>/find username</code> или <code>/find 12345</code>")
        return
    users = await db.find_users(q)
    if not users:
        await message.answer("Ничего не нашлось.")
        return
    lines = [f"<b>🔍 Найдено: {len(users)}</b>", ""]
    lines += [fmt_user(u) for u in users]
    await message.answer("\n".join(lines))


@router.message(Command("dialog"))
async def cmd_dialog(message: Message, command: CommandObject):
    """История переписки с юзером."""
    if not is_admin(message.from_user.id):
        return
    arg = (command.args or "").strip()
    if not arg.isdigit():
        await message.answer("Как пользоваться: <code>/dialog 12345678</code>")
        return
    uid = int(arg)
    rows = await db.get_dialog(uid, 30)
    if not rows:
        await message.answer("Переписки нет.")
        return
    lines = [f"<b>💬 Диалог с {uid}</b>", ""]
    for r in rows:
        arrow = "◀️" if r["direction"] == "in" else "▶️"
        stamp = (r["created_at"] or "")[:16].replace("T", " ")
        lines.append(f"{arrow} <i>{stamp}</i>\n{r['text'] or '[медиа]'}")
    await message.answer("\n\n".join(lines[:1] + lines[1:])[:4000])


@router.message(Command("msg"))
async def cmd_msg(message: Message, command: CommandObject, bot: Bot):
    """Написать юзеру напрямую: /msg 12345678 текст."""
    if not is_admin(message.from_user.id):
        return
    args = (command.args or "").strip().split(maxsplit=1)
    if len(args) < 2 or not args[0].isdigit():
        await message.answer("Как пользоваться: <code>/msg 12345678 Привет!</code>")
        return
    uid, text = int(args[0]), args[1]
    try:
        await bot.send_message(uid, text)
    except TelegramForbiddenError:
        await db.mark_blocked(uid)
        await message.answer("⛔️ Юзер заблокировал бота — помечен в базе")
        return
    except Exception as e:  # noqa: BLE001
        await message.answer(f"❌ Не отправилось: {e}")
        return
    await db.log_message(uid, "out", text)
    await message.answer("✅ Отправлено")


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
        await message.answer("База пуста.")
        return
    lines = ["<b>🎯 Сегменты</b>", "", "<code>ГЕО  вертикаль   всего  гейт</code>"]
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
        await call.message.edit_text("Отменено.")
        await call.answer()
        return

    if action == "next":
        size = await db.count_audience(filters)
        if not size:
            await call.answer("Под эти фильтры никто не подходит", show_alert=True)
            return
        await state.update_data(size=size)
        await state.set_state(Broadcast.waiting_message)
        await call.message.edit_text(
            f"{audience.describe(filters)}\n\n"
            f"📨 Получателей: <b>{size}</b>\n\n"
            "Пришли сообщение — текст, фото или видео. "
            "Оно уйдёт ровно в том виде, в каком ты его отправишь."
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
        f"👆 Так это увидят подписчики.\n"
        f"Аудитория: <b>{data['size']}</b>\n\n"
        f"🧪 <b>Тест себе</b> — копия придёт только админам\n"
        f"🎯 <b>Тест на {SAMPLE_SIZE}</b> — уйдёт {SAMPLE_SIZE} реальным юзерам, "
        f"при полной рассылке они исключаются\n"
        f"✏️ <b>Переписать</b> — прислать новую версию",
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
            log.warning("Тест не ушёл админу %s: %s", aid, e)
        await asyncio.sleep(BROADCAST_SLEEP)
    await call.answer(f"🧪 Отправлено ({ok})")


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
        await call.answer("Некому отправлять", show_alert=True)
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
        f"🎯 Доставлено {sent} / блок {blocked} / ошибок {failed}", show_alert=True
    )


@router.callback_query(Broadcast.confirming, F.data == "bcredo")
async def bc_redo(call: CallbackQuery, state: FSMContext):
    await state.set_state(Broadcast.waiting_message)
    await call.message.edit_text("Пришли новую версию сообщения.")
    await call.answer()


@router.callback_query(Broadcast.confirming, F.data == "bccancel")
async def bc_cancel(call: CallbackQuery, state: FSMContext):
    await state.clear()
    await call.message.edit_text("Отменено.")
    await call.answer()


@router.callback_query(Broadcast.confirming, F.data == "bcgo")
async def bc_go(call: CallbackQuery, state: FSMContext, bot: Bot):
    data = await state.get_data()
    await state.clear()
    await call.message.edit_text("🚀 Рассылка запущена, отчёт придёт по завершении.")
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
            log.warning("Флуд-контроль, ждём %s сек", e.retry_after)
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
            log.exception("Ошибка отправки %s: %s", uid, e)
            failed += 1

        await asyncio.sleep(BROADCAST_SLEEP)

    await db.log_broadcast_finish(bid, sent, failed, blocked)
    await bot.send_message(
        report_to,
        f"✅ <b>Рассылка завершена</b>\n"
        f"Доставлено: <b>{sent}</b>\n"
        f"Заблокировали бота: <b>{blocked}</b>\n"
        f"Ошибок: <b>{failed}</b>",
    )
