"""Хранилище: SQLite через aiosqlite."""
import datetime as dt
import logging

import aiosqlite

from config import DB_PATH

log = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    user_id      INTEGER PRIMARY KEY,
    username     TEXT,
    first_name   TEXT,
    lang         TEXT,               -- language_code клиента Telegram (сырой)
    ui_lang      TEXT,               -- язык интерфейса, выбранный юзером
    geo          TEXT,               -- lv / lt / en (офферы + каналы)
    vertical     TEXT,               -- casino / betting / NULL (не определена)
    source       TEXT,               -- метка кампании из deep-link
    gate_passed  INTEGER DEFAULT 0,
    status       TEXT DEFAULT 'active',   -- active / blocked
    created_at   TEXT,
    passed_at    TEXT,
    last_seen    TEXT,
    last_check   TEXT
);
CREATE INDEX IF NOT EXISTS idx_users_geo    ON users(geo);
CREATE INDEX IF NOT EXISTS idx_users_status ON users(status);
CREATE INDEX IF NOT EXISTS idx_users_source ON users(source);
-- индекс по vertical создаётся в _migrate: на старых базах колонки ещё нет

-- Клики по бонусам: главный сигнал качества трафика
CREATE TABLE IF NOT EXISTS bonus_clicks (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id   INTEGER,
    bonus_id  TEXT,
    geo       TEXT,
    vertical  TEXT,
    clicked_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_clicks_bonus ON bonus_clicks(bonus_id);
CREATE INDEX IF NOT EXISTS idx_clicks_user  ON bonus_clicks(user_id);

-- Подписка на КАЖДЫЙ канал отдельно. Гейт требует все каналы ГЕО сразу,
-- но для сегментов рассылки важно знать конкретику: кто сидит в казино-канале,
-- кто в беттинг-канале, а кто подписался только на один и гейт не прошёл.
CREATE TABLE IF NOT EXISTS channel_subs (
    user_id    INTEGER,
    chat_id    INTEGER,
    geo        TEXT,
    vertical   TEXT,               -- casino / betting (вертикаль канала)
    title      TEXT,
    subscribed INTEGER DEFAULT 0,
    checked_at TEXT,
    PRIMARY KEY (user_id, chat_id)
);
CREATE INDEX IF NOT EXISTS idx_subs_user ON channel_subs(user_id);
CREATE INDEX IF NOT EXISTS idx_subs_vert ON channel_subs(vertical, subscribed);

CREATE TABLE IF NOT EXISTS broadcasts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    admin_id    INTEGER,
    audience    TEXT,
    sent        INTEGER DEFAULT 0,
    failed      INTEGER DEFAULT 0,
    blocked     INTEGER DEFAULT 0,
    started_at  TEXT,
    finished_at TEXT
);

-- Связка «сообщение в чате админа» → «юзер», чтобы ответ reply'ем
-- уходил нужному человеку
CREATE TABLE IF NOT EXISTS relay (
    admin_chat_id INTEGER,
    admin_msg_id  INTEGER,
    user_id       INTEGER,
    created_at    TEXT,
    PRIMARY KEY (admin_chat_id, admin_msg_id)
);

