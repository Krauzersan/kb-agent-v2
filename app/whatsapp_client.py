"""Минимальный клиент WhatsApp Cloud API (Meta) — только отправка ответа.

Официальная WhatsApp Business Platform, не WhatsApp Web. Токен и Phone Number ID
вводятся в админ-панели (вкладка «Настройки» → WhatsApp) и хранятся в settings_store.
"""
from __future__ import annotations

import logging

import httpx

import settings_store

log = logging.getLogger("whatsapp")

_API_VERSION = "v21.0"


def _token() -> str:
    return (settings_store.get("whatsapp_access_token") or "").strip()


def _phone_number_id() -> str:
    return (settings_store.get("whatsapp_phone_number_id") or "").strip()


def configured() -> bool:
    return bool(_token() and _phone_number_id())


def _api_url() -> str:
    return f"https://graph.facebook.com/{_API_VERSION}/{_phone_number_id()}/messages"


def send_message(to: str, text: str) -> dict | None:
    if not configured():
        log.warning("WhatsApp не настроен — access-токен или Phone Number ID не заданы в настройках")
        return None
    headers = {"Authorization": f"Bearer {_token()}"}
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


def send_image(to: str, image_url: str, caption: str = "") -> dict | None:
    """WhatsApp Cloud API сам скачивает картинку по ссылке (image.link) — заливать
    файл вручную не нужно."""
    if not configured():
        log.warning("WhatsApp не настроен — access-токен или Phone Number ID не заданы в настройках")
        return None
    headers = {"Authorization": f"Bearer {_token()}"}
    image: dict = {"link": image_url}
    if caption:
        image["caption"] = caption[:1024]  # лимит WhatsApp на подпись
    payload = {"messaging_product": "whatsapp", "to": to, "type": "image", "image": image}
    try:
        r = httpx.post(_api_url(), json=payload, headers=headers, timeout=30)
        if r.status_code >= 400:
            log.error("WhatsApp send image %s: %s", r.status_code, r.text)
            return None
        return r.json()
    except Exception as e:  # noqa: BLE001
        log.error("WhatsApp send image error: %s", e)
        return None
