"""Проверка подписки на каналы + клавиатуры."""
import logging

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

import bonuses
from config import GEOS, Geo

log = logging.getLogger(__name__)

OK_STATUSES = {"member", "administrator", "creator"}


async def is_subscribed(bot: Bot, chat_id: int, user_id: int) -> bool:
    """True если юзер в канале. Бот обязан быть админом канала."""
    try:
        member = await bot.get_chat_member(chat_id=chat_id, user_id=user_id)
        return member.status in OK_STATUSES
    except TelegramForbiddenError:
        log.error("Бот не админ в канале %s — getChatMember запрещён", chat_id)
        return False
    except TelegramBadRequest as e:
        log.error("getChatMember %s/%s: %s", chat_id, user_id, e)
        return False


async def missing_channels(bot: Bot, geo: Geo, user_id: int) -> list:
    """Каналы ГЕО, на которые юзер НЕ подписан."""
    missing = []
    for ch in geo.channels:
        if not await is_subscribed(bot, ch.chat_id, user_id):
            missing.append(ch)
    return missing


# --------------------------------------------------------------------------
# Клавиатуры
# --------------------------------------------------------------------------
def gate_kb(missing: list, geo_code: str) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text=f"📢 {ch.title}", url=ch.url)] for ch in missing]
    rows.append(
        [InlineKeyboardButton(text="✅ Я подписался", callback_data=f"check:{geo_code}")]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def geo_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=f"{g.flag} {g.title}", callback_data=f"geo:{g.code}")]
            for g in GEOS.values()
        ]
    )


def hub_kb(geo_code: str) -> InlineKeyboardMarkup:
    c = bonuses.counts(geo_code)
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"🎰 Казино ({c['casino']})", callback_data=f"list:{geo_code}:casino"
                )
            ],
            [
                InlineKeyboardButton(
                    text=f"⚽ Ставки ({c['betting']})", callback_data=f"list:{geo_code}:betting"
                )
            ],
        ]
    )


def list_kb(geo_code: str, vertical: str) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=f"{b.brand} — {b.title}", callback_data=f"bonus:{b.id}"
            )
        ]
        for b in bonuses.get(geo_code, vertical)
    ]
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data=f"hub:{geo_code}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def bonus_kb(bonus, geo_code: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🎁 Забрать бонус", url=bonus.url)],
            [
                InlineKeyboardButton(
                    text="⬅️ Назад", callback_data=f"list:{geo_code}:{bonus.vertical}"
                )
            ],
        ]
    )
