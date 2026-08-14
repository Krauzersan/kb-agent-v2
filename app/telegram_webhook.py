"""Приём сообщений из Telegram (Bot API webhook) и ответ через RAG.

Базовый уровень: только текстовые сообщения, без inline-кнопок, без вложений.
Без проверки secret_token (Telegram поддерживает его при setWebhook) — добавьте,
если этот эндпоинт будет смотреть в интернет без другой защиты (см. README).
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, BackgroundTasks, Request, Response

import convo
import rag
import telegram_client

log = logging.getLogger("telegram_webhook")
router = APIRouter()

# Дедупликация по update_id — Telegram может повторно доставить апдейт.
_processed: set[int] = set()
_MAX_PROCESSED = 5000

_GREETING = "Здравствуйте! Задайте вопрос — постараюсь ответить по базе знаний."
_ERROR_MSG = "Не получилось ответить из-за внутренней ошибки, попробуйте ещё раз чуть позже."


def _handle(chat_id: int, question: str) -> None:
    thread_key = f"telegram:{chat_id}"
    try:
        history = convo.get(thread_key)
        result = rag.answer_question(question, history=history, channel="external",
                                     thread_key=thread_key, asker_user_id=chat_id)
        answer = result["answer"]
        telegram_client.send_message(chat_id, answer)
        convo.append(thread_key, "user", question)
        convo.append(thread_key, "assistant", answer)
    except Exception:  # noqa: BLE001
        log.exception("Ошибка обработки Telegram-сообщения")
        telegram_client.send_message(chat_id, _ERROR_MSG)


@router.post("/webhook/telegram")
async def telegram_webhook(request: Request, background: BackgroundTasks):
    try:
        update = await request.json()
    except Exception:
        return Response("bad json", status_code=200)  # не просим повтор

    update_id = update.get("update_id")
    if update_id is not None:
        if update_id in _processed:
            return Response("dup", status_code=200)
        if len(_processed) > _MAX_PROCESSED:
            _processed.clear()
        _processed.add(update_id)

    message = update.get("message") or update.get("edited_message")
    if not message:
        return Response("ignored", status_code=200)

    chat_id = (message.get("chat") or {}).get("id")
    text = (message.get("text") or "").strip()
    if not chat_id or not text:
        return Response("ignored", status_code=200)

    if text.startswith("/start"):
        telegram_client.send_message(chat_id, _GREETING)
        return Response("OK", status_code=200)

    background.add_task(_handle, chat_id, text)
    return Response("OK", status_code=200)
