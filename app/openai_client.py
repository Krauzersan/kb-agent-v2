"""Ответы через OpenAI (ChatGPT) и DeepSeek. Оба используют OpenAI-совместимый API."""
from __future__ import annotations

from typing import List

import httpx
import openai

import settings_store
from claude_client import ClaudeNotConfigured, _system_prompt, build_user_message

# Клиенты кэшируются по (ключ, base_url, proxy).
_clients: dict = {}

# «Думающие» модели, которым можно отправлять reasoning_effort (на обычных чат-моделях
# этот параметр не поддерживается и может вернуть ошибку). OpenAI: с сентября 2024
# max_tokens везде заменён на max_completion_tokens (max_tokens ещё работает как
# устаревший алиас, но лучше сразу слать актуальный параметр). DeepSeek — наоборот,
# max_tokens остаётся как есть.
_OPENAI_REASONING_PREFIX = "gpt-5"
_DEEPSEEK_REASONING_MODELS = {"deepseek-reasoner", "deepseek-v4-pro"}


def _provider_kwargs(provider: str, model: str, max_tokens: int) -> dict:
    if provider == "deepseek":
        kwargs = {"max_tokens": max_tokens}
        if model in _DEEPSEEK_REASONING_MODELS:
            effort = (settings_store.get("deepseek_effort") or "").strip()
            if effort:
                kwargs["reasoning_effort"] = effort
        return kwargs
    kwargs = {"max_completion_tokens": max_tokens}
    if model.startswith(_OPENAI_REASONING_PREFIX):
        effort = (settings_store.get("openai_effort") or "").strip()
        if effort:
            kwargs["reasoning_effort"] = effort
    return kwargs


def _get_client(api_key: str, base_url: str | None):
    proxy = (settings_store.get("claude_proxy") or "").strip()
    key = (api_key, base_url, proxy)
    if key not in _clients:
        http_client = None
        if proxy:
            http_client = httpx.Client(
                proxy=proxy,
                timeout=httpx.Timeout(connect=30.0, read=120.0, write=30.0, pool=30.0),
            )
        _clients[key] = openai.OpenAI(api_key=api_key, base_url=base_url,
                                      http_client=http_client, max_retries=2)
    return _clients[key]


def ask(question: str, hits: List[dict], provider: str = "openai", history=None,
        channel: str = "internal", mode: str = "normal", max_tokens: int | None = None) -> str:
    if provider == "deepseek":
        api_key = (settings_store.get("deepseek_api_key") or "").strip()
        base_url = "https://api.deepseek.com"
        model = settings_store.get("deepseek_model")
        name = "DeepSeek"
    else:
        api_key = (settings_store.get("openai_api_key") or "").strip()
        base_url = None  # штатный OpenAI
        model = settings_store.get("openai_model")
        name = "OpenAI"

    if not api_key:
        raise ClaudeNotConfigured(
            f"Ключ {name} не задан. Откройте «Настройки» и введите API-ключ {name}."
        )

    client = _get_client(api_key, base_url)
    max_tokens = max_tokens or int(settings_store.get("claude_max_tokens"))
    user_content = build_user_message(question, hits, channel=channel)
    messages = [{"role": "system", "content": _system_prompt(channel, mode)}]
    for h in (history or []):
        role = h.get("role")
        content = (h.get("content") or "").strip()
        if role in ("user", "assistant") and content:
            messages.append({"role": role, "content": content})
    messages.append({"role": "user", "content": user_content})

    # Потоковый режим — данные идут непрерывно (важно при работе через прокси).
    parts: List[str] = []
    stream = client.chat.completions.create(
        model=model,
        messages=messages,
        stream=True,
        **_provider_kwargs(provider, model, max_tokens),
    )
    for chunk in stream:
        if chunk.choices and chunk.choices[0].delta and chunk.choices[0].delta.content:
            parts.append(chunk.choices[0].delta.content)
    return "".join(parts).strip() or "Не удалось сформировать ответ."


def complete(system: str, user: str, max_tokens: int = 400, provider: str = "openai") -> str:
    """Простой служебный вызов модели БЕЗ роли/правил агента (для summary, map-reduce и т.п.)."""
    if provider == "deepseek":
        api_key = (settings_store.get("deepseek_api_key") or "").strip()
        base_url = "https://api.deepseek.com"
        model = settings_store.get("deepseek_model")
        name = "DeepSeek"
    else:
        api_key = (settings_store.get("openai_api_key") or "").strip()
        base_url = None
        model = settings_store.get("openai_model")
        name = "OpenAI"
    if not api_key:
        raise ClaudeNotConfigured(
            f"Ключ {name} не задан. Откройте «Настройки» и введите API-ключ {name}."
        )
    client = _get_client(api_key, base_url)
    resp = client.chat.completions.create(
        model=model,
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        **_provider_kwargs(provider, model, max_tokens),
    )
    return (resp.choices[0].message.content or "").strip()
