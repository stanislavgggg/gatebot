"""Проверка подписки, поиск картинок и локализованные клавиатуры."""
import logging
import os

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.types import FSInputFile, InlineKeyboardButton, InlineKeyboardMarkup

import bonuses
from config import GEOS, IMAGES_DIR, Geo
from locales import t

log = logging.getLogger(__name__)

OK_STATUSES = {"member", "administrator", "creator"}
IMG_EXTS = (".jpg", ".jpeg", ".png", ".webp")


# --------------------------------------------------------------------------
# Подписка
# --------------------------------------------------------------------------
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
# Картинки
# --------------------------------------------------------------------------
def find_image(name: str, geo_code: str | None = None) -> FSInputFile | None:
    """
    Ищет картинку по приоритету: {name}_{geo} → {name}.
    Например gate_lv.jpg, затем gate.jpg. Нет файла — вернёт None,
    и сообщение уйдёт просто текстом.
    """
    candidates = []
    if geo_code:
        candidates += [f"{name}_{geo_code}{ext}" for ext in IMG_EXTS]
    candidates += [f"{name}{ext}" for ext in IMG_EXTS]
    for fname in candidates:
        path = os.path.join(IMAGES_DIR, fname)
        if os.path.isfile(path):
            return FSInputFile(path)
    return None


# --------------------------------------------------------------------------
# Клавиатуры
# --------------------------------------------------------------------------
def gate_kb(missing: list, geo_code: str) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text=f"📢 {ch.title}", url=ch.url)] for ch in missing]
    rows.append(
        [
            InlineKeyboardButton(
                text=t(geo_code, "btn_check"), callback_data=f"check:{geo_code}"
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def geo_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"{g.flag} {g.title}", callback_data=f"geo:{g.code}"
                )
            ]
            for g in GEOS.values()
        ]
    )


def hub_kb(geo_code: str) -> InlineKeyboardMarkup:
    c = bonuses.counts(geo_code)
    rows = []
    if c["casino"]:
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"{t(geo_code, 'cat_casino')} ({c['casino']})",
                    callback_data=f"list:{geo_code}:casino",
                )
            ]
        )
    if c["betting"]:
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"{t(geo_code, 'cat_betting')} ({c['betting']})",
                    callback_data=f"list:{geo_code}:betting",
                )
            ]
        )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def list_kb(geo_code: str, vertical: str) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=f"{b.brand} — {b.title}", callback_data=f"bonus:{b.id}")]
        for b in bonuses.get(geo_code, vertical)
    ]
    rows.append(
        [
            InlineKeyboardButton(
                text=t(geo_code, "btn_back"), callback_data=f"hub:{geo_code}"
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def bonus_kb(bonus, geo_code: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=t(geo_code, "btn_claim"), url=bonus.url)],
            [
                InlineKeyboardButton(
                    text=t(geo_code, "btn_back"),
                    callback_data=f"list:{geo_code}:{bonus.vertical}",
                )
            ],
        ]
    )
