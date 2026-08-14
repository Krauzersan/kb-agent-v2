"""Приём сообщений WhatsApp Cloud API (Meta) и ответ через RAG.

Базовый уровень: только текстовые сообщения на входе. Картинки из базы знаний
(пометки «[изображение...] URL» в ответе) отправляются нативным вложением — Meta
сама скачивает их по прямой ссылке, заливать файл не нужно.

Без памяти диалога — намеренно: каждый вопрос обрабатывается сам по себе, без
истории треда (в отличие от Пачки, см. webhook.py). Раньше память была, но
приводила к тому, что агент зацикливался на одном и том же вопросе; thread_key
всё ещё передаётся в rag.answer_question — но только для группировки в логе/
метриках, на сам ответ модели он не влияет.

GET-запрос — обязательное разовое подтверждение вебхука при подключении в кабинете
Meta (hub.challenge). Проверка подписи X-Hub-Signature-256 не реализована —
добавьте перед продакшеном, если этот эндпоинт будет смотреть в интернет без
другой защиты (см. README).
"""
from __future__ import annotations

import hmac
import logging

from fastapi import APIRouter, BackgroundTasks, Request, Response

import img_markers
import rag
import settings_store
import whatsapp_client

log = logging.getLogger("whatsapp_webhook")
router = APIRouter()

# Дедупликация по id сообщения — Meta может повторно доставить событие.
_processed: set[str] = set()
_MAX_PROCESSED = 5000

_ERROR_MSG = "Не получилось ответить из-за внутренней ошибки, попробуйте ещё раз чуть позже."
_DENIED_MSG = "Извините, у вас нет доступа к этому боту. Обратитесь к администратору."


def _parse_allowed(raw: str) -> set[str]:
    items = set()
    for chunk in (raw or "").replace(",", "\n").splitlines():
        v = chunk.strip().lstrip("+")
        if v:
            items.add(v)
    return items


def _is_allowed(from_number: str) -> bool:
    """Пусто в настройках = отвечаем всем (как раньше). Иначе сверяем номер
    отправителя (как его прислал WhatsApp — цифры без "+") со списком в настройках."""
    allowed = _parse_allowed(settings_store.get("whatsapp_allowed_numbers"))
    return not allowed or from_number in allowed


def _handle(from_number: str, question: str) -> None:
    thread_key = f"whatsapp:{from_number}"  # только для группировки в логе/метриках, не память
    try:
        result = rag.answer_question(question, channel="external",
                                     thread_key=thread_key, asker_user_id=from_number)
        answer = result["answer"]
        clean_text, images = img_markers.extract_images(answer)
        if clean_text:
            whatsapp_client.send_message(from_number, clean_text)
        for img in images:
            whatsapp_client.send_image(from_number, img["url"], caption=img["caption"])
    except Exception:  # noqa: BLE001
        log.exception("Ошибка обработки WhatsApp-сообщения")
        whatsapp_client.send_message(from_number, _ERROR_MSG)


@router.get("/webhook/whatsapp")
async def whatsapp_verify(request: Request):
    """Разовое подтверждение адреса вебхука при подключении в кабинете Meta."""
    mode = request.query_params.get("hub.mode")
    token = request.query_params.get("hub.verify_token") or ""
    challenge = request.query_params.get("hub.challenge") or ""
    expected = (settings_store.get("whatsapp_verify_token") or "").strip()
    if mode == "subscribe" and expected and hmac.compare_digest(token, expected):
        return Response(challenge, status_code=200)
    return Response("forbidden", status_code=403)


@router.post("/webhook/whatsapp")
async def whatsapp_webhook(request: Request, background: BackgroundTasks):
    try:
        body = await request.json()
    except Exception:
        return Response("bad json", status_code=200)  # не просим повтор

    try:
        for entry in body.get("entry") or []:
            for change in entry.get("changes") or []:
                value = change.get("value") or {}
                for msg in value.get("messages") or []:
                    msg_id = msg.get("id")
                    if msg_id:
                        if msg_id in _processed:
                            continue
                        if len(_processed) > _MAX_PROCESSED:
                            _processed.clear()
                        _processed.add(msg_id)

                    if msg.get("type") != "text":
                        continue
                    from_number = msg.get("from")
                    text = (msg.get("text") or {}).get("body", "").strip()
                    if not from_number or not text:
                        continue
                    if not _is_allowed(from_number):
                        background.add_task(whatsapp_client.send_message, from_number, _DENIED_MSG)
                        continue
                    background.add_task(_handle, from_number, text)
    except Exception:  # noqa: BLE001
        log.exception("Ошибка разбора WhatsApp webhook")

    return Response("OK", status_code=200)
