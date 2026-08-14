"""Минимальный клиент WhatsApp Cloud API (Meta) — только отправка ответа.

Официальная WhatsApp Business Platform, не WhatsApp Web. Нужен постоянный
access-токен и Phone Number ID из кабинета Meta for Developers — см. README.
"""
from __future__ import annotations

import logging

import httpx

from config import settings

log = logging.getLogger("whatsapp")

_API_VERSION = "v21.0"


def configured() -> bool:
    return bool(settings.WHATSAPP_ACCESS_TOKEN and settings.WHATSAPP_PHONE_NUMBER_ID)


def _api_url() -> str:
    return f"https://graph.facebook.com/{_API_VERSION}/{settings.WHATSAPP_PHONE_NUMBER_ID}/messages"


def send_message(to: str, text: str) -> dict | None:
    if not configured():
        log.warning("WhatsApp не настроен — пусто WHATSAPP_ACCESS_TOKEN/WHATSAPP_PHONE_NUMBER_ID")
        return None
    headers = {"Authorization": f"Bearer {settings.WHATSAPP_ACCESS_TOKEN}"}
    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "text",
        "text": {"body": text},
    }
    try:
        r = httpx.post(_api_url(), json=payload, headers=headers, timeout=30)
        if r.status_code >= 400:
            log.error("WhatsApp send %s: %s", r.status_code, r.text)
            return None
        return r.json()
    except Exception as e:  # noqa: BLE001
        log.error("WhatsApp send error: %s", e)
        return None
