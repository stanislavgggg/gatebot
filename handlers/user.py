"""Хендлеры пользователя: /start, гейт с картинкой, бонус-хаб."""
import logging
from datetime import datetime, timedelta, timezone

from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import CallbackQuery, Message

import bonuses
import db
import relay
from config import (
    ASK_LANG_ON_START,
    DEFAULT_GEO,
    RECHECK_DAYS,
    get_geo,
    parse_payload,
    parse_vertical,
)
from gate import (
    bonus_kb,
    evaluate,
    find_image,
    gate_kb,
    geo_kb,
    hub_kb,
    lang_kb,
    list_kb,
)
from locales import FALLBACK, LOCALES, normalize_lang, t

LOCALE_CODES = set(LOCALES)

router = Router()
log = logging.getLogger(__name__)


async def send_gate(
    bot: Bot, chat_id: int, geo, missing: list, lang: str,
    vertical: str | None = None,
) -> None:
    """
    Гейт: картинка + текст на языке ГЕО.

    Если из метки источника определилась вертикаль (casino / betting),
    берётся текст и картинка под неё — чтобы посадка совпадала с креативом.
    Иначе используется общий вариант.
    """
    wording = t(lang, "gate_one" if len(geo.channels) == 1 else "gate_many")
    key = f"gate_{vertical}" if vertical else "gate"
    text = t(lang, key, n=wording)
    kb = gate_kb(missing, geo.code, lang)

    photo = find_image("gate", geo.code, vertical)
    if photo:
        try:
            await bot.send_photo(chat_id, photo=photo, caption=text, reply_markup=kb)
            return
        except Exception as e:  # noqa: BLE001
            log.warning("Could not send gate image: %s", e)
    await bot.send_message(chat_id, text, reply_markup=kb)


def resolve_geo(user: dict | None, language_code: str | None):
    """
    Определяет ГЕО пользователя.

    Порядок приоритета:
      1. ГЕО, уже сохранённое за юзером в базе (он либо пришёл по
         geo-ссылке, либо выбрал вручную ранее) — не перетираем.
      2. Язык клиента Telegram (from_user.language_code), если он
         совпадает с одним из поддерживаемых ГЕО (lv / lt / en).
      3. DEFAULT_GEO — «Rest of the world», английский.
      4. Если и он не настроен — None, вызывающий код покажет
         выбор страны (geo_kb).
    """
    saved = get_geo(user.get("geo")) if user else None
    if saved:
        return saved

    # language_code приходит как "lt", "en-US", "ru" — берём базовую часть
    base = (language_code or "").split("-")[0].lower()
    by_lang = get_geo(base)
    if by_lang:
        return by_lang

    return get_geo(DEFAULT_GEO)


def resolve_lang(user: dict | None, language_code: str | None) -> str:
    """
    Определяет язык интерфейса — НЕЗАВИСИМО от ГЕО.

    ГЕО отвечает за офферы и каналы гейта, язык — только за тексты.
    Житель Латвии, которому удобнее по-английски, читает бота
    по-английски, но остаётся на латвийских бонусах и латвийском гейте.

    Порядок:
      1. Язык, выбранный юзером вручную (/language) — не перетираем.
      2. Язык клиента Telegram, если для него есть перевод.
      3. Язык ГЕО, если для него есть перевод (латвиец → латышский).
      4. Английский.
    """
    if user and user.get("ui_lang") in LOCALE_CODES:
        return user["ui_lang"]

    by_client = normalize_lang(language_code)
    if by_client:
        return by_client

    by_geo = normalize_lang(user.get("geo") if user else None)
    if by_geo:
        return by_geo

    return FALLBACK


