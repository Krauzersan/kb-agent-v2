"""Учёт токенов/стоимости LLM-вызовов в рамках ОДНОГО вопроса пользователя.

Один вопрос может потребовать НЕСКОЛЬКО обращений к модели — роутер (llm.complete,
router.py), декомпозиция и сборка в DEEP-режиме (deep.py), map/reduce в режиме
full_scan (mapreduce.py), сам ответ (llm.ask). Чтобы посчитать стоимость ОДНОГО
вопроса, а не последнего вызова, каждый вызов провайдера регистрирует свой usage
здесь через record(), а rag.answer_question() сбрасывает накопитель в начале и
читает сумму totals() в конце.

thread-local, а не глобальная переменная: FastAPI выполняет каждый top-level запрос
в отдельном потоке пула потоков (run_in_threadpool) — потоки переиспользуются между
запросами, поэтому reset() в начале answer_question() обязателен, иначе можно
унаследовать «хвост» от предыдущего запроса/фоновой задачи, обработанной тем же
потоком ранее.

Инструментированы только ask()/complete() провайдера Claude (см. claude_client.py) —
это единственный провайдер с точной ценой в этом модуле сейчас. Вызовы через
OpenAI/DeepSeek/GigaChat токены не считают (llm_provider переключается в настройках
редко, и точных актуальных цен на них здесь нет) — totals() в этом случае просто
вернёт нули, что честнее, чем гадать.
"""
from __future__ import annotations

import threading
from datetime import datetime, timezone

_local = threading.local()

# USD за 1M токенов (вход, выход). claude-sonnet-5 — отдельная функция ниже: до
# 2026-08-31 действует интро-цена ($2/$10), после — обычная ($3/$15). Остальные модели
# доступны в выпадающем списке настроек (см. settings.html) — цены проверены по
# прайсу Anthropic на 2026-06-24, могут разойтись при будущих изменениях прайса.
_CLAUDE_PRICES_PER_MTOK = {
    "claude-fable-5": (10.00, 50.00),
    "claude-mythos-5": (10.00, 50.00),
    "claude-opus-5": (5.00, 25.00),
    "claude-opus-4-8": (5.00, 25.00),
    "claude-opus-4-7": (5.00, 25.00),
    "claude-opus-4-6": (5.00, 25.00),
    "claude-sonnet-4-6": (3.00, 15.00),
    "claude-haiku-4-5": (1.00, 5.00),
}

_SONNET5_INTRO_UNTIL = datetime(2026, 8, 31, 23, 59, 59, tzinfo=timezone.utc)


def _sonnet5_prices() -> tuple[float, float]:
    if datetime.now(timezone.utc) <= _SONNET5_INTRO_UNTIL:
        return 2.00, 10.00
    return 3.00, 15.00


def _prices_per_mtok(provider: str, model: str) -> tuple[float, float] | None:
    if provider != "claude":
        return None
    if model == "claude-sonnet-5":
        return _sonnet5_prices()
    return _CLAUDE_PRICES_PER_MTOK.get(model)


def reset() -> None:
    _local.records = []


def record(provider: str, model: str, input_tokens: int = 0, output_tokens: int = 0,
           cache_creation_tokens: int = 0, cache_read_tokens: int = 0) -> None:
    if not hasattr(_local, "records"):
        _local.records = []
    _local.records.append({
        "provider": provider, "model": model,
        "input_tokens": int(input_tokens or 0), "output_tokens": int(output_tokens or 0),
        "cache_creation_tokens": int(cache_creation_tokens or 0),
        "cache_read_tokens": int(cache_read_tokens or 0),
    })


def totals() -> dict:
    """Сумма всех record() с последнего reset() в этом потоке. cost_usd — None, если
    хотя бы один вызов потратил токены у провайдера/модели без цены в таблице выше
    (честнее показать «неизвестно», чем занизить реальную стоимость)."""
    records = getattr(_local, "records", [])
    tokens_in = sum(r["input_tokens"] for r in records)
    tokens_out = sum(r["output_tokens"] for r in records)
    cache_write = sum(r["cache_creation_tokens"] for r in records)
    cache_read = sum(r["cache_read_tokens"] for r in records)
    cost = 0.0
    cost_unknown = False
    for r in records:
        prices = _prices_per_mtok(r["provider"], r["model"])
        if prices is None:
            if r["input_tokens"] or r["output_tokens"] or r["cache_creation_tokens"] or r["cache_read_tokens"]:
                cost_unknown = True
            continue
        in_rate, out_rate = prices
        cost += r["input_tokens"] / 1_000_000 * in_rate
        cost += r["output_tokens"] / 1_000_000 * out_rate
        # Кэш-чтение ~0.1x, кэш-запись (5-минутный TTL, который использует этот проект
        # без явного ttl в cache_control) ~1.25x цены входных токенов той же модели.
        cost += r["cache_read_tokens"] / 1_000_000 * in_rate * 0.1
        cost += r["cache_creation_tokens"] / 1_000_000 * in_rate * 1.25
    return {
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "cache_write_tokens": cache_write,
        "cache_read_tokens": cache_read,
        "cost_usd": None if cost_unknown else round(cost, 6),
        "calls": len(records),
    }
