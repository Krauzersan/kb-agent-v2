"""RAG-ядро: поиск по базе знаний -> сборка контекста -> ответ выбранного LLM."""
from __future__ import annotations

import logging
import re

import db
import llm
import mapreduce
import reranker
import settings_store
import vectorstore

log = logging.getLogger("rag")

# ---------- роутер: агрегационные вопросы («перечисли все X», «список всех Y») ----------
#
# Обычный top-k векторный поиск отдаёт несколько ближайших по смыслу кусков — этого
# достаточно для точечного вопроса, но НЕ для вопроса, ответ на который размазан по
# многим разным файлам («какие есть кассовые ПО» — у каждой кассы свой файл).
# Для таких вопросов top-k почти всегда возвращает куски из 1-2 файлов и обрезает
# остальное. Ниже — детектор такого вопроса и два альтернативных пути ответа.
_AGG_RE = re.compile(
    r"перечисли|перечислите|весь список|полный список|список всех|список всей|"
    r"\bвсе\b|\bвсех\b|\bвсём\b|\bвсей\b|какие (есть|бывают|существуют)|"
    r"со всеми|полностью список",
    re.IGNORECASE,
)


def is_aggregation_question(question: str) -> bool:
    return bool(_AGG_RE.search(question or ""))


# ---------- фильтр «не путать кассовые системы» ----------
#
# У разных касс (IIKO, Эвотор, Frontol, R-Keeper, SetRetail, МойСклад, 1С, ...) свои
# инструкции по одним и тем же по смыслу операциям (например «списание бонусов») —
# они похожи семантически, поэтому обычный векторный поиск легко подмешивает статью
# про ДРУГУЮ кассу в контекст, а модель иногда путает их в ответе (может даже
# приложить не те скриншоты). Если вопрос явно называет ОДНУ систему — детерминированно
# выкидываем куски из статей про явно ДРУГУЮ систему из этого списка, до того как
# они попадут в контекст модели — так вместо промпт-инструкций получаем гарантию.
_POS_SYSTEMS = {
    "iiko": (r"\biiko\b",),
    "evotor": (r"эвотор", r"\bevotor\b"),
    "frontol": (r"frontol",),
    "rkeeper": (r"r-?keeper",),
    "setretail": (r"setretail",),
    "moysklad": (r"мо[йи]склад", r"мо[йи]\s+склад"),
    "1c": (r"\b1с\b", r"\b1c\b"),
    "restart": (r"рестарт",),
    "smartapteka": (r"смартаптека",),
    "moykassir": (r"moykassir", r"мо[йи]\s*кассир"),
    "retailcrm": (r"retailcrm",),
    "antisklad": (r"антисклад",),
    "xpos": (r"\bxpos\b", r"\bатол\b"),
    "spargo": (r"спарго",),
}


def _detect_pos_systems(text: str) -> set:
    text = (text or "").lower()
    found = set()
    for key, patterns in _POS_SYSTEMS.items():
        if any(re.search(p, text, re.IGNORECASE) for p in patterns):
            found.add(key)
    return found


def _filter_cross_system_hits(hits: list, query: str) -> list:
    """Если запрос называет РОВНО ОДНУ известную кассовую систему — выкидываем куски из
    файлов, которые явно про ДРУГУЮ систему (и не упоминают целевую вообще). Файлы без
    привязки к конкретной системе, а также файлы, где среди упомянутых систем есть и
    целевая (например совместное «Frontol или XPOS» — обе имеют право остаться, если
    спросили хотя бы про одну из них), не трогаем. Если запрос не называет систему
    однозначно (ни одной или несколько сразу) — ничего не фильтруем."""
    wanted = _detect_pos_systems(query)
    if len(wanted) != 1:
        return hits
    target = next(iter(wanted))
    filtered = []
    for h in hits:
        mentioned = _detect_pos_systems(h.get("filename") or "")
        if not mentioned or target in mentioned:
            filtered.append(h)
    return filtered or hits  # если отфильтровали всё подчистую — лучше отдать как было


