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


def send_photo(chat_id: int | str, photo_url: str, caption: str = "") -> dict | None:
    """Telegram сам скачивает картинку по ссылке — заливать файл вручную не нужно."""
    if not configured():
        log.warning("Telegram не настроен — токен бота не задан в настройках")
        return None
    payload = {"chat_id": chat_id, "photo": photo_url}
    if caption:
        payload["caption"] = caption[:1024]  # лимит Telegram на подпись к фото
    try:
        r = httpx.post(_api_url("sendPhoto"), json=payload, timeout=30)
        if r.status_code >= 400:
            log.error("Telegram sendPhoto %s: %s", r.status_code, r.text)
            return None
        return r.json()
    except Exception as e:  # noqa: BLE001
        log.error("Telegram sendPhoto error: %s", e)
        return None


def webhook_url() -> str:
    """Публичный адрес нашего вебхука — тот же public_base_url, что используется
    для ссылок на картинки (вкладка «Настройки» → Поиск)."""
    base = (settings_store.get("public_base_url") or "").rstrip("/")
    return f"{base}/webhook/telegram" if base else ""


def set_webhook() -> dict:
    """Регистрирует вебхук в Telegram сама — без ручного curl. Вызывается при каждом
    сохранении настроек (см. admin.py), так что при смене токена/адреса всё
    переподключается само по себе."""
    url = webhook_url()
    if not configured():
        return {"ok": False, "description": "токен бота не задан"}
    if not url:
        return {"ok": False, "description": "не задан публичный адрес сервиса (вкладка «Поиск»)"}
    try:
        r = httpx.post(_api_url("setWebhook"), json={"url": url}, timeout=15)
        data = r.json()
        if not data.get("ok"):
            log.error("Telegram setWebhook не удался: %s", data)
        return data
    except Exception as e:  # noqa: BLE001
        log.error("Telegram setWebhook error: %s", e)
        return {"ok": False, "description": str(e)}


def webhook_info() -> dict | None:
    """Для отображения статуса в админке — что Telegram реально знает о нашем вебхуке."""
    if not configured():
        return None
    try:
        r = httpx.get(_api_url("getWebhookInfo"), timeout=10)
        data = r.json()
        return data.get("result") if data.get("ok") else None
    except Exception as e:  # noqa: BLE001
        log.error("Telegram getWebhookInfo error: %s", e)
        return None