-- Лог переписки: что писал юзер и что отвечали ему
CREATE TABLE IF NOT EXISTS inbox (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER,
    direction  TEXT,      -- in / out
    text       TEXT,
    created_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_inbox_user ON inbox(user_id);
"""


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


async def init() -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript(SCHEMA)
        await db.commit()
        await _migrate(db)


async def _migrate(db) -> None:
    """Догоняет схему на уже работающей базе, не теряя данные."""
    cur = await db.execute("PRAGMA table_info(users)")
    cols = {r[1] for r in await cur.fetchall()}

    if "vertical" not in cols:
        await db.execute("ALTER TABLE users ADD COLUMN vertical TEXT")
        await db.commit()

    # Язык интерфейса отделён от ГЕО: житель Латвии может читать бота
    # по-английски, оставаясь при этом на латвийских офферах и гейте.
    if "ui_lang" not in cols:
        await db.execute("ALTER TABLE users ADD COLUMN ui_lang TEXT")
        await db.commit()

    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_users_vertical ON users(vertical)"
    )
    await db.commit()

    # Заполняем вертикаль тем, у кого её ещё нет, разбирая метку источника
    from config import parse_vertical

    cur = await db.execute(
        "SELECT user_id, source FROM users WHERE vertical IS NULL AND source IS NOT NULL"
    )
    rows = await cur.fetchall()
    updated = 0
    for uid, source in rows:
        v = parse_vertical(source)
        if v:
            await db.execute(
                "UPDATE users SET vertical = ? WHERE user_id = ?", (v, uid)
            )
            updated += 1
    if updated:
        await db.commit()

    await _backfill_subs(db)


async def _backfill_subs(db) -> None:
    """
    Заполняет channel_subs для тех, кто уже прошёл гейт до появления таблицы.

    Прошёл гейт = был подписан на ВСЕ каналы своего ГЕО, иначе бы не прошёл.
    Так старая база сразу становится сегментируемой, без повторного опроса
    Telegram по каждому юзеру.
    """
    from config import GEOS

    cur = await db.execute("SELECT COUNT(*) FROM channel_subs")
    if (await cur.fetchone())[0]:
        return  # уже заполняли — второй раз не трогаем

    cur = await db.execute(
        "SELECT user_id, geo FROM users WHERE gate_passed = 1 AND geo IS NOT NULL"
    )
    rows = await cur.fetchall()
    added = 0
    for uid, geo_code in rows:
        geo = GEOS.get((geo_code or "").lower())
        if not geo:
            continue
        for ch in geo.channels:
            await db.execute(
                """INSERT OR IGNORE INTO channel_subs
                   (user_id, chat_id, geo, vertical, title, subscribed, checked_at)
                   VALUES (?,?,?,?,?,1,?)""",
                (uid, ch.chat_id, geo.code, ch.vertical, ch.title, now()),
            )
            added += 1
    if added:
        await db.commit()
        log.info("Бэкфилл подписок: записей %s", added)


async def upsert_user(
    user_id: int,
    username: str | None,
    first_name: str | None,
    lang: str | None,
    geo: str | None,
    source: str | None,
    vertical: str | None = None,
) -> None:
    """Первый /start создаёт запись. Повторный — обновляет last_seen.

    Первая атрибуция (geo/source) не перетирается: если юзер вернётся
    по другой ссылке, исходный источник сохраняется.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT user_id FROM users WHERE user_id = ?", (user_id,))
        if await cur.fetchone():
            await db.execute(
                """UPDATE users
                   SET username = ?, first_name = ?, last_seen = ?, status = 'active',
                       geo      = COALESCE(NULLIF(geo, ''), ?),
                       source   = COALESCE(NULLIF(source, ''), ?),
                       vertical = COALESCE(NULLIF(vertical, ''), ?)
                   WHERE user_id = ?""",
                (username, first_name, now(), geo, source, vertical, user_id),
            )
        else:
            await db.execute(
                """INSERT INTO users
                   (user_id, username, first_name, lang, geo, vertical, source,
                    created_at, last_seen)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (user_id, username, first_name, lang, geo, vertical, source,
                 now(), now()),
            )
        await db.commit()


async def set_ui_lang(user_id: int, lang: str) -> None:
    """Запоминает выбранный язык интерфейса. ГЕО при этом не трогается."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE users SET ui_lang = ? WHERE user_id = ?", (lang, user_id)
        )
        await db.commit()


async def set_geo(user_id: int, geo: str) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE users SET geo = ? WHERE user_id = ?", (geo, user_id))
        await db.commit()


async def mark_passed(user_id: int) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """UPDATE users
               SET gate_passed = 1, passed_at = COALESCE(passed_at, ?), last_check = ?
               WHERE user_id = ?""",
            (now(), now(), user_id),
        )
        await db.commit()


async def revoke_pass(user_id: int) -> None:
    """Сбрасывает гейт: юзер отписался от канала после того, как прошёл."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE users SET gate_passed = 0, last_check = ? WHERE user_id = ?",
            (now(), user_id),
        )
        await db.commit()


async def mark_blocked(user_id: int) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE users SET status = 'blocked' WHERE user_id = ?", (user_id,)
        )
        await db.commit()


async def get_user(user_id: int) -> dict | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
        row = await cur.fetchone()
        return dict(row) if row else None


async def save_subs(user_id: int, geo_code: str, results: list) -> None:
    """
    Пишет состояние подписки по каждому каналу.

    results: [(channel, подписан, статус), ...] из gate.evaluate().
    Вызывается на каждой проверке гейта, поэтому картина всегда свежая.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        for ch, ok, _status in results:
            await db.execute(
                """INSERT INTO channel_subs
                   (user_id, chat_id, geo, vertical, title, subscribed, checked_at)
                   VALUES (?,?,?,?,?,?,?)
                   ON CONFLICT(user_id, chat_id) DO UPDATE SET
                       subscribed = excluded.subscribed,
                       checked_at = excluded.checked_at,
                       title      = excluded.title,
                       vertical   = excluded.vertical,
                       geo        = excluded.geo""",
                (user_id, ch.chat_id, geo_code, ch.vertical, ch.title,
                 1 if ok else 0, now()),
            )
        await db.commit()


