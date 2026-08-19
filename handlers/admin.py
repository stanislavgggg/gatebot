"""Админка: /broadcast с тест-режимом, /stats, /reload, /links."""
import asyncio
import logging

from aiogram import Bot, F, Router
from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramRetryAfter,
)
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

import bonuses
import db
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
def audience_kb() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=f"{g.flag} {g.title}", callback_data=f"bcgeo:{g.code}")]
        for g in GEOS.values()
    ]
    rows.append([InlineKeyboardButton(text="📣 Всем", callback_data="bcgeo:all")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


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
# Рассылка
# --------------------------------------------------------------------------
@router.message(Command("broadcast"))
async def cmd_broadcast(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    await state.set_state(Broadcast.choosing)
    await message.answer("Кому шлём?", reply_markup=audience_kb())


@router.callback_query(Broadcast.choosing, F.data.startswith("bcgeo:"))
async def bc_pick(call: CallbackQuery, state: FSMContext):
    code = call.data.split(":", 1)[1]
    geo = None if code == "all" else code
    audience = await db.get_audience(geo)
    await state.update_data(geo=geo, size=len(audience), sampled=[])
    await state.set_state(Broadcast.waiting_message)
    await call.message.edit_text(
        f"Аудитория: <b>{len(audience)}</b> получателей.\n\n"
        "Пришли сообщение для рассылки — текст, фото или видео. "
        "Оно уйдёт подписчикам ровно в том виде, в каком ты его отправишь "
        "(включая кнопки, если добавишь их через @BotFather-пост)."
    )
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
    users = [u for u in await db.get_audience(data.get("geo")) if u not in already]
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
            geo=data.get("geo"),
            from_chat_id=data["from_chat_id"],
            message_id=data["message_id"],
            report_to=call.message.chat.id,
            exclude=set(data.get("sampled") or []),
        )
    )


async def run_broadcast(
    bot: Bot,
    admin_id: int,
    geo: str | None,
    from_chat_id: int,
    message_id: int,
    report_to: int,
    exclude: set[int] | None = None,
):
    """Рассылка с троттлингом, ретраями и пометкой заблокировавших."""
    exclude = exclude or set()
    users = [u for u in await db.get_audience(geo) if u not in exclude]
    bid = await db.log_broadcast_start(admin_id, geo or "all")
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
