"""Диспетчер LLM-провайдеров: Claude / OpenAI (ChatGPT) / DeepSeek / GigaChat.

Провайдер выбирается в админ-панели (settings: llm_provider). Поиск по базе знаний
и эмбеддинги от провайдера НЕ зависят — меняется только генерация ответа.

Фоллбэк: если выбранный провайдер не ответил (не настроен, упал лимит, недоступен,
любая ошибка) — автоматически пробуем следующий НАСТРОЕННЫЙ провайдер (у которого
есть API-ключ), в фиксированном порядке. Если не настроен вообще ни один — ведём
себя как раньше: поднимается LLMNotConfigured для выбранного провайдера, вызывающий
код (rag.py и т.п.) уже умеет её показывать пользователю понятным сообщением.
"""
from __future__ import annotations

import logging
from typing import List

import claude_client
import gigachat_client
import openai_client
import settings_store

log = logging.getLogger("llm")

# Единое исключение «провайдер не настроен».
LLMNotConfigured = claude_client.ClaudeNotConfigured

PROVIDERS = ("claude", "openai", "deepseek", "gigachat")

# Ключ в settings_store, по которому проверяем, что у провайдера вообще есть шанс
# ответить (без сетевого вызова — просто "ключ не пуст").
_PROVIDER_KEY = {
    "claude": "anthropic_api_key",
    "openai": "openai_api_key",
    "deepseek": "deepseek_api_key",
    "gigachat": "gigachat_auth_key",
}


def current_provider() -> str:
    p = (settings_store.get("llm_provider") or "claude").strip().lower()
    return p if p in PROVIDERS else "claude"


def _configured_providers() -> list[str]:
    return [p for p in PROVIDERS if (settings_store.get(_PROVIDER_KEY[p]) or "").strip()]


def _fallback_order() -> list[str]:
    """Выбранный в настройках провайдер — первым, дальше остальные настроенные
    (с непустым ключом), в порядке PROVIDERS. Если не настроен НИ ОДИН — всё равно
    возвращаем [текущий], чтобы сохранилось привычное сообщение «провайдер не настроен»,
    а не путаница из нескольких одинаковых ошибок подряд."""
    primary = current_provider()
    configured = _configured_providers()
    if not configured:
        return [primary]
    order = [primary] if primary in configured else []
    order += [p for p in PROVIDERS if p in configured and p != primary]
    return order


def _dispatch_ask(provider: str, question: str, hits: List[dict], history, channel: str,
                  mode: str) -> str:
    if provider == "openai":
        return openai_client.ask(question, hits, provider="openai", history=history,
                                  channel=channel, mode=mode)
    if provider == "deepseek":
        return openai_client.ask(question, hits, provider="deepseek", history=history,
                                  channel=channel, mode=mode)
    if provider == "gigachat":
        return gigachat_client.ask(question, hits, history=history, channel=channel, mode=mode)
    return claude_client.ask(question, hits, history=history, channel=channel, mode=mode)


def _dispatch_complete(provider: str, system: str, user: str, max_tokens: int) -> str:
    if provider == "openai":
        return openai_client.complete(system, user, max_tokens=max_tokens, provider="openai")
    if provider == "deepseek":
        return openai_client.complete(system, user, max_tokens=max_tokens, provider="deepseek")
    if provider == "gigachat":
        return gigachat_client.complete(system, user, max_tokens=max_tokens)
    return claude_client.complete(system, user, max_tokens=max_tokens)


def ask(question: str, hits: List[dict], history=None, channel: str = "internal",
        mode: str = "normal") -> str:
    order = _fallback_order()
    last_exc: Exception | None = None
    for i, provider in enumerate(order):
        try:
            answer = _dispatch_ask(provider, question, hits, history=history,
                                   channel=channel, mode=mode)
            if i > 0:
                log.warning("LLM-фоллбэк: %s не ответил, ответ получен от %s",
                           order[0], provider)
            return answer
        except Exception as e:  # noqa: BLE001
            last_exc = e
            more = f" — пробую следующий ({order[i + 1]})" if i + 1 < len(order) else ""
            log.warning("Провайдер %s не ответил (%s)%s", provider, e, more)
    raise last_exc


def complete(system: str, user: str, max_tokens: int = 400) -> str:
    """Служебный вызов модели без роли/правил агента (summary файлов, map-reduce)."""
    order = _fallback_order()
    last_exc: Exception | None = None
    for i, provider in enumerate(order):
        try:
            answer = _dispatch_complete(provider, system, user, max_tokens)
            if i > 0:
                log.warning("LLM-фоллбэк (complete): %s не ответил, ответ получен от %s",
                           order[0], provider)
            return answer
        except Exception as e:  # noqa: BLE001
            last_exc = e
            more = f" — пробую следующий ({order[i + 1]})" if i + 1 < len(order) else ""
            log.warning("Провайдер %s не ответил (%s)%s", provider, e, more)
    raise last_exc
