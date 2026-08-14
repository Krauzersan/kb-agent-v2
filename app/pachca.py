"""Клиент REST API Пачки: отправка сообщений и реакций от имени бота.

Токен и базовый URL берутся из настроек (вводятся в админ-панели).
"""
from __future__ import annotations

import logging
import mimetypes
import os

import httpx

import settings_store

log = logging.getLogger("pachca")

# ID самого бота (узнаём из ответа на отправку сообщения) — чтобы в истории треда
# отличать реплики бота от реплик сотрудников.
_bot_user_id = None


def bot_user_id():
    return _bot_user_id


def _api_url() -> str:
    return settings_store.get("pachca_api_url")


def get_message(message_id: int) -> dict | None:
    """Одно сообщение по id (нужно, чтобы достать исходный вопрос треда из родительского чата)."""
    if not message_id:
        return None
    try:
        r = httpx.get(f"{_api_url()}/messages/{message_id}", headers=_headers(), timeout=20)
        if r.status_code >= 400:
            log.error("Pachca get_message %s: %s", r.status_code, r.text)
            return None
        return r.json().get("data")
    except Exception as e:  # noqa: BLE001
        log.error("Pachca get_message error: %s", e)
        return None


def get_user(user_id: int) -> dict | None:
    """Профиль сотрудника по id (для метрик: имя автора вопроса — в webhook-событии
    Пачки есть только user_id, имя нужно дотягивать отдельным запросом)."""
    if not user_id:
        return None
    try:
        r = httpx.get(f"{_api_url()}/users/{user_id}", headers=_headers(), timeout=15)
        if r.status_code >= 400:
            log.error("Pachca get_user %s: %s", r.status_code, r.text)
            return None
        return r.json().get("data")
    except Exception as e:  # noqa: BLE001
        log.error("Pachca get_user error: %s", e)
        return None


def get_chat_messages(chat_id: int, limit: int = 20) -> list:
    """Последние сообщения чата/треда в хронологическом порядке (старые -> новые)."""
    try:
        # order=desc — сначала самые свежие; затем разворачиваем в хронологию.
        r = httpx.get(f"{_api_url()}/messages", headers=_headers(),
                      params={"chat_id": chat_id, "order": "desc", "limit": limit}, timeout=30)
        if r.status_code >= 400:
            log.error("Pachca get_messages %s: %s", r.status_code, r.text)
            return []
        data = r.json().get("data", []) or []
        return list(reversed(data))
    except Exception as e:  # noqa: BLE001
        log.error("Pachca get_messages error: %s", e)
        return []


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {settings_store.get('pachca_bot_token')}",
        "Content-Type": "application/json",
    }


def get_upload_params() -> dict | None:
    """Первый шаг загрузки файла: подпись и параметры для прямой загрузки в S3."""
    try:
        r = httpx.post(f"{_api_url()}/uploads", headers=_headers(), timeout=20)
        if r.status_code >= 400:
            log.error("Pachca get_upload_params %s: %s", r.status_code, r.text)
            return None
        return r.json()
    except Exception as e:  # noqa: BLE001
        log.error("Pachca get_upload_params error: %s", e)
        return None


def upload_file(local_path: str, filename: str, file_type: str = "file") -> dict | None:
    """Загружает локальный файл в Пачку (presigned S3, 3 шага) и возвращает словарь,
    готовый для message.files[]. Для file_type="image" сама читает width/height —
    без них Пачка отклоняет вложение (422)."""
    params = get_upload_params()
    if not params:
        return None
    direct_url = params.get("direct_url")
    key = (params.get("key") or "").replace("${filename}", filename)
    if not direct_url or not key:
        log.error("Pachca upload_file: неожиданный ответ /uploads: %s", params)
        return None
    form_fields = {k: v for k, v in params.items() if k not in ("direct_url", "key")}
    form_fields["key"] = key
    try:
        with open(local_path, "rb") as f:
            content_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
            # Поле file должно идти последним в multipart — httpx кладёт files после data.
            r = httpx.post(direct_url, data=form_fields,
                           files={"file": (filename, f, content_type)}, timeout=60)
        if r.status_code not in (200, 201, 204):
            log.error("Pachca upload_file (S3) %s: %s", r.status_code, r.text[:300])
            return None
    except Exception as e:  # noqa: BLE001
        log.error("Pachca upload_file error: %s", e)
        return None

    result = {"key": key, "name": filename, "file_type": file_type,
              "size": os.path.getsize(local_path)}
    if file_type == "image":
        try:
            from PIL import Image
            with Image.open(local_path) as img:
                result["width"], result["height"] = img.size
        except Exception:
            log.warning("Pachca upload_file: не удалось прочитать размеры картинки %s", filename)
            return None
    return result


def send_message(entity_type: str, entity_id: int, content: str,
                 parent_message_id: int | None = None, files: list | None = None) -> dict | None:
    """Отправить сообщение. entity_type: 'discussion' (чат/канал), 'thread' или 'user'."""
    message = {"entity_type": entity_type, "entity_id": entity_id, "content": content}
    if parent_message_id:
        message["parent_message_id"] = parent_message_id
    if files:
        message["files"] = files
    try:
        r = httpx.post(f"{_api_url()}/messages",
                       headers=_headers(), json={"message": message}, timeout=30)
        if r.status_code >= 400:
            log.error("Pachca send_message %s: %s", r.status_code, r.text)
            return None
        resp = r.json()
        global _bot_user_id
        try:
            uid = (resp.get("data") or {}).get("user_id")
            if uid:
                _bot_user_id = uid
        except Exception:
            pass
        return resp
    except Exception as e:  # noqa: BLE001
        log.error("Pachca send_message error: %s", e)
        return None


def create_thread(message_id: int) -> dict | None:
    """Создать тред на сообщении (POST /messages/{id}/thread).

    Если тред на этом сообщении уже есть — Пачка вернёт информацию о нём же (эндпоинт
    идемпотентный). Возвращает data с полями id (для send_message/get_chat_messages)
    и chat_id, либо None при ошибке.
    """
    try:
        r = httpx.post(f"{_api_url()}/messages/{message_id}/thread",
                       headers=_headers(), timeout=20)
        if r.status_code >= 400:
            log.error("Pachca create_thread %s: %s", r.status_code, r.text)
            return None
        return r.json().get("data")
    except Exception as e:  # noqa: BLE001
        log.error("Pachca create_thread error: %s", e)
        return None


def add_reaction(message_id: int, name: str) -> None:
    # Для кастомной реакции-индикатора (agent-thinking) Пачка ждёт параметр `name`.
    if not name:
        return
    try:
        httpx.post(f"{_api_url()}/messages/{message_id}/reactions",
                   headers=_headers(), json={"name": name}, timeout=15)
    except Exception as e:  # noqa: BLE001
        log.warning("Pachca add_reaction error: %s", e)


def remove_reaction(message_id: int, name: str) -> None:
    if not name:
        return
    try:
        httpx.request("DELETE", f"{_api_url()}/messages/{message_id}/reactions",
                      headers=_headers(), json={"name": name}, timeout=15)
    except Exception as e:  # noqa: BLE001
        log.warning("Pachca remove_reaction error: %s", e)