async def log_click(
    user_id: int, bonus_id: str, geo: str | None, vertical: str | None
) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT INTO bonus_clicks (user_id, bonus_id, geo, vertical, clicked_at)
               VALUES (?,?,?,?,?)""",
            (user_id, bonus_id, geo, vertical, now()),
        )
        await db.commit()


def _build_filter(f: dict) -> tuple[str, list]:
    """Собирает WHERE под фильтры конструктора аудитории."""
    q = " WHERE status = 'active'"
    params: list = []

    if f.get("gate") == "passed":
        q += " AND gate_passed = 1"
    elif f.get("gate") == "not_passed":
        q += " AND gate_passed = 0"

    geos = f.get("geos") or []
    if geos:
        q += f" AND geo IN ({','.join('?' * len(geos))})"
        params += list(geos)

    verts = list(f.get("verticals") or [])
    if verts:
        parts = []
        named = [v for v in verts if v != "none"]
        if named:
            parts.append(f"vertical IN ({','.join('?' * len(named))})")
            params += named
        if "none" in verts:
            parts.append("vertical IS NULL")
        q += " AND (" + " OR ".join(parts) + ")"

    # Подписка на конкретный канал (а не «прошёл гейт вообще»)
    chan = f.get("chan") or "any"
    if chan != "any":
        sub = (
            "SELECT 1 FROM channel_subs cs"
            " WHERE cs.user_id = users.user_id AND cs.subscribed = 1"
            " AND cs.vertical = ?"
        )
        if chan in ("casino", "betting"):
            q += f" AND EXISTS ({sub})"
            params.append(chan)
        elif chan in ("casino_only", "betting_only"):
            want = chan.split("_")[0]
            other = "betting" if want == "casino" else "casino"
            q += f" AND EXISTS ({sub}) AND NOT EXISTS ({sub})"
            params += [want, other]

    # Интерес по фактическим кликам на бонусы
    interest = f.get("interest") or "any"
    if interest != "any":
        clicked = (
            "SELECT 1 FROM bonus_clicks bc WHERE bc.user_id = users.user_id"
        )
        if interest == "none":
            q += f" AND NOT EXISTS ({clicked})"
        else:
            q += f" AND EXISTS ({clicked} AND bc.vertical = ?)"
            params.append(interest)

    days = f.get("days") or 0
    if days:
        q += f" AND last_seen >= datetime('now', '-{int(days)} day')"

    if f.get("source"):
        q += " AND source LIKE ?"
        params.append(f"%{f['source']}%")

    return q, params


async def get_audience(
    geo: str | None = None,
    source: str | None = None,
    only_passed: bool = True,
    filters: dict | None = None,
) -> list[int]:
    """
    Список user_id для рассылки.

    Либо простой вызов (geo/source/only_passed), либо filters —
    словарь из конструктора аудитории.
    """
    if filters is None:
        filters = {
            "geos": [geo] if geo else [],
            "gate": "passed" if only_passed else "any",
            "source": source,
        }
    where, params = _build_filter(filters)
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT user_id FROM users" + where, params)
        return [r[0] for r in await cur.fetchall()]


async def count_audience(filters: dict) -> int:
    where, params = _build_filter(filters)
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT COUNT(*) FROM users" + where, params)
        return (await cur.fetchone())[0]


async def breakdown() -> list[dict]:
    """Матрица ГЕО × вертикаль — для быстрой оценки размеров сегментов."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            """SELECT geo, vertical,
                      COUNT(*) AS total,
                      SUM(gate_passed = 1) AS passed,
                      SUM(status = 'blocked') AS blocked
               FROM users
               GROUP BY geo, vertical
               ORDER BY total DESC"""
        )
        return [dict(r) for r in await cur.fetchall()]


async def stats() -> dict:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        out: dict = {}
        cur = await db.execute(
            """SELECT COUNT(*) AS total,
                      SUM(gate_passed = 1) AS passed,
                      SUM(status = 'blocked') AS blocked
               FROM users"""
        )
        out["total"] = dict(await cur.fetchone())

        cur = await db.execute(
            """SELECT geo, COUNT(*) AS total,
                      SUM(gate_passed = 1) AS passed,
                      SUM(status = 'blocked') AS blocked
               FROM users GROUP BY geo ORDER BY total DESC"""
        )
        out["by_geo"] = [dict(r) for r in await cur.fetchall()]

        cur = await db.execute(
            """SELECT COALESCE(source, '—') AS source,
                      COUNT(*) AS total,
                      SUM(gate_passed = 1) AS passed
               FROM users GROUP BY source ORDER BY total DESC LIMIT 15"""
        )
        out["by_source"] = [dict(r) for r in await cur.fetchall()]

        cur = await db.execute(
            """SELECT bonus_id, COUNT(*) AS clicks,
                      COUNT(DISTINCT user_id) AS uniq
               FROM bonus_clicks GROUP BY bonus_id
               ORDER BY clicks DESC LIMIT 10"""
        )
        out["top_bonuses"] = [dict(r) for r in await cur.fetchall()]

        cur = await db.execute(
            """SELECT COUNT(*) AS c FROM users
               WHERE created_at >= datetime('now', '-1 day')"""
        )
        out["last_24h"] = (await cur.fetchone())["c"]
        return out


async def log_broadcast_start(admin_id: int, audience: str) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "INSERT INTO broadcasts (admin_id, audience, started_at) VALUES (?,?,?)",
            (admin_id, audience, now()),
        )
        await db.commit()
        return cur.lastrowid


async def log_broadcast_finish(bid: int, sent: int, failed: int, blocked: int) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """UPDATE broadcasts
               SET sent = ?, failed = ?, blocked = ?, finished_at = ?
               WHERE id = ?""",
            (sent, failed, blocked, now(), bid),
        )
        await db.commit()


# --------------------------------------------------------------------------
# Переписка с конкретным юзером
# --------------------------------------------------------------------------
async def touch(user_id: int) -> None:
    """Обновляет время последней активности."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE users SET last_seen = ? WHERE user_id = ?", (now(), user_id)
        )
        await db.commit()


