"""Приём сообщений из Telegram (Bot API webhook) и ответ через RAG.

Базовый уровень: только текстовые сообщения на входе, без inline-кнопок. Картинки
из базы знаний (пометки «[изображение...] URL» в ответе) отправляются нативными
фото — Telegram сам скачивает их по прямой ссылке, заливать файл не нужно.
Без проверки secret_token (Telegram поддерживает его при setWebhook) — добавьте,
если этот эндпоинт будет смотреть в интернет без другой защиты (см. README).
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, BackgroundTasks, Request, Response

import convo
import img_markers
import rag
import settings_store
import telegram_client

log = logging.getLogger("telegram_webhook")
router = APIRouter()

# Дедупликация по update_id — Telegram может повторно доставить апдейт.
_processed: set[int] = set()
_MAX_PROCESSED = 5000

_GREETING = "Здравствуйте! Задайте вопрос — постараюсь ответить по базе знаний."
_ERROR_MSG = "Не получилось ответить из-за внутренней ошибки, попробуйте ещё раз чуть позже."
_DENIED_MSG = "Извините, у вас нет доступа к этому боту. Обратитесь к администратору."


def _parse_allowed(raw: str) -> set[str]:
    items = set()
    for chunk in (raw or "").replace(",", "\n").splitlines():
        v = chunk.strip().lstrip("@").lower()
        if v:
            items.add(v)
    return items


def _is_allowed(message: dict) -> bool:
    """Пусто в настройках = отвечаем всем (как раньше). Иначе сверяем числовой
    user_id и @username автора сообщения со списком в настройках."""
    allowed = _parse_allowed(settings_store.get("telegram_allowed_users"))
    if not allowed:
        return True
    frm = message.get("from") or {}
    user_id = str(frm.get("id") or "")
    username = (frm.get("username") or "").lower()
    return user_id in allowed or (username and username in allowed)


def _handle(chat_id: int, question: str) -> None:
    thread_key = f"telegram:{chat_id}"
    try:
        history = convo.get(thread_key)
        result = rag.answer_question(question, history=history, channel="external",
                                     thread_key=thread_key, asker_user_id=chat_id)
        answer = result["answer"]
        clean_text, images = img_markers.extract_images(answer)
        if clean_text:
            telegram_client.send_message(chat_id, clean_text)
        for img in images:
            telegram_client.send_photo(chat_id, img["url"], caption=img["caption"])
        convo.append(thread_key, "user", question)
        convo.append(thread_key, "assistant", clean_text)
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

    if not _is_allowed(message):
        # В фон — send_message синхронно бьёт по сети (httpx), держать на этом event loop
        # не нужно (см. как это уже сделано ниже для _handle).
        background.add_task(telegram_client.send_message, chat_id, _DENIED_MSG)
        return Response("OK", status_code=200)

    if text.startswith("/start"):
        background.add_task(telegram_client.send_message, chat_id, _GREETING)
        return Response("OK", status_code=200)

    background.add_task(_handle, chat_id, text)
    return Response("OK", status_code=200)
