"""Разбор пометок «[изображение...] URL» в ответе агента — общий формат для всех
каналов (см. claude_client._IMG_HINT). Пачка грузит картинку как вложение сама
(webhook.py), а вот Telegram и WhatsApp умеют принимать прямую ссылку на картинку —
им не нужно её скачивать и заливать заново, только выдёргивать разметку из текста.
"""
from __future__ import annotations

import re

_IMG_LINK_RE = re.compile(r'\[изображение(?::\s*([^\]]*))?\]\s+(\S+)')


def extract_images(text: str) -> tuple[str, list[dict]]:
    """Убирает из текста пометки [изображение...] URL, возвращает (чистый_текст,
    [{"caption": str, "url": str}, ...]) в порядке появления."""
    images: list[dict] = []

    def _repl(m: re.Match) -> str:
        images.append({"caption": (m.group(1) or "").strip(), "url": m.group(2)})
        return ""

    clean = _IMG_LINK_RE.sub(_repl, text or "")
    clean = re.sub(r"\n{3,}", "\n\n", clean).strip()
    return clean, images
