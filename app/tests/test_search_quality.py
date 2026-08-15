"""Быстрые регрессионные тесты на 4 проблемы качества поиска, найденные в review.

Без pytest (в проекте пока нет тестовой инфраструктуры вообще) — просто скрипт,
каждая проверка воспроизводит конкретный баг и падает с понятным сообщением, если
он вернулся. Запуск: python3 tests/test_search_quality.py (из app/, с зависимостями
проекта — snowballstemmer, sentence-transformers и т.д. уже установлены).

Тест 4 монки-патчит embeddings.embed_query/embed_passages фиксированными векторами —
не грузит реальную модель, чтобы тест оставался быстрым и детерминированным. Раздел 1
(стемминг) и раздел 2 (overlap) гоняют реальный код без моков — оба самодостаточны,
сети/моделей не требуют.
"""
from __future__ import annotations

import os
import sys
import tempfile

# ПРИНУДИТЕЛЬНО, не setdefault: если DATA_DIR уже задан окружением (как в dev/prod
# контейнере — почти всегда), setdefault() не сработал бы, и тест выполнился бы на
# РЕАЛЬНОЙ базе (DELETE FROM files и т.п. — уже случалось, отсюда этот комментарий).
os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="kb-agent-test-")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

assert "db" not in sys.modules and "config" not in sys.modules, (
    "db/config уже импортированы до переопределения DATA_DIR — тест рискует попасть "
    "в реальную базу вместо временной. Не импортируйте db/config на уровне модуля выше."
)

FAILURES: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    status = "OK  " if condition else "FAIL"
    print(f"[{status}] {name}" + (f" — {detail}" if detail and not condition else ""))
    if not condition:
        FAILURES.append(name)


# ---------- 1. Стемминг FTS (db.py) ----------

def test_stemming():
    import db
    db.init_db()

    with db._conn() as con:
        con.execute("DELETE FROM chunks_fts")
        con.execute("DELETE FROM files")
        con.execute(
            "INSERT INTO files (id, filename, stored_path, status, created_at, updated_at) "
            "VALUES (1,'a.md','','ready','x','x')"
        )
        con.execute(
            "INSERT INTO files (id, filename, stored_path, status, created_at, updated_at) "
            "VALUES (2,'b.md','','ready','x','x')"
        )

    db.index_chunks_fts(1, ["Порядок списания бонусов с карты клиента после покупки"])
    db.index_chunks_fts(2, ["Инструкция по подключению кассового оборудования Эвотор"])

    # Ровно репро из тикета: "списать бонусы" должен находить кусок со "списание бонусов".
    hits = db.search_fts("как списать бонусы", limit=5)
    check("1a. 'списать бонусы' находит 'списание бонусов'",
          any(h["file_id"] == 1 for h in hits),
          f"hits={[(h['file_id'], h['filename']) for h in hits]}")

    # Обычные словоформы (падеж/число) — то, что стемминг чинит напрямую.
    hits = db.search_fts("кассы эвотор", limit=5)
    check("1b. 'кассы' (мн.ч./падеж) находит 'кассового' (ед.ч.)",
          any(h["file_id"] == 2 for h in hits),
          f"hits={[(h['file_id'], h['filename']) for h in hits]}")

    # Честная граница метода: словообразование (глагол -> отглагольное сущ.) стемминг
    # в принципе не решает — фиксируем это явно, чтобы не удивляться в будущем.
    stem_a = db._stemmer.stemWords(["списать"])[0]
    stem_b = db._stemmer.stemWords(["списание"])[0]
    check("1c. (документирует предел метода) 'списать'/'списание' — РАЗНЫЕ основы",
          stem_a != stem_b, f"'{stem_a}' vs '{stem_b}'")


# ---------- 2. Overlap кусков (ingest.py) ----------

