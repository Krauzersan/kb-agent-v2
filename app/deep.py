"""Режим DEEP (см. router.py): сложные технические вопросы и ТЗ.

Обычный top-k поиск отдаёт несколько похожих кусков текста — этого достаточно для
точечного вопроса, но не для «составь ТЗ на интеграцию с X» или «сравни варианты Y и Z
и предложи архитектуру»: в одном сложном вопросе на самом деле несколько разных подтем,
и по каждой нужны СВОИ факты из базы, а не общий top-k по вопросу целиком.

Конвейер: декомпозиция на подвопросы -> поиск фактов по каждому -> план ответа по
собранным фактам -> генерация текста по каждому разделу плана отдельным вызовом ->
финальная сборка разделов в один связный ответ. Ощутимо дороже и медленнее обычного
поиска (десяток+ LLM-вызовов вместо одного) — это осознанный компромисс для вопросов,
которые роутер (router.classify) в принципе не отправил бы сюда, будь они попроще.

answer() возвращает None, если декомпозиция не удалась или вопрос оказался недостаточно
сложным для неё (см. _decompose) — вызывающий код (rag._answer_question) в этом случае
обязан упасть на обычный FAST-путь, а не показать пользователю ошибку.
"""
from __future__ import annotations

import json
import logging
import re

import llm

log = logging.getLogger("deep")

_DECOMPOSE_SYSTEM = (
    "Разбей сложный вопрос пользователя на 3-6 более простых, самостоятельных подвопросов, "
    "по каждому из которых можно отдельно искать факты в базе знаний. Каждый подвопрос — "
    "конкретный и годный для отдельного поиска (не общая фраза вроде «детали» или "
    "«нюансы»), но КОРОТКИЙ — одна фраза до 12 слов, это поисковый запрос, а не пересказ. "
    "Если вопрос на самом деле простой и раскладывать нечего — верни массив из одного "
    "этого же вопроса.\n\n"
    "Ответь СТРОГО в формате JSON-массива строк, без пояснений и без markdown-разметки "
    "(без ```). Пример: [\"Как настроить X\", \"Какие ограничения у Y\", \"Как связаны X и Z\"]"
)

_PLAN_SYSTEM = (
    "Тебе дан сложный вопрос пользователя и факты, найденные в базе знаний по его подтемам "
    "(сгруппированы по подвопросам). Составь план развёрнутого структурированного ответа — "
    "список разделов. Раздел — это реально отдельная смысловая часть вопроса, не дроби "
    "искусственно; разделов должно быть от 2 до 6. Для каждого раздела укажи КОРОТКИЙ "
    "заголовок (2-5 слов) и одну короткую фразу (до 15 слов), что именно в нём раскрыть — "
    "не пересказывай сами факты, только тему раздела.\n\n"
    "Ответь СТРОГО в формате JSON-массива объектов {\"title\": ..., \"focus\": ...}, без "
    "пояснений и без markdown-разметки (без ```)."
)

_MAX_SUBQUESTIONS = 6
_PER_SUB_TOP_K = 6
_FACTS_DIGEST_LIMIT = 12000   # символов — контекст план-шага, не весь пул фактов целиком
_SECTION_TEXT_LIMIT = 400     # символов на факт в дайджесте для плана (не для генерации разделов)
# Декомпозиция/план — структурированный JSON, не финальный текст, поэтому это не тот же
# reasoning-бюджет, что у ask() ниже. Но и тут нужен запас: 6 подвопросов/разделов на
# русском — это не 400 токенов, а куда больше, и модель, отрезанная на середине строки,
# отдаёт НЕВАЛИДНЫЙ JSON (Unterminated string), а не просто короткий ответ — весь шаг
# падает, а не деградирует мягко. См. также инструкцию отвечать короче в самом промпте.
_DECOMPOSE_MAX_TOKENS = 900
_PLAN_MAX_TOKENS = 1200
# Настройка панели claude_max_tokens рассчитана на обычный короткий ответ. При высоком
# effort (см. claude_client.ask) reasoning-модель может истратить весь этот бюджет на
# размышления, не оставив места на видимый текст раздела — тогда придёт пустой ответ.
# Даём генерации раздела и финальной сборке собственный, заведомо больший бюджет.
_SECTION_MAX_TOKENS = 3000
_ASSEMBLE_MAX_TOKENS = 4000


def _extract_json_array(text: str) -> str:
    """Находит ПАРНУЮ закрывающую скобку для первой "[" (считая вложенность и пропуская
    скобки внутри строковых литералов) — а не rfind("]"), который хватает ПОСЛЕДНЮЮ "]"
    во всём тексте. Если модель дописывает пояснение после массива (несмотря на
    инструкцию отвечать строго JSON) и это пояснение само содержит "]" — rfind включает
    в срез весь мусор между настоящим концом массива и этой ложной скобкой, и json.loads
    падает с "Extra data" на валидном по сути массиве."""
    start = text.find("[")
    if start == -1:
        return text
    depth = 0
    in_string = False
    escape = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    return text[start:]  # не сбалансировано — пусть json.loads сам поднимет ошибку