def _answer_from_catalog(question: str, history=None, channel: str = "internal"):
    """Быстрый обход: вместо кусков по смыслу — краткое summary КАЖДОГО файла базы.

    Возвращает None, если каталог ещё не построен (см. db.catalog_entries()) —
    вызывающий код в этом случае обязан скатиться на обычный top-k поиск.
    """
    entries = db.catalog_entries()
    if not entries:
        return None
    hits = [
        {"filename": e["filename"], "text": e["summary"] or "", "file_id": e["id"],
         "score": 1.0, "priority": int(e.get("priority") or 0)}
        for e in entries
    ]
    try:
        answer = llm.ask(question, hits, history=history, channel=channel, mode="catalog")
    except llm.LLMNotConfigured as e:
        return {"answer": str(e), "sources": [], "hits": hits, "mode": "catalog"}
    sources, seen = [], set()
    for h in hits:
        if h["filename"] not in seen:
            seen.add(h["filename"])
            sources.append(h["filename"])
    return {"answer": answer, "sources": sources, "hits": hits, "mode": "catalog"}


def _search_query(question: str, history) -> str:
    """Короткая реплика-уточнение («я кассир», «да», «версия 8.7») сама по себе почти не
    несёт смысла для векторного поиска — тема разговора была задана в ПРЕДЫДУЩИХ репликах
    треда, а не в этой. Без контекста поиск уходит в сторону и находит что попало под руку,
    и модель (справедливо для того, что ей передали) может решить, что в базе ответа нет,
    хотя на самом деле он там есть — просто не был найден. Подмешиваем недавние реплики
    пользователя из истории треда в поисковый запрос (только для ПОИСКА — сам вопрос,
    который видит модель, не меняется)."""
    if not history:
        return question
    recent_user = [h.get("content", "") for h in history[-6:] if h.get("role") == "user"]
    recent_user = [c for c in recent_user if c][-2:]
    if not recent_user:
        return question
    return " ".join(recent_user + [question])


_RRF_K = 60  # стандартная константа RRF: чем больше, тем меньше давят позиции внизу списка


def _rrf_fuse(vector_hits: list, bm25_hits: list) -> list:
    """Reciprocal Rank Fusion двух НЕЗАВИСИМЫХ списков кандидатов — вектор и BM25 ищут
    каждый сам по себе, а не «BM25-рескор уже найденных вектором» (как было раньше).

    Разница принципиальна: кусок с точным лексическим совпадением (код ошибки, номер
    версии, название кассы), которого эмбеддинг вообще не занёс в top-N (а не просто
    ранжировал ниже) — раньше терялся безвозвратно, потому что BM25 умел только
    досчитать скор уже отобранным вектором кандидатам. Теперь BM25-топ — самостоятельный
    источник кандидатов, и такой кусок всё равно попадёт в объединённый пул.

    Фьюзим по РАНГАМ (не сырым скорам — косинус и bm25() в разных шкалах и не сравнимы
    напрямую): кандидат получает 1/(k + позиция + 1) от каждого списка, где он
    встретился; если он есть только в одном — считаем от одного. Итоговый rrf
    min-max-нормализуем в h["hybrid"], чтобы priority_boost (см. _search_with_priority)
    остался тем же мягким слагаемым на сравнимой шкале, что и раньше.
    """
    merged: dict = {}
    for rank, h in enumerate(vector_hits):
        key = (h.get("file_id"), h.get("chunk_index"))
        entry = merged.setdefault(key, dict(h))
        entry["rrf"] = entry.get("rrf", 0.0) + 1.0 / (_RRF_K + rank + 1)
    for rank, h in enumerate(bm25_hits):
        key = (h.get("file_id"), h.get("chunk_index"))
        entry = merged.get(key)
        if entry is None:
            entry = dict(h)  # найден ТОЛЬКО лексически — вектором вообще не отобран
            merged[key] = entry
        entry["rrf"] = entry.get("rrf", 0.0) + 1.0 / (_RRF_K + rank + 1)

    cand = list(merged.values())
    if not cand:
        return cand
    rrf_scores = [h["rrf"] for h in cand]
    lo, hi = min(rrf_scores), max(rrf_scores)
    span = (hi - lo) or 1.0
    for h in cand:
        h["hybrid"] = (h["rrf"] - lo) / span
    return cand


