"""Конструктор аудитории: ГЕО × вертикаль × статус × активность."""
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

import db
from config import GEOS

# Короткие подписи: длинные обрезаются в три кнопки в ряд
VERTICALS = [
    ("casino", "🎰 Casino"),
    ("betting", "⚽ Betting"),
    ("none", "❓ None"),
]

GATE_LABELS = {
    "passed": "✅ Passed the gate",
    "not_passed": "⛔️ Did not pass the gate",
    "any": "👥 Everyone",
}
GATE_CYCLE = ["passed", "not_passed", "any"]

DAYS_LABELS = {
    0: "📅 All time",
    30: "📅 Active in 30 days",
    7: "📅 Active in 7 days",
}
DAYS_CYCLE = [0, 30, 7]


def default_filters() -> dict:
    """По умолчанию: все ГЕО, все вертикали, прошедшие гейт, без ограничения дат."""
    return {
        "geos": [g.code for g in GEOS.values()],
        "verticals": [v[0] for v in VERTICALS],
        "gate": "passed",
        "days": 0,
        "source": None,
    }


def toggle(items: list, value: str) -> list:
    """Переключает элемент, не позволяя опустошить список полностью."""
    if value in items:
        if len(items) == 1:
            return items  # последний не снимаем — иначе аудитория всегда пустая
        return [i for i in items if i != value]
    return items + [value]


def cycle(current, options: list):
    idx = options.index(current) if current in options else 0
    return options[(idx + 1) % len(options)]


def describe(f: dict) -> str:
    geos = ", ".join(
        f"{GEOS[c].flag} {GEOS[c].title}" for c in f["geos"] if c in GEOS
    ) or "—"
    verts = ", ".join(
        label.split(" ", 1)[1] for code, label in VERTICALS if code in f["verticals"]
    ) or "—"
    lines = [
        "<b>🎯 Audience</b>",
        "",
        f"GEO: {geos}",
        f"Vertical: {verts}",
        f"Status: {GATE_LABELS[f['gate']].split(' ', 1)[1]}",
        f"Activity: {DAYS_LABELS[f['days']].split(' ', 1)[1]}",
    ]
    if f.get("source"):
        lines.append(f"Source contains: <code>{f['source']}</code>")
    return "\n".join(lines)


def keyboard(f: dict, count: int, prefix: str = "aud") -> InlineKeyboardMarkup:
    """Тумблеры фильтров. Галочка = включено."""
    rows = []

    geo_row = [
        InlineKeyboardButton(
            text=f"{'✅' if g.code in f['geos'] else '⬜️'} {g.flag} {g.code.upper()}",
            callback_data=f"{prefix}:geo:{g.code}",
        )
        for g in GEOS.values()
    ]
    rows.append(geo_row)

    rows.append(
        [
            InlineKeyboardButton(
                text=f"{'✅' if code in f['verticals'] else '⬜️'} {label}",
                callback_data=f"{prefix}:vert:{code}",
            )
            for code, label in VERTICALS
        ]
    )

    rows.append(
        [InlineKeyboardButton(text=GATE_LABELS[f["gate"]], callback_data=f"{prefix}:gate")]
    )
    rows.append(
        [InlineKeyboardButton(text=DAYS_LABELS[f["days"]], callback_data=f"{prefix}:days")]
    )
    rows.append(
        [
            InlineKeyboardButton(
                text=f"📨 Recipients: {count} — continue",
                callback_data=f"{prefix}:next",
            )
        ]
    )
    rows.append([InlineKeyboardButton(text="✖️ Cancel", callback_data=f"{prefix}:cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def render(f: dict, prefix: str = "aud") -> tuple[str, InlineKeyboardMarkup]:
    count = await db.count_audience(f)
    return describe(f), keyboard(f, count, prefix)


def apply_action(f: dict, action: str, value: str | None) -> dict:
    """Меняет фильтры по нажатию кнопки."""
    if action == "geo":
        f["geos"] = toggle(f["geos"], value)
    elif action == "vert":
        f["verticals"] = toggle(f["verticals"], value)
    elif action == "gate":
        f["gate"] = cycle(f["gate"], GATE_CYCLE)
    elif action == "days":
        f["days"] = cycle(f["days"], DAYS_CYCLE)
    return f
