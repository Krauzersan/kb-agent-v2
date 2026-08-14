"""Минимальный клиент Telegram Bot API — только отправка ответа.

Базовый уровень интеграции: без клавиатур, без форматирования, без ретраев.
Токен берётся из .env (TELEGRAM_BOT_TOKEN), не из settings_store — см. заметку в config.py.
"""
from __future__ import annotations

import logging

import httpx

from config import settings

log = logging.getLogger("telegram")


def configured() -> bool:
    return bool(settings.TELEGRAM_BOT_TOKEN)


def _api_url(method: str) -> str:
    return f"https://api.telegram.org/bot{settings.TELEGRAM_BOT_TOKEN}/{method}"


def send_message(chat_id: int | str, text: str) -> dict | None:
    if not configured():
        log.warning("Telegram не настроен — пусто TELEGRAM_BOT_TOKEN")
        return None
    try:
        r = httpx.post(
            _api_url("sendMessage"),
            json={"chat_id": chat_id, "text": text},
            timeout=30,
        )
        if r.status_code >= 400:
            log.error("Telegram sendMessage %s: %s", r.status_code, r.text)
            return None
        return r.json()
    except Exception as e:  # noqa: BLE001
        log.error("Telegram sendMessage error: %s", e)
        return None
