"""Пересылка сообщений юзеров админам и ответы им."""
import logging

from aiogram import Bot
from aiogram.exceptions import TelegramForbiddenError
from aiogram.types import Message

import db
from config import ADMIN_IDS

log = logging.getLogger(__name__)


def user_header(user: dict | None, message: Message) -> str:
    """Шапка над пересланным сообщением: кто написал и откуда пришёл."""
    uname = f"@{message.from_user.username}" if message.from_user.username else "—"
    name = message.from_user.full_name
    parts = [
        f"📨 <b>{name}</b> ({uname})",
        f"ID: <code>{message.from_user.id}</code>",
    ]
    if user:
        geo = (user.get("geo") or "—").upper()
        src = user.get("source") or "—"
        gate = "✅ passed the gate" if user.get("gate_passed") else "⛔️ gate not passed"
        parts.append(f"{geo} · <code>{src}</code> · {gate}")
    parts.append("↩️ Reply to this message — it goes to the user")
    return "\n".join(parts)


async def to_admins(bot: Bot, message: Message) -> None:
    """
    Пересылает сообщение юзера всем админам и запоминает связку,
    чтобы reply в чате админа ушёл обратно этому юзеру.
    """
    if not ADMIN_IDS or message.from_user.id in ADMIN_IDS:
        return

    user = await db.get_user(message.from_user.id)
    await db.log_message(message.from_user.id, "in", message.text or message.caption)

    header = user_header(user, message)
    for aid in ADMIN_IDS:
        try:
            head_msg = await bot.send_message(aid, header)
            body_msg = await bot.copy_message(
                chat_id=aid,
                from_chat_id=message.chat.id,
                message_id=message.message_id,
            )
            # Ответить можно и на шапку, и на само сообщение
            await db.save_relay(aid, head_msg.message_id, message.from_user.id)
            await db.save_relay(aid, body_msg.message_id, message.from_user.id)
        except Exception as e:  # noqa: BLE001
            log.warning("Could not forward to admin %s: %s", aid, e)


async def to_user(bot: Bot, message: Message, user_id: int) -> tuple[bool, str]:
    """Отправляет ответ админа конкретному юзеру. Возвращает (успех, текст)."""
    try:
        await bot.copy_message(
            chat_id=user_id,
            from_chat_id=message.chat.id,
            message_id=message.message_id,
        )
    except TelegramForbiddenError:
        await db.mark_blocked(user_id)
        return False, "⛔️ User blocked the bot — marked in the database"
    except Exception as e:  # noqa: BLE001
        log.error("Reply to user %s failed: %s", user_id, e)
        return False, f"❌ Error: {e}"

    await db.log_message(user_id, "out", message.text or message.caption)
    return True, "✅ Sent"
