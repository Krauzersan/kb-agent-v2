"""Точка входа FastAPI: сборка приложения, маршруты, запуск."""
from __future__ import annotations

import logging
import os

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

import admin
import auth
import convo
import db
import omnidesk_webhook
import settings_store
import webhook
from config import settings

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("app")

app = FastAPI(title="KB Agent", docs_url=None, redoc_url=None)
app.add_middleware(SessionMiddleware, secret_key=settings.SESSION_SECRET,
                   session_cookie=settings.SESSION_COOKIE_NAME,
                   max_age=60 * 60 * 12,  # сессия живёт 12 часов
                   path="/kb-agent", https_only=True)

app.include_router(admin.router)
app.include_router(webhook.router)
app.include_router(omnidesk_webhook.router)

# Картинки из ZIP-импорта — раздаём статикой по прямой ссылке (нужно мессенджерам,
# чтобы вставлять картинку в ответ). Только эта папка публична, не весь KB_DIR.
os.makedirs(settings.ASSETS_DIR, exist_ok=True)
app.mount("/assets", StaticFiles(directory=settings.ASSETS_DIR), name="assets")


@app.exception_handler(auth._RedirectToLogin)
async def _redirect_login(request: Request, exc: auth._RedirectToLogin):
    return auth.redirect_to_login()


@app.on_event("startup")
def _startup():
    os.makedirs(settings.DATA_DIR_ABS, exist_ok=True)
    db.init_db()
    settings_store.init()
    convo.init()
    stuck = db.reset_stuck()
    if stuck:
        log.info("Сброшено зависших файлов (прерванная индексация): %s", stuck)
    log.info("Запуск на порту %s. Данные: %s", settings.PORT, settings.DATA_DIR_ABS)
    log.info("Модель эмбеддингов и встроенный Qdrant загрузятся при первом запросе.")


@app.get("/health")
def health():
    return JSONResponse({"status": "ok"})


if __name__ == "__main__":
    # Запуск напрямую: python main.py  (для теста; в проде — systemd + uvicorn)
    import uvicorn

    uvicorn.run("main:app", host=settings.HOST, port=settings.PORT, workers=1)
