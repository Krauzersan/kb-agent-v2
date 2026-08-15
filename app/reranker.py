"""Кросс-энкодер реранкер поверх уже отобранного пула кандидатов (см. rag._rrf_fuse).

В отличие от эмбеддинга (вопрос и кусок кодируются НЕЗАВИСИМО друг от друга в два
вектора, похожесть — просто косинус между ними), кросс-энкодер видит вопрос и кусок
ВМЕСТЕ, в одном проходе модели — точнее ранжирует, но дороже по CPU. Поэтому
применяется не ко всей базе, а только к узкому пулу (top-N после RRF), который уже
отобран дешёвыми методами (вектор + BM25).
"""
from __future__ import annotations

import math
import threading
from typing import List

from config import settings

_model = None
_load_lock = threading.Lock()


def _get_model():
    """Ленивая загрузка модели — чтобы старт приложения был быстрым."""
    global _model
    if _model is None:
        with _load_lock:
            if _model is None:
                from sentence_transformers import CrossEncoder  # тяжёлый импорт

                _model = CrossEncoder(settings.RERANKER_MODEL, device="cpu")
    return _model


def score(query: str, texts: List[str]) -> List[float]:
    """Релевантность 0..1 каждого текста относительно query — сигмоида от сырых
    логитов кросс-энкодера (так документирует постобработку модель-карта bge-reranker)."""
    if not texts:
        return []
    model = _get_model()
    pairs = [[query, t] for t in texts]
    raw = model.predict(pairs, show_progress_bar=False)
    return [1.0 / (1.0 + math.exp(-float(s))) for s in raw]
