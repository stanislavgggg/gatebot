"""Проверка подписки, поиск картинок и локализованные клавиатуры."""
import logging
import os

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.types import FSInputFile, InlineKeyboardButton, InlineKeyboardMarkup

import bonuses
from config import GEOS, IMAGES_DIR, Geo, ready_geos
from locales import LANG_NAMES, t

log = logging.getLogger(__name__)

OK_STATUSES = {"member", "administrator", "creator"}
# Все статусы, которые Telegram может вернуть штатно.
# Что-то другое в ответе check_member = сбой, а не отсутствие подписки.
VALID_STATUSES = OK_STATUSES | {"left", "kicked", "restricted"}
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


async def check_member(bot: Bot, chat_id: int, user_id: int) -> tuple[bool, str]:
    """
    Как is_subscribed, но возвращает и причину.
    (подписан, статус_или_текст_ошибки)
    """
    try:
        member = await bot.get_chat_member(chat_id=chat_id, user_id=user_id)
        return member.status in OK_STATUSES, str(member.status)
    except TelegramForbiddenError as e:
        return False, f"FORBIDDEN: {e}"
    except TelegramBadRequest as e:
        return False, f"BAD_REQUEST: {e}"
    except Exception as e:  # noqa: BLE001
        return False, f"{type(e).__name__}: {e}"


async def check_all(bot: Bot, geo: Geo, user_id: int) -> list[tuple]:
    """
    Проверяет КАЖДЫЙ канал ГЕО и возвращает [(channel, подписан, статус), ...].

    Нужен, чтобы знать не только «прошёл гейт / не прошёл», но и на какие
    именно каналы юзер подписан — это и есть основа для сегментов рассылки
    («LV + канал казино», «подписан только на пикс» и т. д.).
    """
    out = []
    for ch in geo.channels:
        ok, status = await check_member(bot, ch.chat_id, user_id)
        out.append((ch, ok, status))
    return out


async def evaluate(bot: Bot, geo: Geo, user_id: int) -> tuple[list, list, list]:
    """
    Возвращает (каналы_без_подписки, ошибки_проверки, результаты_по_каналам).

    Ошибка проверки — это не «юзер не подписан», а поломка на нашей стороне
    (бот не админ, неверный chat_id). Такие случаи нельзя молча превращать
    в отказ: иначе гейт не пустит вообще никого, а причина останется невидимой.
    """
    results = await check_all(bot, geo, user_id)
    missing, errors = [], []
    for ch, ok, status in results:
        if not ok:
            missing.append(ch)
        # Всё, что не валидный статус участника — это сбой проверки
        if status not in VALID_STATUSES:
            errors.append((ch, status))
            log.error("Проверка канала %s (%s) сломана: %s", ch.chat_id, ch.title, status)
    return missing, errors, results


async def missing_channels(bot: Bot, geo: Geo, user_id: int) -> list:
    """Каналы ГЕО, на которые юзер НЕ подписан."""
    missing, _, _ = await evaluate(bot, geo, user_id)
    return missing


# --------------------------------------------------------------------------
# Картинки
# --------------------------------------------------------------------------
def find_image(
    name: str, geo_code: str | None = None, vertical: str | None = None
) -> FSInputFile | None:
    """
    Ищет картинку по убыванию точности:
      gate_{geo}_{vertical} → gate_{geo} → gate_{vertical} → gate
    Например: gate_lv_casino.jpg → gate_lv.jpg → gate_casino.jpg → gate.jpg
    Ни одного файла нет — вернёт None, сообщение уйдёт просто текстом.
    """
    stems = []
    if geo_code and vertical:
        stems.append(f"{name}_{geo_code}_{vertical}")
    if geo_code:
        stems.append(f"{name}_{geo_code}")
    if vertical:
        stems.append(f"{name}_{vertical}")
    stems.append(name)

    for stem in stems:
        for ext in IMG_EXTS:
            path = os.path.join(IMAGES_DIR, f"{stem}{ext}")
            if os.path.isfile(path):
                return FSInputFile(path)
    return None


# --------------------------------------------------------------------------
# Клавиатуры
# --------------------------------------------------------------------------
def _lang_row(lang: str) -> list:
    """
    Кнопка смены языка. Ставится последней строкой, чтобы не перебивать
    основной CTA, но быть на виду — иначе сменить язык можно только
    угадав команду /language, чего никто не делает.
    """
    return [
        InlineKeyboardButton(text=t(lang, "btn_lang"), callback_data="lang:pick")
    ]


def gate_kb(missing: list, geo_code: str, lang: str) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text=f"📢 {ch.title}", url=ch.url)] for ch in missing]
    rows.append(
        [
            InlineKeyboardButton(
                text=t(lang, "btn_check"), callback_data=f"check:{geo_code}"
            )
        ]
    )
    rows.append(_lang_row(lang))
    return InlineKeyboardMarkup(inline_keyboard=rows)


def geo_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"{g.flag} {g.title}", callback_data=f"geo:{g.code}"
                )
            ]
            for g in ready_geos()
        ]
    )


def lang_kb() -> InlineKeyboardMarkup:
    """Выбор языка интерфейса. К ГЕО отношения не имеет."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=name, callback_data=f"lang:{code}")]
            for code, name in LANG_NAMES.items()
        ]
    )


def hub_kb(geo_code: str, lang: str) -> InlineKeyboardMarkup:
    c = bonuses.counts(geo_code)
    rows = []
    if c["casino"]:
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"{t(lang, 'cat_casino')} ({c['casino']})",
                    callback_data=f"list:{geo_code}:casino",
                )
            ]
        )
    if c["betting"]:
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"{t(lang, 'cat_betting')} ({c['betting']})",
                    callback_data=f"list:{geo_code}:betting",
                )
            ]
        )
    rows.append(_lang_row(lang))
    return InlineKeyboardMarkup(inline_keyboard=rows)


def list_kb(geo_code: str, vertical: str, lang: str) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=f"{b.brand} — {b.title}", callback_data=f"bonus:{b.id}")]
        for b in bonuses.get(geo_code, vertical)
    ]
    rows.append(
        [
            InlineKeyboardButton(
                text=t(lang, "btn_back"), callback_data=f"hub:{geo_code}"
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def bonus_kb(bonus, geo_code: str, lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=t(lang, "btn_claim"), url=bonus.url)],
            [
                InlineKeyboardButton(
                    text=t(lang, "btn_back"),
                    callback_data=f"list:{geo_code}:{bonus.vertical}",
                )
            ],
        ]
    )
