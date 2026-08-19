"""Хранилище: SQLite через aiosqlite."""
import datetime as dt

import aiosqlite

from config import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    user_id      INTEGER PRIMARY KEY,
    username     TEXT,
    first_name   TEXT,
    lang         TEXT,
    geo          TEXT,               -- lv / lt
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
"""


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


async def init() -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript(SCHEMA)
        await db.commit()


async def upsert_user(
    user_id: int,
    username: str | None,
    first_name: str | None,
    lang: str | None,
    geo: str | None,
    source: str | None,
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
                       geo    = COALESCE(NULLIF(geo, ''), ?),
                       source = COALESCE(NULLIF(source, ''), ?)
                   WHERE user_id = ?""",
                (username, first_name, now(), geo, source, user_id),
            )
        else:
            await db.execute(
                """INSERT INTO users
                   (user_id, username, first_name, lang, geo, source,
                    created_at, last_seen)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (user_id, username, first_name, lang, geo, source, now(), now()),
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


async def get_audience(
    geo: str | None = None,
    source: str | None = None,
    only_passed: bool = True,
) -> list[int]:
    """Список user_id для рассылки."""
    q = "SELECT user_id FROM users WHERE status = 'active'"
    params: list = []
    if only_passed:
        q += " AND gate_passed = 1"
    if geo:
        q += " AND geo = ?"
        params.append(geo)
    if source:
        q += " AND source LIKE ?"
        params.append(f"{source}%")
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(q, params)
        return [r[0] for r in await cur.fetchall()]


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
