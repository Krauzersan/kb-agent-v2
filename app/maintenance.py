"""Обслуживание: размеры на диске, очистка мусора, сжатие базы."""
from __future__ import annotations

import logging
import os
import shutil
import sqlite3

from config import settings

log = logging.getLogger("maintenance")

# Где лежит хранилище Qdrant (по умолчанию как в нашей установке)
QDRANT_STORAGE = os.getenv("QDRANT_STORAGE", "/opt/qdrant/storage")


def _dir_size(path: str) -> int:
    total = 0
    if not path or not os.path.isdir(path):
        return 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(root, name))
            except OSError:
                pass
    return total


def _file_size(path: str) -> int:
    try:
        return os.path.getsize(path)
    except OSError:
        return 0


def disk_stats() -> dict:
    """Свободное место и размеры основных частей сервиса."""
    try:
        du = shutil.disk_usage(settings.DATA_DIR_ABS)
        total, used, free = du.total, du.used, du.free
    except Exception:
        total = used = free = 0
    return {
        "disk_total": total, "disk_used": used, "disk_free": free,
        "db": _file_size(settings.DB_PATH),
        "kb_files": _dir_size(settings.KB_DIR),
        "qdrant": _dir_size(QDRANT_STORAGE),
        "model_cache": _dir_size(settings.HF_HOME),
        "data_total": _dir_size(settings.DATA_DIR_ABS),
    }


def cleanup(keep_thread_days: int = 30) -> dict:
    """Удаляет осиротевшие файлы, старую память тредов и сжимает БД (VACUUM)."""
    import db as dbmod

    report = {"orphans": 0, "orphan_bytes": 0, "threads_removed": 0,
              "db_before": _file_size(settings.DB_PATH), "db_after": 0}

    # 1. Файлы в хранилище, на которые нет записи в базе (остатки от сбоев)
    known = set()
    try:
        for f in dbmod.list_files():
            sp = f.get("stored_path")
            if sp:
                known.add(os.path.abspath(sp))
    except Exception:
        log.exception("Очистка: не удалось прочитать список файлов")
        return report

    if os.path.isdir(settings.KB_DIR):
        for name in os.listdir(settings.KB_DIR):
            path = os.path.abspath(os.path.join(settings.KB_DIR, name))
            if not os.path.isfile(path) or path in known:
                continue
            try:
                size = os.path.getsize(path)
                os.remove(path)
                report["orphans"] += 1
                report["orphan_bytes"] += size
            except OSError:
                pass

    # 2. Старая память тредов + сжатие базы
    try:
        con = sqlite3.connect(settings.DB_PATH, timeout=60)
        try:
            try:
                cur = con.execute(
                    "DELETE FROM thread_memory WHERE updated_at < datetime('now', ?)",
                    (f"-{int(keep_thread_days)} days",),
                )
                report["threads_removed"] = cur.rowcount or 0
                con.commit()
            except sqlite3.OperationalError:
                pass  # таблицы может не быть
            con.isolation_level = None      # VACUUM нельзя внутри транзакции
            con.execute("VACUUM")
        finally:
            con.close()
    except Exception:
        log.exception("Очистка: сжатие базы не удалось")

    report["db_after"] = _file_size(settings.DB_PATH)
    log.info("Очистка завершена: файлов %s (%s байт), тредов %s, БД %s -> %s",
             report["orphans"], report["orphan_bytes"], report["threads_removed"],
             report["db_before"], report["db_after"])
    return report
