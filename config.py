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

    @property
    def is_ready(self) -> bool:
        """
        ГЕО готово к работе, только если у него есть хотя бы один канал.

        Без каналов гейт пропускает всех подряд (missing пустой →
        mark_passed), то есть юзер получает доступ, ни на что не
        подписавшись. Поэтому такие ГЕО не показываем и не назначаем
        автоматически — они включатся сами, как только появятся
        переменные окружения с каналом.
        """
        return bool(self.channels)


def ready_geos() -> list["Geo"]:
    """Только те ГЕО, у которых настроены каналы."""
    return [g for g in GEOS.values() if g.is_ready]


def _channels(*items: Channel) -> list[Channel]:
    """
    Отбрасывает каналы без настроенного chat_id (значение 0).

    Нужно для ГЕО, где канал ещё не заведён: пустой список каналов =
    гейта нет, юзер сразу попадает в хаб. Так ГЕО можно включить
    заранее, а канал добавить позже одной переменной окружения.
    """
    return [ch for ch in items if ch.chat_id]


GEOS: dict[str, Geo] = {
    "lv": Geo(
        code="lv",
        title="Latvija",
        flag="🇱🇻",
        channels=[
            Channel(
                chat_id=_int_env("CH_LV_CASINO", -1003910322335),
                title=os.getenv("CH_LV_CASINO_TITLE", "LUCKY LATVIA"),
                url=os.getenv("CH_LV_CASINO_URL", "https://t.me/luckylatviaan"),
                vertical="casino",
            ),
            Channel(
                chat_id=_int_env("CH_LV_BETTING", -1003713143280),
                title=os.getenv("CH_LV_BETTING_TITLE", "LV PICKS"),
                url=os.getenv("CH_LV_BETTING_URL", "https://t.me/latviapicks"),
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
                chat_id=_int_env("CH_LT_CASINO", -1003237183860),
                title=os.getenv("CH_LT_CASINO_TITLE", "LUCKY GURU"),
                url=os.getenv("CH_LT_CASINO_URL", "https://t.me/luckycasinoguru"),
                vertical="casino",
            ),
            # Второго канала под LT пока нет. Когда появится беттинг-канал —
            # раскомментировать и добавить переменные CH_LT_BETTING*.
            # Channel(
            #     chat_id=_int_env("CH_LT_BETTING", 0),
            #     title=os.getenv("CH_LT_BETTING_TITLE", "Betting LT"),
            #     url=os.getenv("CH_LT_BETTING_URL", ""),
            #     vertical="betting",
            # ),
        ],
    ),
    # ------------------------------------------------- REST OF THE WORLD (EN)
    # Всё, что не LV и не LT. Тексты — английские (locales.py → "en").
    # Каналы опциональны: пока CH_INT_* не заданы, гейта нет и юзер
    # сразу попадает в бонус-хаб.
    "en": Geo(
        code="en",
        title="Rest of the world",
        flag="🌍",
        channels=_channels(
            Channel(
                chat_id=_int_env("CH_INT_CASINO", 0),
                title=os.getenv("CH_INT_CASINO_TITLE", "Casino"),
                url=os.getenv("CH_INT_CASINO_URL", ""),
                vertical="casino",
            ),
            Channel(
                chat_id=_int_env("CH_INT_BETTING", 0),
                title=os.getenv("CH_INT_BETTING_TITLE", "Betting"),
                url=os.getenv("CH_INT_BETTING_URL", ""),
                vertical="betting",
            ),
        ),
    ),
}

# ---------------------------------------------------------------------------
# Тексты пользователя вынесены в locales.py (lv / lt / en).
# Здесь остаются только настройки медиа.
# ---------------------------------------------------------------------------
# Папка с картинками для гейта. Ожидаемые имена файлов:
#   image/gate_lv.jpg   — для Латвии
#   image/gate_lt.jpg   — для Литвы
#   image/gate.jpg      — общий фолбэк
# Поддерживаются .jpg .jpeg .png .webp. Если файла нет — уйдёт просто текст.
IMAGES_DIR = os.getenv("IMAGES_DIR", "image")


def get_geo(code: str | None) -> Geo | None:
    """Возвращает ГЕО по коду. Ненастроенные (без каналов) — как несуществующие."""
    if not code:
        return None
    geo = GEOS.get(code.lower())
    if geo and not geo.is_ready:
        return None
    return geo


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


def parse_vertical(source: str | None) -> str | None:
    """
    Достаёт вертикаль из метки источника, чтобы подобрать текст и картинку
    под креатив: lv_fb_casino → casino, lv_fb_betting_cr3 → betting.
    Не распознали — None, тогда используется общий текст гейта.
    """
    if not source:
        return None
    s = source.lower()
    if "casino" in s or "slots" in s:
        return "casino"
    if "betting" in s or "bet" in s or "picks" in s or "sport" in s:
        return "betting"
    return None
