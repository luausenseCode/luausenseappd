"""luauwhitelist - sqlite хранилище (общее для бота и сайта)."""
from __future__ import annotations

import datetime
import json
import os
import secrets
import sqlite3
import threading

from config import DATA_DIR

os.makedirs(DATA_DIR, exist_ok=True)
DB_PATH = os.path.join(DATA_DIR, "luauwhitelist.db")

_LOCAL = threading.local()


def conn() -> sqlite3.Connection:
    """Одно соединение на поток (SQLite не любит шарить соединение между потоками)."""
    c = getattr(_LOCAL, "conn", None)
    if c is None:
        c = sqlite3.connect(DB_PATH, timeout=30, isolation_level=None, check_same_thread=False)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA busy_timeout=30000")
        c.execute("PRAGMA synchronous=NORMAL")
        _LOCAL.conn = c
    return c


def init() -> None:
    c = conn()
    c.executescript(
        """
        CREATE TABLE IF NOT EXISTS panels (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id    INTEGER NOT NULL,
            channel_id  INTEGER NOT NULL,
            message_id  INTEGER,
            kind        TEXT NOT NULL,          -- 'script' | 'external'
            title       TEXT,
            author      TEXT,
            description TEXT,
            thumbnail   TEXT,
            image       TEXT,
            footer      TEXT,
            color       INTEGER DEFAULT 0,
            content_url TEXT,                   -- сырая ссылка на скрипт/лоадер
            role_ids    TEXT DEFAULT '[]',      -- JSON список id ролей
            created_at  TEXT
        );

        CREATE TABLE IF NOT EXISTS keys (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            key         TEXT UNIQUE NOT NULL,
            kind        TEXT NOT NULL,          -- 'script' | 'external'
            note        TEXT DEFAULT '',
            created_at  TEXT,
            expires_at  TEXT,                   -- ISO или NULL = бессрочно
            used_by     INTEGER,                -- discord user id
            used_at     TEXT,
            hwid        TEXT,
            banned      INTEGER DEFAULT 0,
            resets      INTEGER DEFAULT 0,
            executions  INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS loaders (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            name       TEXT,
            filename   TEXT,
            source_url TEXT,                    -- если лоадер сделан по внешней ссылке
            created_at TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_keys_kind ON keys(kind);
        CREATE INDEX IF NOT EXISTS idx_panels_guild ON panels(guild_id);
        """
    )


def now() -> str:
    return datetime.datetime.utcnow().isoformat(timespec="seconds")


def gen_key(prefix: str = "LUUAU") -> str:
    return f"{prefix}-" + "-".join(secrets.token_hex(2).upper() for _ in range(3))


def gen_hwid() -> str:
    return "HWID-" + secrets.token_hex(6).upper()


# --------------------------------------------------------------------------- #
#  Panels
# --------------------------------------------------------------------------- #
def add_panel(**p) -> int:
    cur = conn().execute(
        """INSERT INTO panels (guild_id, channel_id, message_id, kind, title, author, description,
                               thumbnail, image, footer, color, content_url, role_ids, created_at)
           VALUES (:guild_id, :channel_id, :message_id, :kind, :title, :author, :description,
                   :thumbnail, :image, :footer, :color, :content_url, :role_ids, :created_at)""",
        {**p, "created_at": now()},
    )
    return cur.lastrowid


def get_panel(panel_id: int):
    return conn().execute("SELECT * FROM panels WHERE id=?", (panel_id,)).fetchone()


def list_panels(guild_id=None, kind=None):
    q = "SELECT * FROM panels WHERE 1=1"
    args = []
    if guild_id:
        q += " AND guild_id=?"
        args.append(guild_id)
    if kind:
        q += " AND kind=?"
        args.append(kind)
    q += " ORDER BY id DESC"
    return conn().execute(q, args).fetchall()


def set_panel_message(panel_id: int, message_id: int) -> None:
    conn().execute("UPDATE panels SET message_id=? WHERE id=?", (message_id, panel_id))


def delete_panel(panel_id: int) -> None:
    conn().execute("DELETE FROM panels WHERE id=?", (panel_id,))


def panel_roles(panel) -> list[int]:
    try:
        return json.loads(panel["role_ids"] or "[]")
    except Exception:
        return []


# --------------------------------------------------------------------------- #
#  Keys
# --------------------------------------------------------------------------- #
def create_keys(kind: str, count: int = 100, days: int | None = None, note: str = "") -> list[str]:
    expires = (datetime.datetime.utcnow() + datetime.timedelta(days=days)).isoformat(
        timespec="seconds"
    ) if days else None
    c = conn()
    out = []
    c.execute("BEGIN")
    try:
        for _ in range(count):
            k = gen_key()
            c.execute(
                "INSERT INTO keys (key, kind, note, created_at, expires_at) VALUES (?,?,?,?,?)",
                (k, kind, note, now(), expires),
            )
            out.append(k)
        c.execute("COMMIT")
    except Exception:
        c.execute("ROLLBACK")
        raise
    return out


