"""Трекер прогресса текущей операции: по файлам + по кускам текущего файла.

Одна операция за раз. Панель опрашивает /admin/progress и рисует прогресс-бар,
который двигается даже на одном тяжёлом файле (за счёт прогресса по кускам).
"""
from __future__ import annotations

import threading

_lock = threading.Lock()
_state = {
    "active": False, "kind": "",
    "total": 0, "done": 0,            # файлы: всего / завершено
    "file_name": "", "file_total": 0, "file_done": 0,  # текущий файл: кусков всего/готово
}


def start(kind: str, total: int) -> None:
    with _lock:
        _state.update(active=True, kind=kind, total=int(total), done=0,
                      file_name="", file_total=0, file_done=0)


def set_file(name: str, total_chunks: int) -> None:
    with _lock:
        _state["file_name"] = name or ""
        _state["file_total"] = int(total_chunks or 0)
        _state["file_done"] = 0


def chunk_step(n: int = 1) -> None:
    with _lock:
        _state["file_done"] += n


def step(n: int = 1) -> None:
    """Текущий файл завершён — двигаем счётчик файлов."""
    with _lock:
        _state["done"] += n
        _state["file_name"] = ""
        _state["file_total"] = 0
        _state["file_done"] = 0


def finish() -> None:
    with _lock:
        _state["active"] = False


def get() -> dict:
    with _lock:
        return dict(_state)
