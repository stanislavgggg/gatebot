"""
Бонус-хаб: чтение офферов из bonuses.json.

Файл правится вручную (или заливается из твоего промо-пайплайна),
после чего в боте достаточно нажать /reload — рестарт не нужен.
"""
import json
import logging
import re
from dataclasses import dataclass
from datetime import date

from config import BONUSES_FILE

log = logging.getLogger(__name__)

# Числа в заголовке оффера: "200% iki 1000 € + 100 sukimų"
_PERCENT_RE = re.compile(r"(\d[\d\s.,]*)\s*%")
_MONEY_RE = re.compile(r"(\d[\d\s.,]*)\s*(?:€|eur\b|EUR\b)", re.IGNORECASE)
_SPINS_RE = re.compile(
    r"(\d+)\s*(?:sukim|griezien|spin|free spin|fs\b)", re.IGNORECASE
)


def _num(raw: str) -> float:
    """'1 000' / '1.000' / '2,000' → 1000.0"""
    cleaned = re.sub(r"[\s.,]", "", raw)
    try:
        return float(cleaned)
    except ValueError:
        return 0.0


@dataclass
class Bonus:
    id: str
    brand: str
    title: str
    description: str
    url: str
    vertical: str  # casino / betting
    geo: str
    expires: str | None = None  # YYYY-MM-DD, None = бессрочный
    priority: int = 0  # ручной вес: больше — выше в списке, обходит авторасчёт

    @property
    def is_expired(self) -> bool:
        if not self.expires:
            return False
        try:
            return date.fromisoformat(self.expires) < date.today()
        except ValueError:
            return False

    @property
    def score(self) -> tuple:
        """
        Выгодность оффера для сортировки: (потолок €, процент, фриспины).

        Считается из заголовка, потому что отдельного числового поля в
        bonuses.json нет, а дублировать сумму в двух местах — гарантия
        расхождения. Если авторасчёт ошибается на конкретном оффере,
        ставь ему priority вручную — он важнее.

        Сравнивать по потолку, а не по проценту: «200% до 300 €» даёт
        игроку меньше, чем «100% до 1000 €».
        """
        cap = max((_num(m) for m in _MONEY_RE.findall(self.title)), default=0.0)
        pct = max((_num(m) for m in _PERCENT_RE.findall(self.title)), default=0.0)
        spins = max(
            (float(m) for m in _SPINS_RE.findall(self.title)), default=0.0
        )
        return (cap, pct, spins)


_CACHE: dict[str, list[Bonus]] = {}

# Итоги последней загрузки — показываются админу в /reload
LAST_LOAD: dict[str, int] = {"total": 0, "expired": 0, "blank": 0}


def load(path: str = BONUSES_FILE) -> dict[str, list[Bonus]]:
    """Читает JSON и складывает бонусы по ключу geo. Протухшие отбрасывает."""
    global _CACHE
    try:
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
    except FileNotFoundError:
        log.error("Файл бонусов не найден: %s", path)
        _CACHE = {}
        return _CACHE
    except json.JSONDecodeError as e:
        log.error("Битый JSON в %s: %s", path, e)
        return _CACHE  # оставляем прошлую рабочую версию

    data: dict[str, list[Bonus]] = {}
    skipped = blank = 0
    for geo, items in raw.items():
        bucket = []
        for it in items:
            try:
                b = Bonus(
                    id=it["id"],
                    brand=it["brand"],
                    title=it["title"],
                    description=it.get("description", ""),
                    url=it["url"],
                    vertical=it.get("vertical", "casino"),
                    geo=geo,
                    expires=it.get("expires"),
                    priority=int(it.get("priority", 0) or 0),
                )
            except KeyError as e:
                log.warning("Пропущен бонус без поля %s в гео %s", e, geo)
                continue
            if not b.title.strip():
                # Заготовка без условий: показывать оффер с пустым
                # заголовком хуже, чем не показывать вовсе — кнопка
                # выходит безымянной, а игрок не понимает, что берёт
                blank += 1
                continue
            if b.is_expired:
                skipped += 1
                continue
            bucket.append(b)
        data[geo] = bucket

    _CACHE = data
    total = sum(len(v) for v in data.values())
    log.info(
        "Загружено бонусов: %s (протухших: %s, без условий: %s)",
        total, skipped, blank,
    )
    LAST_LOAD.update(total=total, expired=skipped, blank=blank)
    return _CACHE


def get(geo: str, vertical: str | None = None) -> list[Bonus]:
    """
    Офферы ГЕО, отсортированные по выгодности — самый жирный первым.

    Порядок в bonuses.json не имеет значения: сортировка идёт по
    priority, затем по разобранной из заголовка сумме. Так новый добавленный
    оффер сам встаёт на своё место, без ручной перестановки файла.
    """
    items = _CACHE.get(geo, [])
    if vertical:
        items = [b for b in items if b.vertical == vertical]
    return sorted(items, key=lambda b: (b.priority, b.score), reverse=True)


def find(bonus_id: str, geo: str | None = None) -> Bonus | None:
    """
    Ищет бонус по id. Если задано geo — только внутри него.

    Ограничение по ГЕО критично: id прилетает из callback_data старой
    клавиатуры, которая могла быть создана под другое ГЕО. Без проверки
    литовец получил бы латвийский оффер с латвийским трекером.
    """
    buckets = [_CACHE.get(geo, [])] if geo else _CACHE.values()
    for items in buckets:
        for b in items:
            if b.id == bonus_id:
                return b
    return None


def counts(geo: str) -> dict[str, int]:
    items = _CACHE.get(geo, [])
    return {
        "casino": len([b for b in items if b.vertical == "casino"]),
        "betting": len([b for b in items if b.vertical == "betting"]),
        "total": len(items),
    }


def expiring_soon(days: int = 7) -> list[Bonus]:
    """Бонусы, которые протухнут в ближайшие N дней — для напоминания админу."""
    out = []
    today = date.today()
    for items in _CACHE.values():
        for b in items:
            if not b.expires:
                continue
            try:
                left = (date.fromisoformat(b.expires) - today).days
            except ValueError:
                continue
            if 0 <= left <= days:
                out.append(b)
    return out
