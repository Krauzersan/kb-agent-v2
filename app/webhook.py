"""Приём событий из Пачки (исходящий webhook), проверка подписи и ответ через RAG.

Секрет webhook и настройки берутся из админ-панели (settings_store).
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import re
import time
import urllib.parse

from fastapi import APIRouter, BackgroundTasks, Request, Response

import convo
import db
import pachca
import rag
import settings_store
from config import settings

log = logging.getLogger("webhook")
router = APIRouter()

# Ссылка на наши же картинки (из ZIP-импорта) в формате «[изображение: подпись] URL».
_IMG_LINK_RE = re.compile(r'\[изображение(?::\s*([^\]]*))?\]\s+(\S+)')

# Пачка не рендерит markdown-заголовки (#, ##) и горизонтальные линии (---) — показывает
# их как есть, решётками/чёрточками. В промпте это запрещено, но модель время от времени
# всё равно копирует такую разметку из исходных .md-файлов базы (там # — обычное дело).
# Подчищаем детерминированно, не полагаясь только на то, что модель всегда послушается.
_MD_HEADER_RE = re.compile(r'^#{1,6}[ \t]+(.*)$', re.MULTILINE)
_MD_HR_RE = re.compile(r'^[ \t]*(-{3,}|_{3,}|\*{3,})[ \t]*$', re.MULTILINE)


def _sanitize_formatting(text: str) -> str:
    """Заголовки # -> **жирный текст**, горизонтальные линии --- вырезаются."""
    def _header_repl(m):
        content = m.group(1).strip()
        if content.startswith("**") and content.endswith("**"):
            return content  # уже жирный — не дублируем звёздочки
        return f"**{content}**"

    text = _MD_HEADER_RE.sub(_header_repl, text)
    text = _MD_HR_RE.sub("", text)
    return text


def _extract_and_upload_images(answer: str) -> tuple[str, list]:
    """Ищет в ответе ссылки на НАШИ картинки (settings_store.public_base_url + /assets/…),
    грузит их в Пачку и убирает ссылку из текста — вместо неё картинка придёт настоящим
    вложением. Если для конкретной ссылки что-то не срослось (не наша, не найден файл,
    не удалось загрузить) — просто оставляет её как есть, текстовой ссылкой."""
    if not bool(settings_store.get("pachca_native_images")):
        return answer, []
    base = (settings_store.get("public_base_url") or "").rstrip("/")
    if not base:
        return answer, []
    assets_prefix = base + "/assets/"
    assets_root = os.path.realpath(settings.ASSETS_DIR)
    files: list = []

    def _repl(m):
        caption, url = (m.group(1) or "").strip(), m.group(2)
        if not url.startswith(assets_prefix):
            return m.group(0)
        rel = urllib.parse.unquote(url[len(assets_prefix):])
        local_path = os.path.realpath(os.path.join(settings.ASSETS_DIR, *rel.split("/")))
        # защита от path traversal (../../etc/passwd и т.п.) — путь должен остаться внутри ASSETS_DIR
        if local_path != assets_root and not local_path.startswith(assets_root + os.sep):
            return m.group(0)
        if not os.path.isfile(local_path):
            return m.group(0)
        uploaded = pachca.upload_file(local_path, os.path.basename(local_path), file_type="image")
        if not uploaded:
            return m.group(0)  # не вышло — оставляем ссылку текстом, картинка не теряется
        files.append(uploaded)
        return caption

    new_answer = _IMG_LINK_RE.sub(_repl, answer).strip()
    return new_answer, files

# Простейшая дедупликация (Пачка доставляет «минимум один раз»).
_processed: set[str] = set()
_MAX_PROCESSED = 5000

# Бот просит оценить ответ числом 1-10 в том же треде — распознаём такую реплику.
_RATING_RE = re.compile(r"^\s*(10|[1-9])\s*$")
_RATING_PROMPT = "\n\n_Оцените ответ от 1 до 10 — просто отправьте число сюда 🙏_"

# Имя автора вопроса по user_id (для метрик) — Пачка не кладёт имя в вебхук,
# приходится дотягивать через API; кэшируем, чтобы не дёргать её на каждый вопрос.
_name_cache: dict = {}
_MAX_NAME_CACHE = 2000


def _resolve_asker_name(user_id) -> str | None:
    if not user_id:
        return None
    if user_id in _name_cache:
        return _name_cache[user_id]
    user = pachca.get_user(user_id)
    name = None
    if user:
        full = f"{(user.get('first_name') or '').strip()} {(user.get('last_name') or '').strip()}".strip()
        name = full or user.get("nickname") or None
    name = name or f"id{user_id}"
    if len(_name_cache) > _MAX_NAME_CACHE:
        _name_cache.clear()
    _name_cache[user_id] = name
    return name

# ID сообщений, отправленных самим ботом — чтобы НЕ отвечать на свои же ответы
# (защита от зацикливания, даже если в Пачке выключено «Игнорировать свои сообщения»).
_sent_ids: set = set()
_MAX_SENT = 5000


