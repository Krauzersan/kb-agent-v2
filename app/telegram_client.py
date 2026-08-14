"""Минимальный клиент Telegram Bot API — только отправка ответа.

Базовый уровень интеграции: без клавиатур, без форматирования, без ретраев.
Токен вводится в админ-панели (вкладка «Настройки» → Telegram) и хранится
в settings_store, как у остальных провайдеров/интеграций.
"""
from __future__ import annotations

import logging

import httpx

import settings_store

log = logging.getLogger("telegram")


def _token() -> str:
    return (settings_store.get("telegram_bot_token") or "").strip()


def configured() -> bool:
    return bool(_token())


def _api_url(method: str) -> str:
    return f"https://api.telegram.org/bot{_token()}/{method}"


def send_message(chat_id: int | str, text: str) -> dict | None:
    if not configured():
        log.warning("Telegram не настроен — токен бота не задан в настройках")
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