def test_overlap():
    import ingest

    # Три абзаца, каждый заметно больше, чем нужно, чтобы гарантированно не влезть
    # в один chunk вместе — секция без явных заголовков идёт через _pack_lines.
    para = lambda word, n: " ".join([word] * n)
    text = "\n\n".join([
        para("Альфа", 60),
        para("Бета", 60),
        para("Гамма", 60),
    ])
    chunks = ingest.chunk_text(text)
    check("2a. текст разбился больше чем на 1 кусок", len(chunks) > 1, f"n={len(chunks)}")

    if len(chunks) > 1:
        overlaps = []
        for i in range(1, len(chunks)):
            prev_tail = chunks[i - 1][-200:]
            cur_head = chunks[i][:200]
            shared = any(
                prev_tail[j:j + 20] in cur_head
                for j in range(0, max(len(prev_tail) - 20, 1))
            )
            overlaps.append(shared)
        check("2b. каждый некст-кусок содержит хвост предыдущего", all(overlaps),
              f"overlaps={overlaps}")


# ---------- 3. Cap на кусков одного файла в top_k (rag.py) ----------

def test_per_file_cap():
    import rag

    # 10 кандидатов из файла 1 (высокий rank — как было бы при совпадении заголовка),
    # 3 кандидата из других файлов с более низким rank.
    cand = [{"file_id": 1, "chunk_index": i, "rank": 1.0 - i * 0.01} for i in range(10)]
    cand += [{"file_id": 100 + i, "chunk_index": 0, "rank": 0.5 - i * 0.01} for i in range(3)]
    cand.sort(key=lambda h: h["rank"], reverse=True)

    top = rag._cap_per_file(cand, top_k=8, cap=3)
    counts: dict = {}
    for h in top:
        counts[h["file_id"]] = counts.get(h["file_id"], 0) + 1

    check("3a. ни один файл не превышает cap=3", all(c <= 3 for c in counts.values()),
          f"counts={counts}")
    check("3b. в выдаче есть куски из других файлов, не только file_id=1",
          any(fid != 1 for fid in counts), f"counts={counts}")


# ---------- 4. min_relevance не обходится BM25-only кандидатами (rag.py) ----------

def test_min_relevance_backfill():
    import embeddings
    import rag

    orig_embed_query = embeddings.embed_query
    orig_embed_passages = embeddings.embed_passages
    # Фиксированные детерминированные векторы — не грузим реальную модель.
    embeddings.embed_query = lambda text: [1.0, 0.0]
    embeddings.embed_passages = lambda texts: [
        [0.9, 0.1] if "по_теме" in t else [0.0, 1.0] for t in texts
    ]
    try:
        hits = [
            {"file_id": 1, "chunk_index": 0, "text": "кусок по_теме вопроса", "score": None},
            {"file_id": 2, "chunk_index": 0, "text": "кусок совсем про другое", "score": None},
            {"file_id": 3, "chunk_index": 0, "text": "уже с вектором", "score": 0.95},
        ]
        rag._backfill_cosine(hits, "запрос")
        check("4a. досчитал score всем, у кого его не было",
              all(h.get("score") is not None for h in hits),
              f"scores={[h.get('score') for h in hits]}")
        relevant = [h for h in hits if (h.get("score") or 0) >= 0.5]
        check("4b. нерелевантный BM25-only кандидат отрезан порогом",
              all(h["file_id"] != 2 for h in relevant),
              f"survived={[h['file_id'] for h in relevant]}")
        check("4c. релевантный BM25-only кандидат прошёл порог",
              any(h["file_id"] == 1 for h in relevant),
              f"survived={[h['file_id'] for h in relevant]}")
    finally:
        embeddings.embed_query = orig_embed_query
        embeddings.embed_passages = orig_embed_passages


if __name__ == "__main__":
    test_stemming()
    test_overlap()
    test_per_file_cap()
    test_min_relevance_backfill()
    print()
    if FAILURES:
        print(f"FAILED: {len(FAILURES)} — {FAILURES}")
        sys.exit(1)
    print("Все проверки прошли.")