async def ensure_access(bot: Bot, user: dict | None, user_id: int, geo) -> bool:
    """
    Подтверждает, что юзер всё ещё имеет право на доступ.

    Гейт — это не разовый шлагбаум: пройдя его, юзер может тут же
    отписаться от канала и пользоваться бонусами вечно. Поэтому раз
    в RECHECK_DAYS подписка перепроверяется, и при отписке доступ
    снимается. RECHECK_DAYS = 0 отключает перепроверку.
    """
    if not user or not user.get("gate_passed"):
        return False
    if RECHECK_DAYS <= 0:
        return True

    last = user.get("last_check")
    if last:
        try:
            age = datetime.now(timezone.utc) - datetime.fromisoformat(last)
            if age < timedelta(days=RECHECK_DAYS):
                return True
        except (ValueError, TypeError):
            pass  # кривая дата — безопаснее перепроверить

    missing, _errors, results = await evaluate(bot, geo, user_id)
    await db.save_subs(user_id, geo.code, results)
    if missing:
        await db.revoke_pass(user_id)
        return False
    await db.mark_passed(user_id)
    return True


async def send_hub(
    bot: Bot, chat_id: int, geo, lang: str, text_key: str = "hub"
) -> None:
    """
    Отправляет бонус-хаб. Если под ГЕО ещё нет ни одного оффера,
    клавиатура была бы пустой — вместо неё показываем «пока пусто».
    """
    if bonuses.counts(geo.code)["total"] == 0:
        await bot.send_message(chat_id, t(lang, "empty"))
        return
    await bot.send_message(
        chat_id, t(lang, text_key), reply_markup=hub_kb(geo.code, lang)
    )


async def show_gate_or_hub(
    bot: Bot, chat_id: int, user_id: int, geo, lang: str,
    vertical: str | None = None,
) -> bool:
    """Не подписан — гейт, подписан — хаб."""
    missing, _errors, results = await evaluate(bot, geo, user_id)
    await db.save_subs(user_id, geo.code, results)
    if missing:
        await send_gate(bot, chat_id, geo, missing, lang, vertical)
        return False
    await db.mark_passed(user_id)
    await send_hub(bot, chat_id, geo, lang, "welcome")
    return True


@router.message(CommandStart(deep_link=True))
@router.message(CommandStart())
async def cmd_start(message: Message, command: CommandObject, bot: Bot):
    payload = (command.args or "").strip() or None
    geo_code, source = parse_payload(payload)
    geo = get_geo(geo_code)
    vertical_from_link = parse_vertical(source or payload)

    await db.upsert_user(
        user_id=message.from_user.id,
        username=message.from_user.username,
        first_name=message.from_user.first_name,
        lang=message.from_user.language_code,
        geo=geo.code if geo else None,
        source=source or payload,
        vertical=vertical_from_link,
    )

    user = await db.get_user(message.from_user.id)
    lang = resolve_lang(user, message.from_user.language_code)

    # Опционально: спросить язык на самом первом заходе
    if ASK_LANG_ON_START and not (user or {}).get("ui_lang"):
        await message.answer(t(lang, "lang_pick"), reply_markup=lang_kb())
        return

    if geo is None:
        geo = resolve_geo(user, message.from_user.language_code)
        if geo is None:
            await message.answer(t(lang, "geo_pick"), reply_markup=geo_kb())
            return
        await db.set_geo(message.from_user.id, geo.code)

    # Вертикаль берём из свежей ссылки, иначе из сохранённой в базе
    vertical = vertical_from_link or (user.get("vertical") if user else None)
    await show_gate_or_hub(
        bot, message.chat.id, message.from_user.id, geo, lang, vertical
    )


@router.callback_query(F.data.startswith("geo:"))
async def pick_geo(call: CallbackQuery, bot: Bot):
    code = call.data.split(":", 1)[1]
    geo = get_geo(code)
    if not geo:
        await call.answer("Unknown region", show_alert=True)
        return
    await db.set_geo(call.from_user.id, code)
    user = await db.get_user(call.from_user.id)
    lang = resolve_lang(user, call.from_user.language_code)
    await call.answer()
    try:
        await call.message.delete()
    except Exception:  # noqa: BLE001
        pass
    await show_gate_or_hub(
        bot, call.message.chat.id, call.from_user.id, geo, lang
    )