def _search_with_priority(question: str, top_k: int) -> list:
    """Если есть приоритетные файлы — мягко поднимаем их выше в выдаче.

    Берём больше кандидатов, приоритетным добавляем к близости небольшой буст,
    ре-ранкуем и берём top_k. Так приоритетные источники идут первыми, но при
    отсутствии в них релевантного — подмешиваются обычные (комбинирование).
    """
    prio_ids = db.priority_file_ids()          # берём из БД — всегда актуально
    try:
        boost = float(settings_store.get("priority_boost") or 0)
    except (TypeError, ValueError):
        boost = 0.05

    # Берём пул пошире top_k, чтобы после фильтра «чужих» кассовых систем всё равно
    # осталось из чего набрать top_k релевантных кусков. Вектор и BM25 ищут НЕЗАВИСИМО
    # (см. _rrf_fuse) — фильтр систем применяем уже к объединению обоих, не только
    # к вектору, иначе лексический кандидат из «чужой» кассы проскочит мимо фильтра.
    pool = max(top_k * 3, 15)
    vec_hits = vectorstore.search(question, top_k=pool)
    bm25_hits = db.search_fts(question, limit=pool)
    cand = _rrf_fuse(vec_hits, bm25_hits)
    cand = _filter_cross_system_hits(cand, question)

    # Кросс-энкодер реранкер (опционально, см. reranker.py) — точнее RRF, потому что
    # видит вопрос и кусок ВМЕСТЕ, а не как два независимых сигнала. Считаем relevance
    # только для уже отобранного узкого пула (cand), не для всей базы. Если выключен
    # или упал (модель не скачалась, память и т.п.) — используем RRF-скор как есть,
    # деградация мягкая, без потери ответа.
    if cand and settings_store.get("reranker_enabled"):
        try:
            scores = reranker.score(question, [h.get("text") or "" for h in cand])
            for h, s in zip(cand, scores):
                h["rerank"] = s
        except Exception:
            log.exception("Реранкер упал — используем RRF-скор без него")
            for h in cand:
                h["rerank"] = h["hybrid"]
    else:
        for h in cand:
            h["rerank"] = h["hybrid"]

    for h in cand:
        h["priority"] = 1 if h.get("file_id") in prio_ids else 0
        h["rank"] = h["rerank"] + (boost if (h["priority"] and boost > 0) else 0)
    cand.sort(key=lambda h: h["rank"], reverse=True)
    top = cand[:top_k]
    if prio_ids and boost > 0:
        log.info("Поиск: приоритетных файлов=%s, буст=%s, приоритетных в выдаче=%s из %s",
                 len(prio_ids), boost, sum(1 for h in top if h["priority"]), len(top))
    return top


def answer_question(question: str, history=None, channel: str = "internal",
                    thread_key: str = None, asker_user_id: int = None,
                    asker_name: str = None) -> dict:
    """Тонкая обёртка вокруг _answer_question — только чтобы залогировать вопрос и
    источники в query_log (отладочная панель), не трогая саму логику ответа ни в одном
    из её путей (обычный top-k, каталог, full-scan).

    thread_key/asker_* (Пачка) — для метрик: кто чаще спрашивает и как оценивает ответы.
    """
    result = _answer_question(question, history=history, channel=channel)
    try:
        _log_answer(question, channel, result, thread_key, asker_user_id, asker_name)
    except Exception:
        log.exception("Не удалось записать лог запроса")
    return result


