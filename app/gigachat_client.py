"""Обращение к GigaChat API (Сбер, https://developers.sber.ru/portal/products/gigachat-api).

Особенности этого провайдера — не как у OpenAI-совместимых:
1. Токен получаем отдельным OAuth2-запросом (client_credentials), он живёт ~30 минут —
   кэшируем и обновляем сами, пользователь просто вводит "Authorization key" из личного
   кабинета Сбера (это уже готовый base64(client_id:client_secret), кодировать не нужно).
2. TLS-сертификат серверов Сбера подписан российским корневым CA (Минцифры), которого нет
   в обычных доверенных хранилищах — без него будет SSL-ошибка. Кладём сертификат в
   certs/russian_trusted_ca.pem и подключаем явно через verify=.
"""
from __future__ import annotations

import logging
import os
import time
import uuid
from datetime import datetime, timezone
from typing import List

import httpx
from cryptography import x509

import settings_store
from claude_client import ClaudeNotConfigured, _system_prompt, build_user_message

log = logging.getLogger("gigachat_client")

_OAUTH_URL = "https://ngw.devices.sberbank.ru:9443/api/v2/oauth"
_API_BASE = "https://api.giga.chat/v1"
_CERT_BUNDLE = os.path.join(os.path.dirname(__file__), "certs", "russian_trusted_ca.pem")
# Официальные файлы root+sub CA Минцифры — используются кнопкой «Обновить сертификат»
# в панели (сами по себе не требуют российского CA для скачивания, обычный сайт).
_CERT_SOURCE_URLS = (
    "https://gu-st.ru/content/Other/doc/russian_trusted_root_ca.cer",
    "https://gu-st.ru/content/Other/doc/russian_trusted_sub_ca.cer",
)

# Кэш токена в памяти процесса: (access_token, unix-время истечения, scope-с-которым-выдан).
# При смене scope в настройках старый токен не подходит — форсируем обновление.
_token: str | None = None
_token_expires_at: float = 0.0
_token_scope: str | None = None


def _normalize_expires_at(raw) -> float:
    """API отдаёт expires_at то в секундах, то в миллисекундах — приводим к секундам."""
    val = float(raw or 0)
    return val / 1000 if val > 10**12 else val


def _get_token(force: bool = False) -> str:
    global _token, _token_expires_at, _token_scope
    auth_key = (settings_store.get("gigachat_auth_key") or "").strip()
    if not auth_key:
        raise ClaudeNotConfigured(
            "Authorization key GigaChat не задан. Откройте «Настройки» и вставьте ключ "
            "из личного кабинета developers.sber.ru."
        )
    scope = (settings_store.get("gigachat_scope") or "GIGACHAT_API_PERS").strip()
    now = time.time()
    # 60 секунд запас, чтобы не словить протухший токен из-за задержки самого запроса.
    if not force and _token and scope == _token_scope and now < _token_expires_at - 60:
        return _token

    try:
        with httpx.Client(verify=_CERT_BUNDLE, timeout=httpx.Timeout(30.0)) as client:
            r = client.post(
                _OAUTH_URL,
                headers={
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Accept": "application/json",
                    "RqUID": str(uuid.uuid4()),
                    "Authorization": f"Basic {auth_key}",
                },
                data={"scope": scope},
            )
    except httpx.ConnectError as e:
        raise ClaudeNotConfigured(
            f"Не удалось подключиться к серверу авторизации GigaChat: {e}"
        ) from e
    if r.status_code >= 400:
        raise ClaudeNotConfigured(
            f"GigaChat отклонил авторизацию ({r.status_code}): {r.text[:300]}"
        )
    data = r.json()
    _token = data["access_token"]
    _token_expires_at = _normalize_expires_at(data.get("expires_at"))
    _token_scope = scope
    return _token


def _api_client() -> httpx.Client:
    return httpx.Client(verify=_CERT_BUNDLE,
                        timeout=httpx.Timeout(connect=30.0, read=120.0, write=30.0, pool=30.0))