def get_key(key: str):
    return conn().execute("SELECT * FROM keys WHERE key=?", (key.strip().upper(),)).fetchone()


def list_keys(kind: str | None = None, limit: int = 500):
    q = "SELECT * FROM keys"
    args = []
    if kind:
        q += " WHERE kind=?"
        args.append(kind)
    q += " ORDER BY id DESC LIMIT ?"
    args.append(limit)
    return conn().execute(q, args).fetchall()


def delete_key(key: str) -> bool:
    cur = conn().execute("DELETE FROM keys WHERE key=?", (key,))
    return cur.rowcount > 0


def set_banned(key: str, banned: bool) -> bool:
    cur = conn().execute("UPDATE keys SET banned=? WHERE key=?", (int(banned), key))
    return cur.rowcount > 0


def reset_hwid(key: str) -> str | None:
    c = conn()
    cur = c.execute("UPDATE keys SET hwid=?, resets=resets+1 WHERE key=?", (gen_hwid(), key))
    if cur.rowcount == 0:
        return None
    row = c.execute("SELECT hwid FROM keys WHERE key=?", (key,)).fetchone()
    return row["hwid"]


def bump_executions(key: str) -> None:
    conn().execute("UPDATE keys SET executions=executions+1 WHERE key=?", (key,))


def key_expired(row) -> bool:
    if not row or not row["expires_at"]:
        return False
    try:
        return datetime.datetime.utcnow() > datetime.datetime.fromisoformat(row["expires_at"])
    except Exception:
        return False


def redeem(key_value: str, user_id: int, kind: str | None = None):
    """Возвращает (status, row). status: ok | not_found | wrong_kind | banned |
    already_used | expired"""
    row = get_key(key_value)
    if not row:
        return "not_found", None
    if kind and row["kind"] != kind:
        return "wrong_kind", row
    if row["banned"]:
        return "banned", row
    if key_expired(row):
        return "expired", row
    if row["used_by"] and int(row["used_by"]) != int(user_id):
        return "already_used", row

    hwid = row["hwid"] or gen_hwid()
    conn().execute(
        "UPDATE keys SET used_by=?, used_at=?, hwid=? WHERE key=?",
        (user_id, now(), hwid, row["key"]),
    )
    return "ok", get_key(row["key"])


def active_key(user_id: int, kind: str):
    """Активный (не забаненный, не истёкший) ключ пользователя для данного типа."""
    rows = conn().execute(
        "SELECT * FROM keys WHERE used_by=? AND kind=? AND banned=0 ORDER BY id DESC",
        (user_id, kind),
    ).fetchall()
    for r in rows:
        if not key_expired(r):
            return r
    return None


def stats() -> dict:
    c = conn()
    total = c.execute("SELECT COUNT(*) n FROM keys").fetchone()["n"]
    used = c.execute("SELECT COUNT(*) n FROM keys WHERE used_by IS NOT NULL").fetchone()["n"]
    banned = c.execute("SELECT COUNT(*) n FROM keys WHERE banned=1").fetchone()["n"]
    script = c.execute("SELECT COUNT(*) n FROM keys WHERE kind='script'").fetchone()["n"]
    external = c.execute("SELECT COUNT(*) n FROM keys WHERE kind='external'").fetchone()["n"]
    loaders = c.execute("SELECT COUNT(*) n FROM loaders").fetchone()["n"]
    panels = c.execute("SELECT COUNT(*) n FROM panels").fetchone()["n"]
    return {
        "total": total,
        "used": used,
        "banned": banned,
        "script": script,
        "external": external,
        "loaders": loaders,
        "panels": panels,
    }


# --------------------------------------------------------------------------- #
#  Loaders
# --------------------------------------------------------------------------- #
def add_loader(name: str, filename: str = "", source_url: str = "") -> int:
    cur = conn().execute(
        "INSERT INTO loaders (name, filename, source_url, created_at) VALUES (?,?,?,?)",
        (name, filename, source_url, now()),
    )
    return cur.lastrowid


def list_loaders(limit: int = 100):
    return conn().execute("SELECT * FROM loaders ORDER BY id DESC LIMIT ?", (limit,)).fetchall()


def get_loader(loader_id: int):
    return conn().execute("SELECT * FROM loaders WHERE id=?", (loader_id,)).fetchone()


def delete_loader(loader_id: int) -> None:
    conn().execute("DELETE FROM loaders WHERE id=?", (loader_id,))


init()