def _remember_sent(resp) -> None:
    try:
        mid = (resp or {}).get("data", {}).get("id")
        if mid:
            if len(_sent_ids) > _MAX_SENT:
                _sent_ids.clear()
            _sent_ids.add(mid)
    except Exception:
        pass


def _verify_signature(raw_body: bytes, signature: str | None) -> bool:
    secret = settings_store.get("pachca_webhook_secret")
    if not secret or not signature:
        return False
    expected = hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


def _event_key(event: dict) -> str:
    return f"{event.get('type')}:{event.get('event')}:{event.get('id') or event.get('message_id') or ''}"


# Служебные (системные) сообщения Пачки — на них бот отвечать не должен.
_SYSTEM_RE = re.compile(
    r"добавил[аи]?\s.+\sв\s(беседу|канал|чат)"
    r"|удалил[аи]?\s.+\sиз\s(беседы|канала|чата)"
    r"|исключил[аи]?\s.+\sиз\s(беседы|канала|чата)"
    r"|покинул[аи]?\s(беседу|канал|чат)"
    r"|вышел\sиз\s(беседы|канала|чата)"
    r"|вступил[аи]?\sв\s(беседу|канал|чат)"
    r"|присоединил[аи]?с[яь]\sк\s(беседе|каналу|чату)"
    r"|создал[аи]?\s(беседу|канал)"
    r"|переименовал"
    r"|изменил[аи]?\s(название|описание|аватар)",
    re.IGNORECASE,
)


def _is_system_message(content: str) -> bool:
    return bool(_SYSTEM_RE.search(content or ""))


def _thread_root_question(event: dict) -> str | None:
    """Текст исходного сообщения, на котором открыт тред (первостепенный вопрос).

    В Пачке это родительское сообщение — оно лежит в родительском чате и в историю
    треда не попадает. Берём его id из объекта thread.message_id (или parent_message_id)
    и дотягиваем через API.
    """
    th = event.get("thread") or {}
    root_id = th.get("message_id") or event.get("parent_message_id")
    if not root_id or root_id == event.get("id"):
        return None
    msg = pachca.get_message(root_id)
    if not msg:
        return None
    content = (msg.get("content") or "").strip()
    if not content or _is_system_message(content):
        return None
    return content


def _thread_history(event: dict) -> list:
    """История треда как список реплик {role, content} — для продолжения диалога."""
    chat_id = event.get("chat_id")
    if not chat_id:
        return []
    msgs = pachca.get_chat_messages(chat_id, limit=50)
    bot_id = pachca.bot_user_id()
    asker = event.get("user_id")
    current_id = event.get("id")
    log.info("Тред: chat_id=%s получено сообщений=%s bot_id=%s", chat_id, len(msgs), bot_id)
    history = []
    for m in msgs:
        if m.get("id") == current_id:
            continue  # текущее сообщение добавим отдельно как вопрос
        content = (m.get("content") or "").strip()
        if not content:
            continue
        uid = m.get("user_id")
        if bot_id is not None:
            role = "assistant" if uid == bot_id else "user"
        else:
            # пока не знаем id бота: реплики автора вопроса — user, прочие — assistant
            role = "user" if uid == asker else "assistant"
        history.append({"role": role, "content": content})
    # Сохраняем первый вопрос треда (первостепенный) + последние реплики,
    # чтобы бот держал исходную задачу, но не раздувал контекст.
    if len(history) > 10:
        history = history[:1] + history[-9:]
    return history


