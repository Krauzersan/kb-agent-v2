"""Локальная модель эмбеддингов (multilingual-e5). Работает на CPU, без интернета после загрузки.

Важно: модели семейства e5 требуют префиксы:
  - поисковый запрос  -> "query: ..."
  - кусок документа   -> "passage: ..."
"""
from __future__ import annotations

import threading
from typing import List

from config import settings

_model = None
_dim = None
_load_lock = threading.Lock()


def _get_model():
    """Ленивая загрузка модели — чтобы старт приложения был быстрым."""
    global _model, _dim
    if _model is None:
        with _load_lock:
            if _model is None:
                from sentence_transformers import SentenceTransformer  # тяжёлый импорт

                model = SentenceTransformer(settings.EMBEDDING_MODEL, device="cpu")
                _dim = int(model.get_sentence_embedding_dimension())
                _model = model
    return _model


def vector_size() -> int:
    _get_model()
    return _dim


def embed_passages(texts: List[str]) -> List[List[float]]:
    model = _get_model()
    prefixed = [f"passage: {t}" for t in texts]
    vecs = model.encode(
        prefixed, normalize_embeddings=True, batch_size=16, show_progress_bar=False
    )
    return [v.tolist() for v in vecs]


def embed_query(text: str) -> List[float]:
    model = _get_model()
    vec = model.encode(
        [f"query: {text}"], normalize_embeddings=True, show_progress_bar=False
    )[0]
    return vec.tolist()
