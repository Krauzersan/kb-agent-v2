"""Авторизация админ-панели: именной аккаунт email+пароль, защита от подбора пароля
(rate limit по IP) и сессия в подписанной cookie."""
from __future__ import annotations

import hashlib
import hmac
import os
import time

from fastapi import Request
from fastapi.responses import RedirectResponse
from starlette.status import HTTP_303_SEE_OTHER

import db

_PBKDF2_ITERATIONS = 200_000

# ---------- rate limit на /login ----------

_FAILED_WINDOW = 15 * 60   # 15 минут
_FAILED_MAX = 5            # допустимых неудачных попыток за окно на один IP
_MAX_TRACKED_IPS = 5000    # защита от неограниченного роста словаря в памяти

_failed_attempts: dict[str, list[float]] = {}


def _prune(ip: str, now: float) -> list[float]:
    attempts = [t for t in _failed_attempts.get(ip, []) if now - t < _FAILED_WINDOW]
    if attempts:
        _failed_attempts[ip] = attempts
    else:
        _failed_attempts.pop(ip, None)
    return attempts


def is_rate_limited(ip: str) -> bool:
    return len(_prune(ip, time.time())) >= _FAILED_MAX


def seconds_until_retry(ip: str) -> int:
    attempts = _prune(ip, time.time())
    if len(attempts) < _FAILED_MAX:
        return 0
    return max(0, int(_FAILED_WINDOW - (time.time() - min(attempts))))


def record_failed_login(ip: str) -> None:
    if len(_failed_attempts) > _MAX_TRACKED_IPS:
        _failed_attempts.clear()
    _failed_attempts.setdefault(ip, []).append(time.time())


def record_successful_login(ip: str) -> None:
    _failed_attempts.pop(ip, None)


def client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


# ---------- пароли ----------

def hash_password(password: str, salt: bytes | None = None) -> tuple[str, str]:
    """Возвращает (hash_hex, salt_hex)."""
    salt = salt or os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _PBKDF2_ITERATIONS)
    return digest.hex(), salt.hex()


def _verify_password(password: str, password_hash: str, salt_hex: str) -> bool:
    digest, _ = hash_password(password, bytes.fromhex(salt_hex))
    return hmac.compare_digest(digest, password_hash)


# ---------- проверка учётных данных ----------

def is_authenticated(request: Request) -> bool:
    return bool(request.session.get("auth"))


def check_user_login(email: str, password: str) -> bool:
    """Именной аккаунт (email + свой пароль), хранится в admin_users."""
    email = (email or "").strip().lower()
    if not email or not password:
        return False
    user = db.get_admin_user(email)
    if not user:
        return False
    return _verify_password(password, user["password_hash"], user["salt"])


def login(request: Request, email: str = "") -> None:
    request.session["auth"] = True
    request.session["user"] = email or "admin"


def logout(request: Request) -> None:
    request.session.clear()


def require_login(request: Request):
    """Зависимость FastAPI: пускает дальше или редиректит на /login."""
    if not is_authenticated(request):
        raise _RedirectToLogin()


class _RedirectToLogin(Exception):
    pass


def redirect_to_login() -> RedirectResponse:
    return RedirectResponse(url="/login", status_code=HTTP_303_SEE_OTHER)