def _chat(messages: list, max_tokens: int) -> str:
    """Один вызов chat/completions без стриминга (прокси тут не нужен — сервер в РФ,
    таймаутов из-за туннеля не бывает, поэтому проще и надёжнее без стриминга)."""
    model = settings_store.get("gigachat_model")
    body = {"model": model, "messages": messages, "max_tokens": max_tokens, "stream": False}
    token = _get_token()
    with _api_client() as client:
        r = client.post(
            f"{_API_BASE}/chat/completions",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json=body,
        )
        if r.status_code == 401:
            # Токен мог протухнуть между проверкой кэша и запросом — обновляем один раз.
            token = _get_token(force=True)
            r = client.post(
                f"{_API_BASE}/chat/completions",
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                json=body,
            )
        if r.status_code >= 400:
            log.error("GigaChat chat/completions %s: %s", r.status_code, r.text[:500])
            # Как и ошибки авторизации — превращаем в ClaudeNotConfigured, чтобы rag.py
            # показал понятное сообщение вместо сырого traceback (402 — нет активного
            # тарифа/баланса на этот scope, 403/429 — доступ/лимиты, и т.п.).
            raise ClaudeNotConfigured(
                f"GigaChat отклонил запрос ({r.status_code}): {r.text[:300]}"
            )
        data = r.json()
    choices = data.get("choices") or []
    if not choices:
        return "Не удалось сформировать ответ, попробуйте задать вопрос ещё раз."
    return (choices[0].get("message", {}).get("content") or "").strip()


def ask(question: str, hits: List[dict], history=None, channel: str = "internal",
        mode: str = "normal", max_tokens: int | None = None) -> str:
    max_tokens = max_tokens or int(settings_store.get("claude_max_tokens"))
    user_content = build_user_message(question, hits, channel=channel)
    messages = [{"role": "system", "content": _system_prompt(channel, mode)}]
    for h in (history or []):
        role = h.get("role")
        content = (h.get("content") or "").strip()
        if role in ("user", "assistant") and content:
            messages.append({"role": role, "content": content})
    messages.append({"role": "user", "content": user_content})
    return _chat(messages, max_tokens)


def complete(system: str, user: str, max_tokens: int = 400) -> str:
    """Служебный вызов модели без роли/правил агента (summary файлов, map-reduce)."""
    messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    return _chat(messages, max_tokens)


# ---------- сертификат российского CA: статус и обновление (для панели) ----------

def cert_status() -> dict:
    """Срок действия сертификатов в бандле — чтобы в панели было видно заранее, когда
    его пора обновить, а не когда TLS-соединения к GigaChat начнут молча падать."""
    try:
        with open(_CERT_BUNDLE, "rb") as f:
            certs = x509.load_pem_x509_certificates(f.read())
        if not certs:
            raise ValueError("файл сертификата пуст или повреждён")
        soonest = min(c.not_valid_after_utc for c in certs)
        days_left = (soonest - datetime.now(timezone.utc)).days
        return {"ok": True, "expires": soonest.strftime("%d.%m.%Y"), "days_left": days_left,
                "count": len(certs)}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)}


def update_cert_bundle() -> dict:
    """Перекачивает актуальные root+sub сертификаты Минцифры с gu-st.ru и пересобирает
    бандл. Сами файлы обычные (обычный доверенный CA), российский CA для их скачивания
    не нужен. Пишем во временный файл и переименовываем атомарно — чтобы при сбое
    посреди скачивания не остался битый файл вместо рабочего."""
    parts = []
    with httpx.Client(timeout=20.0, follow_redirects=True) as client:
        for url in _CERT_SOURCE_URLS:
            r = client.get(url)
            r.raise_for_status()
            body = r.content
            if b"BEGIN CERTIFICATE" not in body:
                raise ValueError(f"Ответ от {url} не похож на PEM-сертификат")
            parts.append(body.rstrip() + b"\n")
    new_bundle = b"\n".join(parts)
    certs = x509.load_pem_x509_certificates(new_bundle)  # падает, если бандл битый
    if not certs:
        raise ValueError("не удалось разобрать ни одного сертификата из скачанного")
    tmp_path = _CERT_BUNDLE + ".tmp"
    with open(tmp_path, "wb") as f:
        f.write(new_bundle)
    os.replace(tmp_path, _CERT_BUNDLE)
    log.info("Сертификат GigaChat обновлён: %s сертификатов", len(certs))
    return cert_status()
