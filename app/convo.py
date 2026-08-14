"""Память диалогов по тредам Пачки — с сохранением в SQLite (переживает перезапуск).

Хранит и вопросы сотрудника, и ответы бота, чтобы нейросеть видела СВОИ прошлые
ответы и продолжала мысль. История на тред хранится JSON-ом.
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
from contextlib import contextmanager

from config import settings

_lock = threading.Lock()
_MAX_TURNS = 16


@contextmanager
def _conn():
    os.makedirs(settings.DATA_DIR_ABS, exist_ok=True)
    con = sqlite3.connect(settings.DB_PATH, timeout=30)
    try:
        yield con
        con.commit()
    finally:
        con.close()


def init() -> None:
    with _lock, _conn() as con:
        con.execute(
            "CREATE TABLE IF NOT EXISTS thread_memory ("
            "thread_key TEXT PRIMARY KEY, history TEXT, updated_at TEXT)"
        )


def get(key: str) -> list:
    if not key:
        return []
    with _lock, _conn() as con:
        row = con.execute(
            "SELECT history FROM thread_memory WHERE thread_key = ?", (key,)
        ).fetchone()
    if not row or not row[0]:
        return []
    try:
        return json.loads(row[0])
    except Exception:
        return []


def append(key: str, role: str, content: str) -> None:
    content = (content or "").strip()
    if not key or role not in ("user", "assistant") or not content:
        return
    with _lock, _conn() as con:
        row = con.execute(
            "SELECT history FROM thread_memory WHERE thread_key = ?", (key,)
        ).fetchone()
        hist = []
        if row and row[0]:
            try:
                hist = json.loads(row[0])
            except Exception:
                hist = []
        hist.append({"role": role, "content": content})
        if len(hist) > _MAX_TURNS:
            # Сохраняем самый первый вопрос треда (первостепенный) + последние реплики,
            # чтобы бот не терял исходную задачу по мере разрастания обсуждения.
            hist = hist[:1] + hist[-(_MAX_TURNS - 1):]
        con.execute(
            "INSERT INTO thread_memory (thread_key, history, updated_at) "
            "VALUES (?, ?, datetime('now')) "
            "ON CONFLICT(thread_key) DO UPDATE SET history=excluded.history, "
            "updated_at=excluded.updated_at",
            (key, json.dumps(hist, ensure_ascii=False)),
        )