async def save_relay(admin_chat_id: int, admin_msg_id: int, user_id: int) -> None:
    """Запоминает, какому юзеру принадлежит сообщение в чате админа."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT OR REPLACE INTO relay
               (admin_chat_id, admin_msg_id, user_id, created_at) VALUES (?,?,?,?)""",
            (admin_chat_id, admin_msg_id, user_id, now()),
        )
        await db.commit()


async def get_relay(admin_chat_id: int, admin_msg_id: int) -> int | None:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT user_id FROM relay WHERE admin_chat_id = ? AND admin_msg_id = ?",
            (admin_chat_id, admin_msg_id),
        )
        row = await cur.fetchone()
        return row[0] if row else None


async def log_message(user_id: int, direction: str, text: str | None) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO inbox (user_id, direction, text, created_at) VALUES (?,?,?,?)",
            (user_id, direction, (text or "")[:2000], now()),
        )
        await db.commit()


async def recent_users(limit: int = 20, only_wrote: bool = False) -> list[dict]:
    """Последние юзеры. only_wrote=True — только те, кто реально что-то писал."""
    if only_wrote:
        q = """SELECT u.*, MAX(i.created_at) AS last_msg,
                      SUM(i.direction = 'in') AS msgs_in
               FROM users u JOIN inbox i ON i.user_id = u.user_id
               WHERE i.direction = 'in'
               GROUP BY u.user_id
               ORDER BY last_msg DESC LIMIT ?"""
    else:
        q = """SELECT u.*, NULL AS last_msg, 0 AS msgs_in
               FROM users u ORDER BY last_seen DESC LIMIT ?"""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(q, (limit,))
        return [dict(r) for r in await cur.fetchall()]


async def find_users(query: str, limit: int = 20) -> list[dict]:
    """Поиск по username, имени или ID."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        if query.isdigit():
            cur = await db.execute(
                "SELECT * FROM users WHERE user_id = ?", (int(query),)
            )
        else:
            pat = f"%{query.lstrip('@')}%"
            cur = await db.execute(
                """SELECT * FROM users
                   WHERE username LIKE ? OR first_name LIKE ?
                   ORDER BY last_seen DESC LIMIT ?""",
                (pat, pat, limit),
            )
        return [dict(r) for r in await cur.fetchall()]


async def get_dialog(user_id: int, limit: int = 20) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            """SELECT * FROM (
                   SELECT * FROM inbox WHERE user_id = ?
                   ORDER BY id DESC LIMIT ?
               ) ORDER BY id ASC""",
            (user_id, limit),
        )
        return [dict(r) for r in await cur.fetchall()]
