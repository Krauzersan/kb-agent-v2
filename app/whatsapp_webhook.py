"""Приём сообщений WhatsApp Cloud API (Meta) и ответ через RAG.

Базовый уровень: только текстовые сообщения. GET-запрос — обязательное разовое
подтверждение вебхука при подключении в кабинете Meta (hub.challenge). Проверка
подписи X-Hub-Signature-256 не реализована — добавьте перед продакшеном, если
этот эндпоинт будет смотреть в интернет без другой защиты (см. README).
"""
from __future__ import annotations

import hmac
import logging

from fastapi import APIRouter, BackgroundTasks, Request, Response

import convo
import rag
import settings_store
import whatsapp_client

log = logging.getLogger("whatsapp_webhook")
router = APIRouter()

# Дедупликация по id сообщения — Meta может повторно доставить событие.
_processed: set[str] = set()
_MAX_PROCESSED = 5000

_ERROR_MSG = "Не получилось ответить из-за внутренней ошибки, попробуйте ещё раз чуть позже."


def _handle(from_number: str, question: str) -> None:
    thread_key = f"whatsapp:{from_number}"
    try:
        history = convo.get(thread_key)
        result = rag.answer_question(question, history=history, channel="external",
                                     thread_key=thread_key, asker_user_id=from_number)
        answer = result["answer"]
        whatsapp_client.send_message(from_number, answer)
        convo.append(thread_key, "user", question)
        convo.append(thread_key, "assistant", answer)
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
                    background.add_task(_handle, from_number, text)
    except Exception:  # noqa: BLE001
        log.exception("Ошибка разбора WhatsApp webhook")

    return Response("OK", status_code=200)
