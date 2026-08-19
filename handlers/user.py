"""Хендлеры пользователя: /start, гейт, бонус-хаб."""
import logging

from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import CallbackQuery, Message

import bonuses
import db
from config import (
    DEFAULT_GEO,
    TXT_GATE,
    TXT_GEO_PICK,
    TXT_HUB,
    TXT_NOT_SUBSCRIBED,
    TXT_WELCOME,
    get_geo,
    parse_payload,
)
from gate import bonus_kb, gate_kb, geo_kb, hub_kb, list_kb, missing_channels

router = Router()
log = logging.getLogger(__name__)


async def show_gate_or_hub(bot: Bot, chat_id: int, user_id: int, geo) -> bool:
    """Если подписан — открывает хаб, иначе показывает гейт."""
    missing = await missing_channels(bot, geo, user_id)
    if missing:
        await bot.send_message(chat_id, TXT_GATE, reply_markup=gate_kb(missing, geo.code))
        return False
    await db.mark_passed(user_id)
    await bot.send_message(chat_id, TXT_WELCOME, reply_markup=hub_kb(geo.code))
    return True


@router.message(CommandStart(deep_link=True))
@router.message(CommandStart())
async def cmd_start(message: Message, command: CommandObject, bot: Bot):
    payload = (command.args or "").strip() or None
    geo_code, source = parse_payload(payload)
    geo = get_geo(geo_code)

    await db.upsert_user(
        user_id=message.from_user.id,
        username=message.from_user.username,
        first_name=message.from_user.first_name,
        lang=message.from_user.language_code,
        geo=geo.code if geo else None,
        source=source or payload,
    )

    if geo is None:
        user = await db.get_user(message.from_user.id)
        geo = get_geo(user.get("geo") if user else None) or get_geo(DEFAULT_GEO)
        if geo is None:
            await message.answer(TXT_GEO_PICK, reply_markup=geo_kb())
            return
        await db.set_geo(message.from_user.id, geo.code)

    await show_gate_or_hub(bot, message.chat.id, message.from_user.id, geo)


@router.callback_query(F.data.startswith("geo:"))
async def pick_geo(call: CallbackQuery, bot: Bot):
    code = call.data.split(":", 1)[1]
    geo = get_geo(code)
    if not geo:
        await call.answer("Неизвестное гео", show_alert=True)
        return
    await db.set_geo(call.from_user.id, code)
    await call.answer()
    try:
        await call.message.delete()
    except Exception:  # noqa: BLE001
        pass
    await show_gate_or_hub(bot, call.message.chat.id, call.from_user.id, geo)


@router.callback_query(F.data.startswith("check:"))
async def check_sub(call: CallbackQuery, bot: Bot):
    code = call.data.split(":", 1)[1]
    geo = get_geo(code)
    if not geo:
        await call.answer("Неизвестное гео", show_alert=True)
        return

    missing = await missing_channels(bot, geo, call.from_user.id)
    if missing:
        await call.answer(TXT_NOT_SUBSCRIBED, show_alert=True)
        try:
            await call.message.edit_reply_markup(reply_markup=gate_kb(missing, geo.code))
        except Exception:  # noqa: BLE001
            pass
        return

    await db.mark_passed(call.from_user.id)
    await call.answer("Доступ открыт ✅")
    try:
        await call.message.delete()
    except Exception:  # noqa: BLE001
        pass
    await bot.send_message(call.message.chat.id, TXT_WELCOME, reply_markup=hub_kb(geo.code))


@router.callback_query(F.data.startswith("hub:"))
async def back_to_hub(call: CallbackQuery):
    code = call.data.split(":", 1)[1]
    await call.message.edit_text(TXT_HUB, reply_markup=hub_kb(code))
    await call.answer()


@router.callback_query(F.data.startswith("list:"))
async def show_list(call: CallbackQuery):
    _, code, vertical = call.data.split(":")
    items = bonuses.get(code, vertical)
    if not items:
        await call.answer("Здесь пока пусто — скоро добавим", show_alert=True)
        return
    label = "🎰 Казино" if vertical == "casino" else "⚽ Ставки"
    await call.message.edit_text(
        f"{label}\n\nВыбери бонус:", reply_markup=list_kb(code, vertical)
    )
    await call.answer()


@router.callback_query(F.data.startswith("bonus:"))
async def show_bonus(call: CallbackQuery):
    bonus_id = call.data.split(":", 1)[1]
    b = bonuses.find(bonus_id)
    if not b:
        await call.answer("Бонус больше не актуален", show_alert=True)
        return

    user = await db.get_user(call.from_user.id)
    await db.log_click(call.from_user.id, b.id, user.get("geo") if user else None, b.vertical)

    text = f"<b>{b.brand}</b>\n{b.title}\n\n{b.description}"
    if b.expires:
        text += f"\n\n⏳ Действует до: {b.expires}"
    await call.message.edit_text(text, reply_markup=bonus_kb(b, b.geo))
    await call.answer()


@router.message(Command("bonus"))
@router.message(Command("bonuses"))
async def cmd_bonus(message: Message, bot: Bot):
    user = await db.get_user(message.from_user.id)
    geo = get_geo(user.get("geo") if user else None) or get_geo(DEFAULT_GEO)
    if not geo:
        await message.answer(TXT_GEO_PICK, reply_markup=geo_kb())
        return
    if not user or not user.get("gate_passed"):
        await show_gate_or_hub(bot, message.chat.id, message.from_user.id, geo)
        return
    await message.answer(TXT_HUB, reply_markup=hub_kb(geo.code))


@router.message(F.text)
async def fallback(message: Message, bot: Bot):
    """Любой текст: не прошёл гейт — показываем гейт, прошёл — хаб."""
    user = await db.get_user(message.from_user.id)
    if not user:
        await message.answer("Напиши /start")
        return
    geo = get_geo(user.get("geo")) or get_geo(DEFAULT_GEO)
    if not geo:
        await message.answer(TXT_GEO_PICK, reply_markup=geo_kb())
        return
    if not user.get("gate_passed"):
        await show_gate_or_hub(bot, message.chat.id, message.from_user.id, geo)
        return
    await message.answer(TXT_HUB, reply_markup=hub_kb(geo.code))
