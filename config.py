"""
Конфигурация бота.
Все секреты и ID каналов — через переменные окружения (Railway → Variables).
Добавление нового ГЕО = добавить блок в GEOS + переменные окружения.
"""
import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
DB_PATH = os.getenv("DB_PATH", "bot.db")
BONUSES_FILE = os.getenv("BONUSES_FILE", "bonuses.json")

ADMIN_IDS = {
    int(x) for x in os.getenv("ADMIN_IDS", "").replace(" ", "").split(",") if x
}

# Telegram: ~30 msg/sec на бота. Держим 25 с запасом.
BROADCAST_RATE = float(os.getenv("BROADCAST_RATE", "25"))
BROADCAST_SLEEP = 1.0 / BROADCAST_RATE

# Перепроверять подписку у вернувшегося юзера раз в N дней (0 = выключить)
RECHECK_DAYS = int(os.getenv("RECHECK_DAYS", "7"))

# ГЕО по умолчанию, если в deep-link нет параметра
DEFAULT_GEO = os.getenv("DEFAULT_GEO", "lv")


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


@dataclass
class Channel:
    """Канал, подписка на который обязательна. Бот ДОЛЖЕН быть в нём админом."""

    chat_id: int
    title: str
    url: str
    vertical: str  # casino / betting — для аналитики и подписей


@dataclass
class Geo:
    """ГЕО = набор обязательных каналов + тексты."""

    code: str
    title: str
    flag: str
    channels: list[Channel] = field(default_factory=list)
    gate_text: str = ""
    welcome: str = ""


GEOS: dict[str, Geo] = {
    "lv": Geo(
        code="lv",
        title="Latvija",
        flag="🇱🇻",
        channels=[
            Channel(
                chat_id=_int_env("CH_LV_CASINO", -1000000000001),
                title=os.getenv("CH_LV_CASINO_TITLE", "Casino LV"),
                url=os.getenv("CH_LV_CASINO_URL", "https://t.me/your_casino_lv"),
                vertical="casino",
            ),
            Channel(
                chat_id=_int_env("CH_LV_BETTING", -1000000000002),
                title=os.getenv("CH_LV_BETTING_TITLE", "Betting LV"),
                url=os.getenv("CH_LV_BETTING_URL", "https://t.me/your_betting_lv"),
                vertical="betting",
            ),
        ],
    ),
    "lt": Geo(
        code="lt",
        title="Lietuva",
        flag="🇱🇹",
        channels=[
            Channel(
                chat_id=_int_env("CH_LT_CASINO", -1000000000003),
                title=os.getenv("CH_LT_CASINO_TITLE", "Casino LT"),
                url=os.getenv("CH_LT_CASINO_URL", "https://t.me/your_casino_lt"),
                vertical="casino",
            ),
            Channel(
                chat_id=_int_env("CH_LT_BETTING", -1000000000004),
                title=os.getenv("CH_LT_BETTING_TITLE", "Betting LT"),
                url=os.getenv("CH_LT_BETTING_URL", "https://t.me/your_betting_lt"),
                vertical="betting",
            ),
        ],
    ),
}

# ---------------------------------------------------------------------------
# Тексты. Правятся здесь, без залезания в хендлеры.
# ---------------------------------------------------------------------------
TXT_GATE = (
    "🎁 <b>Все актуальные бонусы букмекеров и казино — в одном боте.</b>\n\n"
    "Фриспины, бездепы, промокоды и повышенные первые депозиты. "
    "Список обновляется каждую неделю.\n\n"
    "Чтобы открыть доступ — подпишись на оба канала ниже "
    "и нажми «Я подписался»."
)

TXT_NOT_SUBSCRIBED = "❌ Подписка не найдена. Подпишись на оба канала и нажми кнопку снова."

TXT_WELCOME = (
    "✅ <b>Доступ открыт!</b>\n\n"
    "Выбирай раздел — внутри актуальные бонусы с прямыми ссылками.\n\n"
    "Новые бонусы буду присылать сюда, как только появятся."
)

TXT_HUB = "🎁 <b>Бонусы</b>\n\nВыбери категорию:"

TXT_GEO_PICK = "🌍 Выбери свою страну:"


def get_geo(code: str | None) -> Geo | None:
    if not code:
        return None
    return GEOS.get(code.lower())


def parse_payload(payload: str | None) -> tuple[str | None, str | None]:
    """
    Разбирает deep-link параметр.

    Формат: {geo}_{source}, где source — произвольная метка кампании/креатива.
      lv                    → geo=lv, source=None
      lv_fb                 → geo=lv, source=fb
      lv_fb_casino_cr3      → geo=lv, source=fb_casino_cr3

    Возвращает (geo_code | None, source | None).
    """
    if not payload:
        return None, None
    payload = payload.strip().lower()
    parts = payload.split("_", 1)
    geo = parts[0] if parts[0] in GEOS else None
    source = parts[1] if len(parts) > 1 else None
    if geo is None:
        # ГЕО не распознали — весь payload считаем меткой источника
        return None, payload
    return geo, source