def _parse_json(raw: str):
    """Модели иногда оборачивают JSON в ```json ... ``` или дописывают пояснение до/после
    массива, несмотря на инструкцию отвечать строго JSON — вырезаем сам массив."""
    raw = (raw or "").strip()
    raw = re.sub(r"^```(?:json)?\s*|\s*```\s*$", "", raw, flags=re.IGNORECASE)
    return json.loads(_extract_json_array(raw))


def _decompose(question: str) -> list[str]:
    try:
        raw = llm.complete(_DECOMPOSE_SYSTEM, question, max_tokens=_DECOMPOSE_MAX_TOKENS)
        items = _parse_json(raw)
        subs = [str(s).strip() for s in items if str(s).strip()]
    except Exception:
        log.exception("DEEP: декомпозиция вопроса упала")
        return []
    return subs[:_MAX_SUBQUESTIONS]


def _plan(question: str, facts_digest: str) -> list[dict]:
    user = f"Вопрос: {question}\n\nНайденные факты по подтемам:\n\n{facts_digest}"
    try:
        raw = llm.complete(_PLAN_SYSTEM, user, max_tokens=_PLAN_MAX_TOKENS)
        items = _parse_json(raw)
    except Exception:
        log.exception("DEEP: построение плана упало")
        return []
    sections = []
    for it in items:
        if not isinstance(it, dict):
            continue
        title = str(it.get("title") or "").strip()
        focus = str(it.get("focus") or "").strip()
        if title:
            sections.append({"title": title, "focus": focus or title})
    return sections[:_MAX_SUBQUESTIONS]


def _generate_section(question: str, section: dict, hits: list[dict], channel: str) -> str | None:
    prompt = (f"Вопрос целиком (для контекста, отвечать не на него, а на раздел ниже): "
              f"{question}\n\nРаздел, который нужно написать: {section['title']}\n"
              f"Что раскрыть: {section['focus']}")
    try:
        return llm.ask(prompt, hits, history=None, channel=channel, mode="deep_section",
                       max_tokens=_SECTION_MAX_TOKENS)
    except Exception:
        log.exception("DEEP: не удалось сгенерировать раздел «%s»", section["title"])
        return None


def _assemble(question: str, drafts: list[str], channel: str) -> str:
    fake_hits = [{"filename": f"Раздел {i + 1}", "text": d, "priority": 0}
                 for i, d in enumerate(drafts)]
    prompt = (f"Вот черновики отдельных разделов развёрнутого ответа на вопрос «{question}». "
              f"Собери из них один связный итоговый ответ.")
    return llm.ask(prompt, fake_hits, history=None, channel=channel, mode="deep_assemble",
                   max_tokens=_ASSEMBLE_MAX_TOKENS)


def answer(question: str, history=None, channel: str = "internal") -> dict | None:
    import rag  # ленивый импорт — rag.py импортирует deep на уровне модуля, разрываем цикл
    import vectorstore

    vectorstore.ensure_collection()
    search_question = rag._search_query(question, history)
    subquestions = _decompose(search_question)
    if len(subquestions) < 2:
        log.info("DEEP: вопрос не разложился на подвопросы — падаю на FAST")
        return None

    log.info("DEEP: разложил вопрос на %s подвопросов: %s", len(subquestions), subquestions)

    all_hits: list[dict] = []
    seen_keys = set()
    facts_parts = []
    for sq in subquestions:
        hits, _ = rag._search_with_priority(sq, _PER_SUB_TOP_K)
        block = []
        for h in hits:
            key = (h.get("file_id"), h.get("chunk_index"))
            if key not in seen_keys:
                seen_keys.add(key)
                all_hits.append(h)
            block.append(f"- {(h.get('text') or '')[:_SECTION_TEXT_LIMIT]}")
        if block:
            facts_parts.append(f"### {sq}\n" + "\n".join(block))

    if not all_hits:
        return {
            "answer": "По этому вопросу не нашлось релевантной информации в базе знаний.",
            "sources": [], "hits": [], "mode": "deep",
        }

    facts_digest = "\n\n".join(facts_parts)[:_FACTS_DIGEST_LIMIT]
    sections = _plan(question, facts_digest)
    if not sections:
        # план не собрался — запасной вариант: по разделу на каждый подвопрос
        sections = [{"title": s, "focus": s} for s in subquestions]

    log.info("DEEP: план из %s разделов", len(sections))

    drafts = []
    for sec in sections:
        text = _generate_section(question, sec, all_hits, channel)
        if text:
            drafts.append(f"**{sec['title']}**\n{text}")

    if not drafts:
        log.warning("DEEP: ни один раздел не сгенерировался — падаю на FAST")
        return None

    try:
        final = _assemble(question, drafts, channel).strip()
    except Exception:
        log.exception("DEEP: финальная сборка упала — отдаю разделы как есть")
        final = "\n\n".join(drafts)

    sources, seen_names = [], set()
    for h in all_hits:
        name = h.get("filename")
        if name and name not in seen_names:
            seen_names.add(name)
            sources.append(name)

    return {"answer": final, "sources": sources, "hits": all_hits, "mode": "deep"}
