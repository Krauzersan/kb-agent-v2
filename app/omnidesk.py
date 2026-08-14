"""Клиент API Omnidesk: чтение сообщений обращения и отправка ответа от имени бота.

Авторизация — HTTP Basic: логин = email сотрудника, пароль = API-ключ.
Все параметры берутся из настроек админ-панели (settings_store).
"""
from __future__ import annotations

import html
import logging
import re

import httpx

import settings_store

log = logging.getLogger("omnidesk")

_TAG_RE = re.compile(r"<[^>]+>")


def domain() -> str:
    return (settings_store.get("omnidesk_domain") or "").strip()


def base_url() -> str:
    return f"https://{domain()}.omnidesk.ru/api"


def _auth():
    return (
        (settings_store.get("omnidesk_staff_email") or "").strip(),
        (settings_store.get("omnidesk_api_key") or "").strip(),
    )


def configured() -> bool:
    email, key = _auth()
    return bool(domain() and email and key)


def _strip_html(s: str) -> str:
    text = html.unescape(_TAG_RE.sub(" ", s or ""))
    return re.sub(r"\s+", " ", text).strip()


def message_text(m: dict) -> str:
    """Достаём текст сообщения: сначала plain, иначе очищенный HTML."""
    plain = (m.get("content") or "").strip()
    if plain:
        return plain
    return _strip_html(m.get("content_html"))


def is_user_message(m: dict) -> bool:
    """Сообщение от клиента (а не от сотрудника/бота) и не внутренняя заметка."""
    return int(m.get("user_id") or 0) > 0 and not m.get("note")


def get_messages(case_id: int) -> list[dict]:
    """Список сообщений обращения, отсортированный по возрастанию message_id.

    Ответ Omnidesk — объект вида {"0": {"message": {...}}, "1": {...}, "total_count": N}.
    """
    r = httpx.get(f"{base_url()}/cases/{case_id}/messages.json", auth=_auth(), timeout=30)
    r.raise_for_status()
    data = r.json()
    messages = []
    for key, value in data.items():
        if key.isdigit() and isinstance(value, dict) and "message" in value:
            messages.append(value["message"])
    messages.sort(key=lambda m: m.get("message_id", 0))
    return messages


def last_message(case_id: int) -> dict | None:
    msgs = get_messages(case_id)
    return msgs[-1] if msgs else None


def post_reply(case_id: int, content: str, as_note: bool = False) -> dict | None:
    """Отправить ответ клиенту (as_note=False) или внутреннюю заметку (as_note=True)."""
    staff_id = str(settings_store.get("omnidesk_staff_id") or "").strip()
    headers = {"Content-Type": "application/json"}
    if as_note:
        url = f"{base_url()}/cases/{case_id}/notes.json"
        message = {"content": content}
        if staff_id:
            message["note_staff_id"] = int(staff_id)
    else:
        url = f"{base_url()}/cases/{case_id}/messages.json"
        message = {"content": content}
        if staff_id:
            message["staff_id"] = int(staff_id)
    try:
        r = httpx.post(url, auth=_auth(), json={"message": message}, headers=headers, timeout=30)
        if r.status_code >= 400:
            log.error("Omnidesk post_reply %s: %s", r.status_code, r.text)
            return None
        return r.json()
    except Exception as e:  # noqa: BLE001
        log.error("Omnidesk post_reply error: %s", e)
        return None
