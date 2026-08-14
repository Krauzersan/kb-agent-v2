"""Разбор пометок «[изображение...] URL» в ответе агента — общий формат для всех
каналов (см. claude_client._IMG_HINT). Regex — единственный источник истины для
этого формата, webhook.py (Пачка) импортирует его отсюда же вместо своей копии.

Пачка грузит картинку как вложение сама (webhook.py, свой code path), а вот
Telegram и WhatsApp умеют принимать прямую ссылку на картинку — им не нужно её
скачивать и заливать заново, только выдёргивать разметку из текста.
"""
from __future__ import annotations

import re

import settings_store

IMG_LINK_RE = re.compile(r'\[изображение(?::\s*([^\]]*))?\]\s+(\S+)')


def extract_images(text: str) -> tuple[str, list[dict]]:
    """Убирает из текста пометки [изображение...] URL и возвращает (чистый_текст,
    [{"caption": str, "url": str}, ...]) — но только для ссылок на НАШИ же картинки
    (public_base_url + /assets/...). Модель отвечает по инструкции копировать ссылку
    из выдержки символ в символ и не выдумывать чужих — но раз мы всё равно отдаём
    эту ссылку сторонним серверам (Telegram/WhatsApp сами её скачают), тот же барьер,
    что уже стоит у Пачки (см. webhook.py: _extract_and_upload_images), нужен и тут:
    не наша ссылка — оставляем как есть текстом, а не отправляем как картинку."""
    base = (settings_store.get("public_base_url") or "").rstrip("/")
    assets_prefix = f"{base}/assets/" if base else None
    images: list[dict] = []

    def _repl(m: re.Match) -> str:
        url = m.group(2)
        if not assets_prefix or not url.startswith(assets_prefix):
            return m.group(0)  # не наша картинка — не трогаем, оставляем текстом
        images.append({"caption": (m.group(1) or "").strip(), "url": url})
        return ""

    clean = IMG_LINK_RE.sub(_repl, text or "")
    clean = re.sub(r"\n{3,}", "\n\n", clean).strip()
    return clean, images
