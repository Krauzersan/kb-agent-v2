"""Диспетчер LLM-провайдеров: Claude / OpenAI (ChatGPT) / DeepSeek.

Провайдер выбирается в админ-панели (settings: llm_provider). Поиск по базе знаний
и эмбеддинги от провайдера НЕ зависят — меняется только генерация ответа.
"""
from __future__ import annotations

from typing import List

import claude_client
import gigachat_client
import openai_client
import settings_store

# Единое исключение «провайдер не настроен».
LLMNotConfigured = claude_client.ClaudeNotConfigured

PROVIDERS = ("claude", "openai", "deepseek", "gigachat")


def current_provider() -> str:
    p = (settings_store.get("llm_provider") or "claude").strip().lower()
    return p if p in PROVIDERS else "claude"


def ask(question: str, hits: List[dict], history=None, channel: str = "internal",
        mode: str = "normal") -> str:
    provider = current_provider()
    if provider == "openai":
        return openai_client.ask(question, hits, provider="openai", history=history,
                                  channel=channel, mode=mode)
    if provider == "deepseek":
        return openai_client.ask(question, hits, provider="deepseek", history=history,
                                  channel=channel, mode=mode)
    if provider == "gigachat":
        return gigachat_client.ask(question, hits, history=history, channel=channel, mode=mode)
    return claude_client.ask(question, hits, history=history, channel=channel, mode=mode)


def complete(system: str, user: str, max_tokens: int = 400) -> str:
    """Служебный вызов модели без роли/правил агента (summary файлов, map-reduce)."""
    provider = current_provider()
    if provider == "openai":
        return openai_client.complete(system, user, max_tokens=max_tokens, provider="openai")
    if provider == "deepseek":
        return openai_client.complete(system, user, max_tokens=max_tokens, provider="deepseek")
    if provider == "gigachat":
        return gigachat_client.complete(system, user, max_tokens=max_tokens)
    return claude_client.complete(system, user, max_tokens=max_tokens)
