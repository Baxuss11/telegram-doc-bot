"""Хранилище SQLite: сотрудники, проверки, отметки, фото.

Всё состояние диалога хранится здесь, поэтому после перезапуска бота
проверка продолжается с того же места.
"""
import os
import sqlite3
from datetime import datetime

from . import config

_conn: sqlite3.Connection | None = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    tg_id INTEGER PRIMARY KEY,
    tg_name TEXT,
    username TEXT,
    display_name TEXT,                 -- ФИО для отчётов
    status TEXT NOT NULL DEFAULT 'new', -- new / pending / allowed / blocked
    mode TEXT NOT NULL DEFAULT 'idle',  -- idle / await_name / await_company / await_zhk / checklist
    draft_company TEXT,
    draft_kind TEXT,
    card_message_id INTEGER,
    created_at TEXT
);
CREATE TABLE IF NOT EXISTS inspections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    inspector_id INTEGER NOT NULL,
    inspector_name TEXT,
    kind TEXT NOT NULL DEFAULT 'tech',  -- tech / legal
    company TEXT NOT NULL,
    zhk TEXT NOT NULL,
    folder_label TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL DEFAULT 'active', -- active / finished / cancelled
    current_item INTEGER NOT NULL DEFAULT 0,
    synced INTEGER NOT NULL DEFAULT 0,     -- 1 = отчёт и строка в таблице сохранены в Google
    folder_url TEXT,
    report_url TEXT
);
CREATE TABLE IF NOT EXISTS answers (
    inspection_id INTEGER NOT NULL,
    crit_key TEXT NOT NULL,
    PRIMARY KEY (inspection_id, crit_key)
);
CREATE TABLE IF NOT EXISTS item_states (
    inspection_id INTEGER NOT NULL,
    item_key TEXT NOT NULL,
    visited INTEGER NOT NULL DEFAULT 0,
    absent INTEGER NOT NULL DEFAULT 0,
    comment TEXT,
    PRIMARY KEY (inspection_id, item_key)
);
CREATE TABLE IF NOT EXISTS photos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    inspection_id INTEGER NOT NULL,
    item_key TEXT NOT NULL,
    tg_file_id TEXT NOT NULL,
    kind TEXT NOT NULL,       -- photo / image / pdf
    mime TEXT,
    file_name TEXT,
    drive_file_id TEXT,
    created_at TEXT
);
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT
);
"""


def init() -> None:
    global _conn
    os.makedirs(config.DATA_DIR, exist_ok=True)
    _conn = sqlite3.connect(config.DB_PATH, check_same_thread=False, isolation_level=None)
    _conn.row_factory = sqlite3.Row
    _conn.execute("PRAGMA journal_mode=WAL")
    _conn.executescript(SCHEMA)
    _migrate()


def _migrate() -> None:
    """Добавляет новые колонки в базу, созданную старой версией бота."""
    cols = {r["name"] for r in _db().execute("PRAGMA table_info(inspections)")}
    if "kind" not in cols:
        _db().execute("ALTER TABLE inspections ADD COLUMN kind TEXT NOT NULL DEFAULT 'tech'")
    ucols = {r["name"] for r in _db().execute("PRAGMA table_info(users)")}
    if "draft_kind" not in ucols:
        _db().execute("ALTER TABLE users ADD COLUMN draft_kind TEXT")


def _db() -> sqlite3.Connection:
    assert _conn is not None, "db.init() не вызван"
    return _conn


def now() -> datetime:
    return datetime.now(config.TZ)


def _update(table: str, key_col: str, key, fields: dict) -> None:
    if not fields:
        return
    cols = ", ".join(f"{k} = ?" for k in fields)
    _db().execute(f"UPDATE {table} SET {cols} WHERE {key_col} = ?", (*fields.values(), key))


# --- Сотрудники ---

def get_user(tg_id: int) -> sqlite3.Row | None:
    return _db().execute("SELECT * FROM users WHERE tg_id = ?", (tg_id,)).fetchone()


def touch_user(tg_id: int, tg_name: str, username: str | None) -> sqlite3.Row:
    _db().execute(
        "INSERT INTO users (tg_id, tg_name, username, created_at) VALUES (?, ?, ?, ?) "
        "ON CONFLICT(tg_id) DO UPDATE SET tg_name = excluded.tg_name, username = excluded.username",
        (tg_id, tg_name, username, now().isoformat()),
    )
    return get_user(tg_id)


def set_user(tg_id: int, **fields) -> None:
    _update("users", "tg_id", tg_id, fields)


def users_with_status(status: str) -> list[sqlite3.Row]:
    return _db().execute(
        "SELECT * FROM users WHERE status = ? ORDER BY display_name, tg_name", (status,)
    ).fetchall()


# --- Проверки ---

def create_inspection(inspector_id: int, inspector_name: str, kind: str,
                      company: str, zhk: str) -> int:
    started = now()
    cur = _db().execute(
        "INSERT INTO inspections (inspector_id, inspector_name, kind, company, zhk, folder_label, started_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (inspector_id, inspector_name, kind, company, zhk,
         started.strftime("%Y-%m-%d %H.%M"), started.isoformat()),
    )
    return cur.lastrowid


def get_inspection(inspection_id: int) -> sqlite3.Row | None:
    return _db().execute("SELECT * FROM inspections WHERE id = ?", (inspection_id,)).fetchone()


def finished_inspections(inspector_id: int | None = None, limit: int = 15) -> list[sqlite3.Row]:
    """Завершённые проверки: все (inspector_id=None) или конкретного сотрудника."""
    if inspector_id is None:
        return _db().execute(
            "SELECT * FROM inspections WHERE status = 'finished' ORDER BY id DESC LIMIT ?",
            (limit,)).fetchall()
    return _db().execute(
        "SELECT * FROM inspections WHERE status = 'finished' AND inspector_id = ? "
        "ORDER BY id DESC LIMIT ?", (inspector_id, limit)).fetchall()


def active_inspection(tg_id: int) -> sqlite3.Row | None:
    return _db().execute(
        "SELECT * FROM inspections WHERE inspector_id = ? AND status = 'active' "
        "ORDER BY id DESC LIMIT 1", (tg_id,)
    ).fetchone()


def set_inspection(inspection_id: int, **fields) -> None:
    _update("inspections", "id", inspection_id, fields)


def recent_companies(limit: int = 8) -> list[sqlite3.Row]:
    """Последние компании — чтобы не набирать название заново (и без опечаток)."""
    return _db().execute(
        "SELECT MAX(id) AS id, company FROM inspections WHERE status != 'cancelled' "
        "GROUP BY company ORDER BY MAX(id) DESC LIMIT ?", (limit,)
    ).fetchall()


def recent_zhk(company: str, limit: int = 8) -> list[sqlite3.Row]:
    return _db().execute(
        "SELECT MAX(id) AS id, zhk FROM inspections WHERE status != 'cancelled' AND company = ? "
        "GROUP BY zhk ORDER BY MAX(id) DESC LIMIT ?", (company, limit)
    ).fetchall()


# --- Отметки и состояние пунктов ---

def checked_criteria(inspection_id: int) -> set[str]:
    rows = _db().execute("SELECT crit_key FROM answers WHERE inspection_id = ?", (inspection_id,))
    return {r["crit_key"] for r in rows}


def toggle_criterion(inspection_id: int, crit_key: str) -> bool:
    """Переключает галочку. Возвращает новое значение."""
    cur = _db().execute(
        "DELETE FROM answers WHERE inspection_id = ? AND crit_key = ?", (inspection_id, crit_key)
    )
    if cur.rowcount:
        return False
    _db().execute("INSERT INTO answers VALUES (?, ?)", (inspection_id, crit_key))
    return True


def clear_criteria(inspection_id: int, crit_keys: list[str]) -> None:
    _db().executemany(
        "DELETE FROM answers WHERE inspection_id = ? AND crit_key = ?",
        [(inspection_id, k) for k in crit_keys],
    )


def item_states(inspection_id: int) -> dict[str, sqlite3.Row]:
    rows = _db().execute("SELECT * FROM item_states WHERE inspection_id = ?", (inspection_id,))
    return {r["item_key"]: r for r in rows}


def set_item_state(inspection_id: int, item_key: str, **fields) -> None:
    _db().execute(
        "INSERT OR IGNORE INTO item_states (inspection_id, item_key) VALUES (?, ?)",
        (inspection_id, item_key),
    )
    cols = ", ".join(f"{k} = ?" for k in fields)
    _db().execute(
        f"UPDATE item_states SET {cols} WHERE inspection_id = ? AND item_key = ?",
        (*fields.values(), inspection_id, item_key),
    )


# --- Фото ---

def add_photo(inspection_id: int, item_key: str, tg_file_id: str, kind: str,
              mime: str | None, file_name: str | None) -> int:
    cur = _db().execute(
        "INSERT INTO photos (inspection_id, item_key, tg_file_id, kind, mime, file_name, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (inspection_id, item_key, tg_file_id, kind, mime, file_name, now().isoformat()),
    )
    return cur.lastrowid


def get_photo(photo_id: int) -> sqlite3.Row | None:
    return _db().execute("SELECT * FROM photos WHERE id = ?", (photo_id,)).fetchone()


def photos(inspection_id: int) -> list[sqlite3.Row]:
    return _db().execute(
        "SELECT * FROM photos WHERE inspection_id = ? ORDER BY id", (inspection_id,)
    ).fetchall()


def photo_counts(inspection_id: int) -> dict[str, int]:
    rows = _db().execute(
        "SELECT item_key, COUNT(*) AS n FROM photos WHERE inspection_id = ? GROUP BY item_key",
        (inspection_id,),
    )
    return {r["item_key"]: r["n"] for r in rows}


def delete_last_photo(inspection_id: int, item_key: str) -> sqlite3.Row | None:
    row = _db().execute(
        "SELECT * FROM photos WHERE inspection_id = ? AND item_key = ? ORDER BY id DESC LIMIT 1",
        (inspection_id, item_key),
    ).fetchone()
    if row:
        _db().execute("DELETE FROM photos WHERE id = ?", (row["id"],))
    return row


def set_photo_drive_id(photo_id: int, drive_file_id: str) -> None:
    _db().execute("UPDATE photos SET drive_file_id = ? WHERE id = ?", (drive_file_id, photo_id))


# --- Настройки ---

def get_setting(key: str) -> str | None:
    row = _db().execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None


def set_setting(key: str, value: str) -> None:
    _db().execute(
        "INSERT INTO settings VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )
