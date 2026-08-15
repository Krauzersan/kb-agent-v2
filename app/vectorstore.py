"""Клиент к Qdrant (настоящий сервер, свой процесс — не встроенное файловое хранилище).

Сервер сам безопасно обрабатывает конкурентные запросы, поэтому лок нужен НЕ для
защиты Qdrant, а для наших собственных многошаговых операций (check-then-create в
ensure_collection, delete+recreate в wipe_all) — сериализуем их между собой, чтобы
не словить состояние гонки в СВОЕЙ логике. Чтение (search, get_file_chunks) — один
запрос без побочных эффектов, лока не требует и не держит.
"""
from __future__ import annotations

import os
import threading
import uuid
from typing import List

from qdrant_client import QdrantClient
from qdrant_client.http import models as qm

import embeddings
from config import settings

_client: QdrantClient | None = None
_lock = threading.Lock()


def client() -> QdrantClient:
    global _client
    if _client is None:
        # Настоящий Qdrant-сервер (быстрые поиск и удаление). Локально, порт 6333.
        _client = QdrantClient(url=settings.QDRANT_URL, timeout=60)
    return _client


def ensure_collection() -> None:
    """Создаёт коллекцию, если её ещё нет. Размер вектора берём из модели."""
    with _lock:
        c = client()
        size = embeddings.vector_size()
        existing = {col.name for col in c.get_collections().collections}
        if settings.QDRANT_COLLECTION not in existing:
            c.create_collection(
                collection_name=settings.QDRANT_COLLECTION,
                vectors_config=qm.VectorParams(size=size, distance=qm.Distance.COSINE),
            )
            try:
                c.create_payload_index(
                    collection_name=settings.QDRANT_COLLECTION,
                    field_name="file_id",
                    field_schema=qm.PayloadSchemaType.INTEGER,
                )
            except Exception:
                # в локальном режиме индекс не обязателен — фильтрация работает и без него
                pass


def embedding_title(filename: str) -> str:
    """Последний сегмент «хлебных крошек» имени файла — заголовок статьи, без папок/
    канала. Расплывчатый вопрос про ТЕМУ файла («как настроить акцию») обычно не
    находит его куски: сам заголовок встречается один раз в начале файла, а не в
    каждом куске, — общих слов с вопросом у куска может просто не быть. Подмешиваем
    заголовок в текст ПЕРЕД эмбеддингом (не в то, что хранится/показывается моделью
    как выдержка — только для расчёта вектора)."""
    name = filename.rsplit(".", 1)[0] if "." in filename else filename
    parts = [p.strip() for p in name.split(" - ") if p.strip()]
    return parts[-1] if parts else name


def add_chunks(file_id: int, filename: str, chunks: List[str], on_progress=None,
               priority: int = 0) -> int:
    """Индексирует куски файла пачками (эмбеддинг + запись в Qdrant).

    on_progress(n) вызывается после каждой пачки — для прогресс-бара по кускам.
    Эмбеддинг делаем ВНЕ лока (тяжёлый CPU), запись в Qdrant — под локом.
    """
    if not chunks:
        return 0
    batch = 64
    prio = 1 if priority else 0
    c = client()
    added = 0
    title = embedding_title(filename)
    for i in range(0, len(chunks), batch):
        sub = chunks[i:i + batch]
        embed_input = [f"{title}\n\n{chunk}" if title else chunk for chunk in sub]
        vectors = embeddings.embed_passages(embed_input)   # вне лока
        points = [
            qm.PointStruct(
                id=str(uuid.uuid4()),
                vector=vec,
                payload={"file_id": file_id, "filename": filename,
                         "chunk_index": i + j, "text": chunk, "priority": prio},
            )
            for j, (chunk, vec) in enumerate(zip(sub, vectors))
        ]
        with _lock:
            c.upsert(collection_name=settings.QDRANT_COLLECTION, points=points)
        added += len(points)
        if on_progress:
            try:
                on_progress(len(points))
            except Exception:
                pass
    return added


def delete_file(file_id: int) -> None:
    with _lock:
        client().delete(
            collection_name=settings.QDRANT_COLLECTION,
            points_selector=qm.FilterSelector(
                filter=qm.Filter(
                    must=[qm.FieldCondition(key="file_id", match=qm.MatchValue(value=file_id))]
                )
            ),
        )


def set_file_priority(file_id: int, value: int) -> None:
    """Обновить приоритет всех кусков файла в индексе (без переиндексации)."""
    with _lock:
        client().set_payload(
            collection_name=settings.QDRANT_COLLECTION,
            payload={"priority": 1 if value else 0},
            points=qm.Filter(
                must=[qm.FieldCondition(key="file_id", match=qm.MatchValue(value=int(file_id)))]
            ),
        )


def wipe_all() -> None:
    """Полностью очищает индекс ЭТОГО инстанса: удаляет и пересоздаёт коллекцию пустой.

    Трогает только settings.QDRANT_COLLECTION — коллекцию текущего процесса (v2),
    поэтому безопасно даже если на одном Qdrant-сервере рядом крутится другой инстанс
    со своей коллекцией (например продакшн — knowledge_base).
    """
    with _lock:
        c = client()
        try:
            c.delete_collection(collection_name=settings.QDRANT_COLLECTION)
        except Exception:
            pass
    ensure_collection()


def delete_files(file_ids) -> None:
    """Удалить векторы сразу нескольких файлов ОДНИМ запросом (быстро)."""
    ids = [int(i) for i in file_ids]
    if not ids:
        return
    with _lock:
        client().delete(
            collection_name=settings.QDRANT_COLLECTION,
            points_selector=qm.FilterSelector(
                filter=qm.Filter(
                    must=[qm.FieldCondition(key="file_id", match=qm.MatchAny(any=ids))]
                )
            ),
        )


def get_file_chunks(file_id: int, limit: int = 1500) -> List[dict]:
    """Все проиндексированные куски одного файла (для просмотра текста в панели).

    Возвращает список {chunk_index, text} по порядку. Читаем страницами через scroll.
    """
    out: List[dict] = []
    flt = qm.Filter(
        must=[qm.FieldCondition(key="file_id", match=qm.MatchValue(value=int(file_id)))]
    )
    next_off = None
    c = client()
    while len(out) < limit:
        points, next_off = c.scroll(
            collection_name=settings.QDRANT_COLLECTION,
            scroll_filter=flt,
            with_payload=True,
            with_vectors=False,
            limit=min(256, limit - len(out)),
            offset=next_off,
        )
        for p in points:
            out.append({
                "chunk_index": p.payload.get("chunk_index", 0),
                "text": p.payload.get("text", ""),
            })
        if not next_off or not points:
            break
    out.sort(key=lambda x: x["chunk_index"])
    return out


def search(query: str, top_k: int) -> List[dict]:
    vec = embeddings.embed_query(query)
    hits = client().search(
        collection_name=settings.QDRANT_COLLECTION,
        query_vector=vec,
        limit=top_k,
        with_payload=True,
    )
    return [
        {
            "score": h.score,
            "text": h.payload.get("text", ""),
            "filename": h.payload.get("filename", ""),
            "file_id": h.payload.get("file_id"),
            "chunk_index": h.payload.get("chunk_index"),
            "priority": int(h.payload.get("priority", 0) or 0),
        }
        for h in hits
    ]