def _handle_question(event: dict) -> None:
    """Фоновая обработка: поставить индикатор, спросить RAG, ответить, снять индикатор."""
    message_id = event.get("id")
    question = (event.get("content") or "").strip()
    is_thread = event.get("entity_type") == "thread"

    # Реплика внутри треда — это не новый вопрос, а оценка предыдущего ответа?
    if is_thread and bool(settings_store.get("pachca_ask_rating")):
        m = _RATING_RE.match(question)
        if m:
            thread_key = f"thread:{event.get('entity_id')}"
            if db.set_rating(thread_key, int(m.group(1))):
                pachca.send_message("thread", event.get("entity_id"), "Спасибо, учёл! 🙌")
                return
            # неоценённого ответа в этом треде нет — не мешаем, ведём себя как обычно

    indicator = settings_store.get("reaction_indicator")
    if indicator and message_id:
        pachca.add_reaction(message_id, indicator)
    ctx_on = bool(settings_store.get("pachca_thread_context"))
    thread_replies_on = bool(settings_store.get("pachca_thread_replies"))

    # Новый вопрос вне треда + включена «отвечать тредом» -> создаём тред на этом
    # сообщении (идемпотентно — если уже есть, вернётся тот же) и дальше ведём себя
    # так же, как для родного pachca-треда. Старое поведение (флаг выключен) не меняется.
    created_thread_id = None
    if not is_thread and thread_replies_on and message_id:
        th = pachca.create_thread(message_id)
        if th and th.get("id"):
            created_thread_id = th["id"]
        else:
            log.warning("Не удалось создать тред на сообщении %s — отвечаю как раньше", message_id)

    if is_thread:
        thread_key = f"thread:{event.get('entity_id')}"
    elif created_thread_id:
        thread_key = f"thread:{created_thread_id}"
    else:
        thread_key = None

    try:
        history = None
        if thread_key and ctx_on:
            if is_thread:
                history = convo.get(thread_key)          # наша надёжная память диалога
                if not history:                          # первый ответ — подхватим историю из API
                    history = _thread_history(event)
                # Исходный вопрос треда лежит в родительском чате — всегда подмешиваем его
                # первым, иначе бот «не помнит», с чего начался тред.
                root_q = _thread_root_question(event)
                if root_q and not any((h.get("content") or "") == root_q for h in (history or [])):
                    history = [{"role": "user", "content": root_q}] + (history or [])
                log.info("Тред: root-вопрос=%s | parent_id=%s | thread=%s",
                         (root_q or "—")[:60], event.get("parent_message_id"), event.get("thread"))
            else:
                # created_thread_id: тред только что создан — текущий вопрос сам корневой,
                # предыдущей истории по определению нет.
                history = convo.get(thread_key)
        log.info("Пачка входящее: entity_type=%s entity_id=%s thread_key=%s ctx=%s "
                 "новый_тред=%s реплик_в_истории=%s",
                 event.get("entity_type"), event.get("entity_id"), thread_key,
                 ctx_on, bool(created_thread_id), len(history) if history else 0)

        asker_name = _resolve_asker_name(event.get("user_id"))
        result = rag.answer_question(question, history=history, channel="internal",
                                     thread_key=thread_key, asker_user_id=event.get("user_id"),
                                     asker_name=asker_name)
        answer = result["answer"]
        answer = _sanitize_formatting(answer)
        answer, img_files = _extract_and_upload_images(answer)

        # Куда отвечаем: свежесозданный тред -> в него; родной тред -> в тот же тред;
        # иначе (легаси) -> в чат ответом на сообщение.
        target_thread_id = created_thread_id or (event.get("entity_id") if is_thread else None)
        # Просьбу оценить ответ можно привязать к оценке только внутри треда (см.
        # db.set_rating выше — ищет по thread_key), поэтому вне треда её не показываем.
        answer_out = answer
        if target_thread_id and thread_key and bool(settings_store.get("pachca_ask_rating")):
            answer_out = answer + _RATING_PROMPT
        if target_thread_id:
            _remember_sent(pachca.send_message("thread", target_thread_id, answer_out, files=img_files))
        else:
            _remember_sent(pachca.send_message("discussion", event.get("chat_id"), answer_out,
                                               parent_message_id=message_id, files=img_files))

        # Запоминаем ход диалога — чтобы бот помнил свой ответ.
        if thread_key and ctx_on:
            convo.append(thread_key, "user", question)
            convo.append(thread_key, "assistant", answer)
    except Exception as e:  # noqa: BLE001
        log.exception("Ошибка обработки вопроса")
        target = event.get("chat_id") or event.get("entity_id")
        if target:
            pachca.send_message("discussion", target,
                                f"Не получилось ответить из-за внутренней ошибки: {e}",
                                parent_message_id=message_id)
    finally:
        if indicator and message_id:
            pachca.remove_reaction(message_id, indicator)


@router.post("/webhook/pachca")
async def pachca_webhook(request: Request, background: BackgroundTasks):
    raw = await request.body()

    # 1. Подпись
    if not _verify_signature(raw, request.headers.get("Pachca-Signature")):
        return Response("invalid signature", status_code=401)

    # 2. Необязательная проверка IP отправителя.
    # За обратным прокси реальный IP — ПОСЛЕДНИЙ элемент X-Forwarded-For.
    if settings_store.get("pachca_verify_ip"):
        allowed = settings_store.get("pachca_allowed_ip")
        xff = request.headers.get("X-Forwarded-For", "")
        client_ip = (xff.split(",")[-1].strip() if xff
                     else (request.client.host if request.client else ""))
        if client_ip and allowed and client_ip != allowed:
            log.warning("Webhook с чужого IP: %s", client_ip)
            return Response("forbidden ip", status_code=403)

    try:
        event = json.loads(raw)
    except Exception:
        return Response("bad json", status_code=200)  # не просим повтор

    # 3. Защита от replay-атак (±60 секунд)
    ts = event.get("webhook_timestamp", 0)
    if abs(time.time() - ts) > 60:
        return Response("expired", status_code=401)

    # 4. Реагируем только на новые сообщения
    if event.get("type") == "message" and event.get("event") == "new":
        # Защита от петли: не отвечаем на сообщения, которые отправили сами.
        if event.get("id") in _sent_ids:
            return Response("own", status_code=200)
        key = _event_key(event)
        if key in _processed:
            return Response("dup", status_code=200)
        if len(_processed) > _MAX_PROCESSED:
            _processed.clear()
        _processed.add(key)

        content = (event.get("content") or "").strip()
        if not content:
            return Response("empty", status_code=200)
        if _is_system_message(content):
            log.info("Пропускаю системное сообщение: %s", content[:80])
            return Response("system", status_code=200)
        # Отвечаем 200 сразу, обработку выносим в фон.
        background.add_task(_handle_question, event)

    return Response("OK", status_code=200)
