"""Приём вебхуков из Omnidesk (через «Правила») и ответ клиенту через RAG.

Omnidesk шлёт только case_id (число — безопасно, без проблем с экранированием).
Сам текст вопроса забираем через API, проверяем что последнее сообщение от клиента
(защита от циклов: на свой же ответ бот не реагирует) и отвечаем.

Важно: Omnidesk ждёт ответ за 5 секунд, поэтому отвечаем 200 сразу, обработку — в фон.
"""
from __future__ import annotations

import hmac
import json
import logging

from fastapi import APIRouter, BackgroundTasks, Request, Response

import omnidesk
import rag
import settings_store

log = logging.getLogger("omnidesk_webhook")
router = APIRouter()

# Дедупликация по (case_id, message_id) — чтобы не ответить дважды на одно сообщение.
_processed: set[tuple] = set()
_MAX_PROCESSED = 5000


def _process(case_id: int) -> None:
    try:
        if not omnidesk.configured():
            log.warning("Omnidesk не настроен — пропускаю обращение %s", case_id)
            return
        msg = omnidesk.last_message(case_id)
        if not msg or not omnidesk.is_user_message(msg):
            # последнее сообщение не от клиента (например, наш же ответ) — отвечать не на что
            return
        key = (case_id, msg.get("message_id"))
        if key in _processed:
            return
        if len(_processed) > _MAX_PROCESSED:
            _processed.clear()
        _processed.add(key)

        question = omnidesk.message_text(msg)
        if not question:
            return

        result = rag.answer_question(question, channel="external")
        answer = result["answer"]

        as_note = bool(settings_store.get("omnidesk_reply_as_note"))
        omnidesk.post_reply(case_id, answer, as_note=as_note)
    except Exception:  # noqa: BLE001
        log.exception("Ошибка обработки обращения Omnidesk %s", case_id)


@router.post("/webhook/omnidesk")
async def omnidesk_webhook(request: Request, background: BackgroundTasks):
    raw = await request.body()
    try:
        body = json.loads(raw or b"{}")
    except Exception:
        body = {}

    # Авторизация по общему токену (в URL ?token=, в теле или в заголовке X-Token).
    token = (settings_store.get("omnidesk_webhook_token") or "").strip()
    given = (request.query_params.get("token")
             or (body.get("token") if isinstance(body, dict) else None)
             or request.headers.get("X-Token"))
    if not token or not given or not hmac.compare_digest(str(given), token):
        return Response("forbidden", status_code=403)

    case_id = (body.get("case_id") if isinstance(body, dict) else None) \
        or request.query_params.get("case_id")
    if not case_id:
        # тестовый запрос Omnidesk при подключении вебхука — отвечаем 200
        return Response("OK", status_code=200)
    try:
        case_id = int(str(case_id).strip())
    except (TypeError, ValueError):
        return Response("bad case_id", status_code=200)

    background.add_task(_process, case_id)
    return Response("OK", status_code=200)