def _log_answer(question: str, channel: str, result: dict, thread_key: str = None,
                asker_user_id: int = None, asker_name: str = None) -> None:
    hits = result.get("hits") or []
    sources, seen = [], set()
    for h in hits[:20]:
        key = (h.get("file_id"), h.get("chunk_index"))
        if key in seen:
            continue
        seen.add(key)
        sources.append({
            "file_id": h.get("file_id"),
            "filename": h.get("filename"),
            "chunk_index": h.get("chunk_index"),
            "score": round(h["score"], 4) if h.get("score") is not None else None,
            "priority": bool(h.get("priority")),
        })
    db.log_query(channel, result.get("mode") or "top_k", question or "",
                 result.get("answer") or "", sources,
                 thread_key=thread_key, asker_user_id=asker_user_id, asker_name=asker_name)


def _answer_question(question: str, history=None, channel: str = "internal") -> dict:
    """channel: "internal" — сотрудник (Пачка, панель), "external" — клиент (Omnidesk).

    Влияет на системный промпт: для external действует жёсткий запрет пересказывать
    чужие персональные данные, которые могут попасться в сырых переписках поддержки.
    """
    question = (question or "").strip()
    if not question:
        return {"answer": "Пустой вопрос.", "sources": []}

    # Агрегационный вопрос («перечисли все X») — обычный top-k поиск для него не годится,
    # т.к. ответ обычно размазан по многим файлам. Уходим в один из двух режимов каталога.
    if is_aggregation_question(question):
        mode = (settings_store.get("search_mode") or "auto").strip().lower()
        try:
            if mode == "full_scan":
                log.info("Агрегационный вопрос, режим full_scan: %s", question[:80])
                return mapreduce.answer(question, channel=channel)
            log.info("Агрегационный вопрос, режим catalog: %s", question[:80])
            result = _answer_from_catalog(question, history=history, channel=channel)
            if result is not None:
                return result
            log.info("Каталог ещё не построен — падаю обратно на обычный top-k поиск")
        except llm.LLMNotConfigured as e:
            return {"answer": str(e), "sources": [], "hits": []}
        except Exception:
            log.exception("Агрегационный роутер упал — падаю обратно на обычный top-k поиск")

    vectorstore.ensure_collection()
    top_k = int(settings_store.get("top_k"))
    hits = _search_with_priority(_search_query(question, history), top_k)

    # Предохранитель: если ничего достаточно похожего не нашлось — не даём модели
    # домысливать, а честно сообщаем, что в базе ответа нет.
    try:
        min_rel = float(settings_store.get("min_relevance") or 0)
    except (TypeError, ValueError):
        min_rel = 0.0
    if min_rel > 0:
        relevant = [h for h in hits if (h.get("score") or 0) >= min_rel]
        if not relevant:
            best = max((h.get("score") or 0) for h in hits) if hits else 0
            return {
                "answer": "В базе знаний нет информации по этому вопросу. "
                          "Уточните формулировку или обратитесь к коллеге.",
                "sources": [], "hits": hits, "best_score": best, "below_threshold": True,
            }
        hits = relevant

    try:
        answer = llm.ask(question, hits, history=history, channel=channel)
    except llm.LLMNotConfigured as e:
        return {"answer": str(e), "sources": [], "hits": hits}
    except Exception as e:  # noqa: BLE001
        # С фоллбэком между провайдерами (llm.py) сюда может долететь исключение НЕ
        # LLMNotConfigured — например если основной провайдер не настроен, а запасной
        # настроен, но упал с реальной ошибкой API. Раньше единственным исходом здесь
        # была LLMNotConfigured; ловим и остальное, чтобы не пробрасывать сырую ошибку
        # пользователю в Пачку/Telegram/WhatsApp мимо дружелюбного сообщения.
        log.exception("LLM не ответил ни через один из настроенных провайдеров")
        return {"answer": f"Не получилось получить ответ от модели: {e}", "sources": [], "hits": hits}

    # уникальные имена файлов-источников (по порядку)
    sources, seen = [], set()
    for h in hits:
        name = h["filename"]
        if name and name not in seen:
            seen.add(name)
            sources.append(name)

    return {"answer": answer, "sources": sources, "hits": hits}