@router.callback_query(F.data.startswith("check:"))
async def check_sub(call: CallbackQuery, bot: Bot):
    code = call.data.split(":", 1)[1]
    geo = get_geo(code)
    if not geo:
        await call.answer("Unknown region", show_alert=True)
        return

    user = await db.get_user(call.from_user.id)
    lang = resolve_lang(user, call.from_user.language_code)

    missing, errors, results = await evaluate(bot, geo, call.from_user.id)
    await db.save_subs(call.from_user.id, geo.code, results)

    if errors:
        # Проверка сломана — сообщаем админам, а не молчим
        from config import ADMIN_IDS

        text = "⚠️ <b>Gate check is failing</b>\n" + "\n".join(
            f"<code>{ch.chat_id}</code> {ch.title}\n<code>{err}</code>"
            for ch, err in errors
        ) + "\n\nRun /check for details."
        for aid in ADMIN_IDS:
            try:
                await bot.send_message(aid, text)
            except Exception:  # noqa: BLE001
                pass

    if missing:
        await call.answer(t(lang, "not_subscribed"), show_alert=True)
        try:
            await call.message.edit_reply_markup(
                reply_markup=gate_kb(missing, code, lang)
            )
        except Exception:  # noqa: BLE001
            pass
        return

    await db.mark_passed(call.from_user.id)
    await call.answer(t(lang, "access_granted"))
    try:
        await call.message.delete()
    except Exception:  # noqa: BLE001
        pass
    await send_hub(bot, call.message.chat.id, geo, lang, "welcome")


async def gate_ok(call: CallbackQuery):
    """
    Защита колбэков хаба. Старые клавиатуры живут в чате вечно:
    без этой проверки юзер, у которого доступ отозван, продолжал бы
    жать «Назад» / «Получить бонус» на сообщениях недельной давности.

    Возвращает (lang, geo) из БАЗЫ, а не из callback_data. ГЕО в кнопке
    отражает момент, когда сообщение было создано, и после смены страны
    устаревает — из-за этого юзер получал офферы и трекеры чужого ГЕО.

    Если доступа нет — (None, None).
    """
    user = await db.get_user(call.from_user.id)
    lang = resolve_lang(user, call.from_user.language_code)
    if not (user and user.get("gate_passed")):
        await call.answer(t(lang, "not_subscribed"), show_alert=True)
        return None, None

    geo = resolve_geo(user, call.from_user.language_code)
    if not geo:
        await call.answer(t(lang, "geo_pick"), show_alert=True)
        return None, None
    return lang, geo


@router.callback_query(F.data.startswith("hub:"))
async def back_to_hub(call: CallbackQuery):
    lang, geo = await gate_ok(call)
    if not lang:
        return
    await call.message.edit_text(
        t(lang, "hub"), reply_markup=hub_kb(geo.code, lang)
    )
    await call.answer()


@router.callback_query(F.data.startswith("list:"))
async def show_list(call: CallbackQuery):
    lang, geo = await gate_ok(call)
    if not lang:
        return
    _, _stale_geo, vertical = call.data.split(":")
    code = geo.code  # ГЕО из базы, а не из устаревшей кнопки
    if not bonuses.get(code, vertical):
        await call.answer(t(lang, "empty"), show_alert=True)
        # Вертикали в этом ГЕО нет — возвращаем в актуальный хаб
        try:
            await call.message.edit_text(
                t(lang, "hub"), reply_markup=hub_kb(code, lang)
            )
        except Exception:  # noqa: BLE001
            pass
        return
    label = t(lang, "cat_casino" if vertical == "casino" else "cat_betting")
    await call.message.edit_text(
        f"{label}\n\n{t(lang, 'pick_bonus')}",
        reply_markup=list_kb(code, vertical, lang),
    )
    await call.answer()


@router.callback_query(F.data.startswith("bonus:"))
async def show_bonus(call: CallbackQuery):
    lang, geo = await gate_ok(call)
    if not lang:
        return
    bonus_id = call.data.split(":", 1)[1]
    geo_code = geo.code

    # Ищем ТОЛЬКО в своём ГЕО: иначе по id из старой клавиатуры уйдёт
    # оффер и партнёрская ссылка чужого рынка
    b = bonuses.find(bonus_id, geo_code)
    if not b:
        await call.answer(t(lang, "expired"), show_alert=True)
        try:
            await call.message.edit_text(
                t(lang, "hub"), reply_markup=hub_kb(geo_code, lang)
            )
        except Exception:  # noqa: BLE001
            pass
        return

    await db.log_click(call.from_user.id, b.id, geo_code, b.vertical)

    text = f"<b>{b.brand}</b>\n{b.title}\n\n{b.description}"
    if b.expires:
        text += f"\n\n{t(lang, 'expires')} {b.expires}"
    await call.message.edit_text(text, reply_markup=bonus_kb(b, geo_code, lang))
    await call.answer()


