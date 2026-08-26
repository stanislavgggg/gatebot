"""
Бонус-хаб: чтение офферов из bonuses.json.

Файл правится вручную (или заливается из твоего промо-пайплайна),
после чего в боте достаточно нажать /reload — рестарт не нужен.
"""
import json
import logging
from dataclasses import dataclass
from datetime import date

from config import BONUSES_FILE

log = logging.getLogger(__name__)


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

    @property
    def is_expired(self) -> bool:
        if not self.expires:
            return False
        try:
            return date.fromisoformat(self.expires) < date.today()
        except ValueError:
            return False


_CACHE: dict[str, list[Bonus]] = {}


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
    skipped = 0
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
                )
            except KeyError as e:
                log.warning("Пропущен бонус без поля %s в гео %s", e, geo)
                continue
            if b.is_expired:
                skipped += 1
                continue
            bucket.append(b)
        data[geo] = bucket

    _CACHE = data
    total = sum(len(v) for v in data.values())
    log.info("Загружено бонусов: %s (протухших пропущено: %s)", total, skipped)
    return _CACHE


def get(geo: str, vertical: str | None = None) -> list[Bonus]:
    items = _CACHE.get(geo, [])
    if vertical:
        items = [b for b in items if b.vertical == vertical]
    return items


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
