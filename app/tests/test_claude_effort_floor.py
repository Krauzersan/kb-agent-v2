"""Регрессионный тест на баг: effort=max/xhigh + маленький claude_max_tokens => пустой ответ.

Воспроизведено вручную на проде (claude-sonnet-5, effort=max, max_tokens=3840): модель
тратит ВЕСЬ бюджет на внутреннее рассуждение, в потоке 0 видимых символов текста,
stop_reason=max_tokens. claude_client.ask() теперь поднимает эффективный max_tokens
до безопасного пола для тяжёлых effort-уровней (см. _MIN_TOKENS_FOR_EFFORT).

Без сети и без реального API-ключа: _get_client() и settings_store.get() подменены
моками, тест проверяет только то, какой max_tokens ask() РЕАЛЬНО передаёт в
client.messages.stream(...). Запуск: python3 tests/test_claude_effort_floor.py
"""
from __future__ import annotations

import os
import sys
import tempfile

os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="kb-agent-test-")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

assert "db" not in sys.modules and "config" not in sys.modules, (
    "db/config уже импортированы до переопределения DATA_DIR — тест рискует попасть "
    "в реальную базу вместо временной. Не импортируйте db/config на уровне модуля выше."
)

import claude_client  # noqa: E402

FAILURES: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    status = "OK  " if condition else "FAIL"
    print(f"[{status}] {name}" + (f" — {detail}" if detail and not condition else ""))
    if not condition:
        FAILURES.append(name)


class _FakeStream:
    def __init__(self):
        self.text_stream = iter(["Ответ."])

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get_final_message(self):
        class _Final:
            stop_reason = "end_turn"
            usage = None
        return _Final()


class _FakeMessages:
    def __init__(self, captured: dict):
        self._captured = captured

    def stream(self, **kwargs):
        self._captured.update(kwargs)
        return _FakeStream()


class _FakeClient:
    def __init__(self, captured: dict):
        self.messages = _FakeMessages(captured)


def _ask_and_capture(settings: dict) -> dict:
    captured: dict = {}
    orig_get_client = claude_client._get_client
    orig_settings_get = claude_client.settings_store.get
    claude_client._get_client = lambda: _FakeClient(captured)
    claude_client.settings_store.get = lambda key, *a, **kw: settings.get(key)
    try:
        claude_client.ask("Тестовый вопрос", hits=[])
    finally:
        claude_client._get_client = orig_get_client
        claude_client.settings_store.get = orig_settings_get
    return captured


def test_floor_applied_for_max_effort():
    captured = _ask_and_capture({
        "claude_model": "claude-sonnet-5",
        "claude_max_tokens": 1024,  # заведомо ниже пола, воспроизводит баг с пустым ответом
        "claude_effort": "max",
    })
    check("1a. max_tokens поднят до защитного пола для effort=max",
          captured.get("max_tokens", 0) >= claude_client._MIN_TOKENS_FOR_EFFORT["max"],
          f"max_tokens={captured.get('max_tokens')}")
    check("1b. effort передан без изменений",
          captured.get("output_config", {}).get("effort") == "max")


def test_floor_applied_for_xhigh_effort():
    captured = _ask_and_capture({
        "claude_model": "claude-sonnet-5",
        "claude_max_tokens": 500,
        "claude_effort": "xhigh",
    })
    check("2. max_tokens поднят до защитного пола для effort=xhigh",
          captured.get("max_tokens", 0) >= claude_client._MIN_TOKENS_FOR_EFFORT["xhigh"],
          f"max_tokens={captured.get('max_tokens')}")


def test_no_floor_for_high_effort():
    """Не должно ломать текущее поведение: high/medium/low не трогаем вообще."""
    captured = _ask_and_capture({
        "claude_model": "claude-sonnet-5",
        "claude_max_tokens": 900,
        "claude_effort": "high",
    })
    check("3. max_tokens НЕ меняется для effort=high (текущее поведение сохранено)",
          captured.get("max_tokens") == 900,
          f"max_tokens={captured.get('max_tokens')}")


def test_configured_value_kept_when_already_above_floor():
    captured = _ask_and_capture({
        "claude_model": "claude-sonnet-5",
        "claude_max_tokens": 8000,
        "claude_effort": "max",
    })
    check("4. заданный max_tokens не занижается, если уже выше пола",
          captured.get("max_tokens") == 8000,
          f"max_tokens={captured.get('max_tokens')}")


if __name__ == "__main__":
    test_floor_applied_for_max_effort()
    test_floor_applied_for_xhigh_effort()
    test_no_floor_for_high_effort()
    test_configured_value_kept_when_already_above_floor()
    print()
    if FAILURES:
        print(f"FAILED: {len(FAILURES)} — {FAILURES}")
        sys.exit(1)
    print("Все проверки прошли.")