@router.message(Command("bonus"))
@router.message(Command("bonuses"))
async def cmd_bonus(message: Message, bot: Bot):
    user = await db.get_user(message.from_user.id)
    lang = resolve_lang(user, message.from_user.language_code)
    geo = resolve_geo(user, message.from_user.language_code)
    if not geo:
        await message.answer(t(lang, "geo_pick"), reply_markup=geo_kb())
        return
    if not await ensure_access(bot, user, message.from_user.id, geo):
        vertical = (user or {}).get("vertical")
        await show_gate_or_hub(
            bot, message.chat.id, message.from_user.id, geo, lang, vertical
        )
        return
    await send_hub(bot, message.chat.id, geo, lang)


@router.message(Command("language"))
@router.message(Command("lang"))
async def cmd_language(message: Message):
    """Смена языка интерфейса. ГЕО (офферы и каналы) не меняется."""
    user = await db.get_user(message.from_user.id)
    lang = resolve_lang(user, message.from_user.language_code)
    await message.answer(t(lang, "lang_pick"), reply_markup=lang_kb())


@router.message(Command("country"))
@router.message(Command("geo"))
async def cmd_country(message: Message):
    """Смена ГЕО — набора офферов и каналов гейта. Язык не меняется."""
    user = await db.get_user(message.from_user.id)
    lang = resolve_lang(user, message.from_user.language_code)
    await message.answer(t(lang, "geo_pick"), reply_markup=geo_kb())


@router.callback_query(F.data == "lang:pick")
async def open_lang_picker(call: CallbackQuery):
    """Кнопка 🌐 на гейте и в хабе — открывает выбор языка."""
    user = await db.get_user(call.from_user.id)
    lang = resolve_lang(user, call.from_user.language_code)
    await call.answer()
    await call.message.answer(t(lang, "lang_pick"), reply_markup=lang_kb())


@router.callback_query(F.data.startswith("lang:"))
async def pick_lang(call: CallbackQuery, bot: Bot):
    code = call.data.split(":", 1)[1]
    if code not in LOCALE_CODES:
        await call.answer("Unknown language", show_alert=True)
        return

    await db.set_ui_lang(call.from_user.id, code)
    await call.answer(t(code, "lang_set"))
    try:
        await call.message.delete()
    except Exception:  # noqa: BLE001
        pass

    # Перерисовываем текущий экран на новом языке
    user = await db.get_user(call.from_user.id)
    geo = resolve_geo(user, call.from_user.language_code)
    if not geo:
        await bot.send_message(
            call.message.chat.id, t(code, "geo_pick"), reply_markup=geo_kb()
        )
        return
    if await ensure_access(bot, user, call.from_user.id, geo):
        await send_hub(bot, call.message.chat.id, geo, code)
    else:
        await show_gate_or_hub(
            bot, call.message.chat.id, call.from_user.id, geo, code,
            (user or {}).get("vertical"),
        )


@router.message()
async def fallback(message: Message, bot: Bot):
    """
    Любое сообщение юзера: пересылаем админам и показываем гейт или хаб.
    Ловит и текст, и медиа — чтобы ничего не терялось.
    """
    user = await db.get_user(message.from_user.id)
    await db.touch(message.from_user.id)
    await relay.to_admins(bot, message)

    lang = resolve_lang(user, message.from_user.language_code)
    if not user:
        await message.answer(t(lang, "start_hint"))
        return
    geo = resolve_geo(user, message.from_user.language_code)
    if not geo:
        await message.answer(t(lang, "geo_pick"), reply_markup=geo_kb())
        return
    if not await ensure_access(bot, user, message.from_user.id, geo):
        await show_gate_or_hub(
            bot, message.chat.id, message.from_user.id, geo, lang,
            user.get("vertical"),
        )
        return
    await send_hub(bot, message.chat.id, geo, lang)
