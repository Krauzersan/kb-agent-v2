"""Классификация вопросов из лога по темам — для аналитики: о чём чаще всего
спрашивают сотрудники и какие темы стоит дополнить в базе знаний.

Работает через тот же LLM, что отвечает агенту (llm.complete) — отдельного
провайдера/ключа не требует. Разметка — по кнопке в панели (не в реальном времени
при каждом вопросе: это добавило бы задержку и стоимость к каждому живому ответу).
"""
from __future__ import annotations

import json
import logging
import re

import db
import llm
import progress

log = logging.getLogger("topics")

_BATCH_SIZE = 25
_FALLBACK_TOPIC = "Не определено"

_SYSTEM = (
    "Ты — классификатор вопросов сотрудников службы поддержки MAXMA. Для каждого "
    "вопроса определи короткую тему (2-4 слова, с большой буквы, в именительном "
    "падеже — например «Начисление баллов», «Настройка кассы», «Промокоды»). "
    "Похожие по смыслу вопросы должны получать ОДНУ и ту же тему, слово в слово. "
    "Если среди уже существующих тем есть подходящая — используй её как есть, не "
    "придумывай новую формулировку для того же смысла. Если вопрос действительно "
    "про новую тему — придумай короткое название в том же стиле.\n\n"
    "Ответь СТРОГО JSON-массивом без пояснений и без markdown: "
    '[{"id": 1, "topic": "Название темы"}, ...] — по одному элементу на каждый '
    "вопрос, id должны точно совпадать с входными."
)

_JSON_RE = re.compile(r"\[.*\]", re.DOTALL)


def _parse_response(text: str) -> dict:
    m = _JSON_RE.search(text or "")
    if not m:
        raise ValueError("в ответе модели не найден JSON-массив")
    data = json.loads(m.group(0))
    out = {}
    for item in data:
        try:
            out[int(item["id"])] = str(item["topic"]).strip()[:80]
        except (KeyError, TypeError, ValueError):
            continue
    return out


def classify_batch(rows: list) -> dict:
    """rows: [{"id":.., "question":..}] -> {id: topic}."""
    existing = db.topics_list()
    known = ("Уже существующие темы (используй их, если вопрос подходит по смыслу): "
             + ", ".join(existing) + "\n\n") if existing else ""
    items = "\n".join(f"{r['id']}: {(r['question'] or '').strip()[:400]}" for r in rows)
    user = f"{known}Вопросы:\n{items}"
    reply = llm.complete(_SYSTEM, user, max_tokens=2000)
    return _parse_response(reply)


def backfill(batch_size: int = _BATCH_SIZE) -> dict:
    """Размечает темой все ещё неразмеченные вопросы в логе. {done, errors}."""
    total = db.count_untagged()
    progress.start("Анализ тем", total)
    done = errors = 0
    try:
        while True:
            ids = db.untagged_query_ids(batch_size)
            if not ids:
                break
            rows = db.query_rows_by_ids(ids)
            try:
                mapping = classify_batch(rows)
            except llm.LLMNotConfigured:
                log.error("Анализ тем остановлен: LLM не настроен")
                raise
            except Exception:
                log.exception("Не удалось классифицировать пачку вопросов (%s шт.) — "
                              "помечаю как «%s»", len(rows), _FALLBACK_TOPIC)
                mapping = {}
            for r in rows:
                topic = mapping.get(r["id"]) or _FALLBACK_TOPIC
                db.set_topic(r["id"], topic)
                if topic == _FALLBACK_TOPIC:
                    errors += 1
                else:
                    done += 1
                progress.step()
        log.info("Анализ тем завершён: готово %s, ошибок %s", done, errors)
    finally:
        progress.finish()
    return {"done": done, "errors": errors}
