"""Точка входа. Запуск: python bot.py"""
import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage

import bonuses
import db
from config import ADMIN_IDS, BOT_TOKEN
from handlers import admin, user

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
log = logging.getLogger("gatebot")


async def main() -> None:
    if not BOT_TOKEN:
        raise SystemExit("BOT_TOKEN не задан — проверь переменные окружения")
    if not ADMIN_IDS:
        log.warning("ADMIN_IDS пуст — админка будет недоступна")

    await db.init()
    bonuses.load()

    bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher(storage=MemoryStorage())
    # admin первым: команды админа должны перехватываться до fallback юзера
    dp.include_router(admin.router)
    dp.include_router(user.router)

    me = await bot.get_me()
    log.info("Бот @%s запущен", me.username)

    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        log.info("Остановлено")
