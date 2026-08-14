"""Веб-панель администратора: вход, список файлов, загрузка, удаление, тест вопроса."""
from __future__ import annotations

import hashlib
import logging
import os
import posixpath
import re
import shutil
import unicodedata
import urllib.parse
import uuid
import zipfile

from fastapi import APIRouter, BackgroundTasks, Form, Request, UploadFile, File
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.concurrency import run_in_threadpool
from starlette.status import HTTP_303_SEE_OTHER

import auth
import db
import gigachat_client
import ingest
import llm
import maintenance
import progress
import rag
import settings_store
import topics
import vectorstore
from config import settings

router = APIRouter()
templates = Jinja2Templates(directory="templates")
log = logging.getLogger("admin")

ALLOWED_EXT = {
    ".md", ".markdown", ".txt", ".yml", ".yaml", ".json", ".csv", ".tsv",
    ".docx", ".pdf", ".xlsx", ".xlsm",
    ".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tiff", ".gif",
}


def _safe_name(name: str) -> str:
    name = os.path.basename(name or "file")
    name = re.sub(r"[^\w.\-() ]+", "_", name, flags=re.UNICODE).strip()
    return name or "file"


def _new_stored_path(ext: str) -> str:
    """Путь физического файла на диске — намеренно короткий (uuid + расширение),
    НЕ зависит от исходного/отображаемого имени. Человекочитаемое имя живёт отдельно
    в БД (поле filename), у него лимита длины нет — в отличие от имени файла в
    файловой системе (обычно 255 байт). У ZIP-импорта из вложенных папок Slite
    отображаемое имя — это весь путь статьи («раздел - подраздел - ... - статья.md»)
    и легко перевалит за этот лимит, особенно на кириллице (2 байта/символ) —
    отсюда была ошибка «File name too long» при копировании на диск."""
    ext = ext if (not ext or ext.startswith(".")) else f".{ext}"
    return os.path.join(settings.KB_DIR, f"{uuid.uuid4().hex}{ext}")


# ---------- вход / выход ----------

@router.get("/login", response_class=HTMLResponse)
def login_form(request: Request):
    if auth.is_authenticated(request):
        return RedirectResponse("/admin", status_code=HTTP_303_SEE_OTHER)
    return templates.TemplateResponse("login.html", {"request": request, "error": None})


@router.post("/login", response_class=HTMLResponse)
def login_submit(request: Request, password: str = Form(...), email: str = Form(...)):
    ip = auth.client_ip(request)
    if auth.is_rate_limited(ip):
        wait_min = max(1, auth.seconds_until_retry(ip) // 60 + 1)
        return templates.TemplateResponse(
            "login.html",
            {"request": request, "error": f"Слишком много попыток. Повторите через {wait_min} мин."},
            status_code=429,
        )

    email = (email or "").strip()
    if auth.check_user_login(email, password):
        auth.record_successful_login(ip)
        auth.login(request, email)
        return RedirectResponse("/admin", status_code=HTTP_303_SEE_OTHER)

    auth.record_failed_login(ip)
    return templates.TemplateResponse(
        "login.html", {"request": request, "error": "Неверный логин или пароль"}, status_code=401
    )


@router.get("/logout")
def logout(request: Request):
    auth.logout(request)
    return RedirectResponse("/login", status_code=HTTP_303_SEE_OTHER)


@router.get("/admin/change_password", response_class=HTMLResponse)
def change_password_form(request: Request):
    auth.require_login(request)
    email = request.session.get("user") or ""
    return templates.TemplateResponse(
        "change_password.html", {"request": request, "email": email, "error": None, "success": False}
    )


@router.post("/admin/change_password", response_class=HTMLResponse)
def change_password_submit(request: Request, current_password: str = Form(...),
                            new_password: str = Form(...), new_password2: str = Form(...)):
    auth.require_login(request)
    email = request.session.get("user") or ""
    ctx = {"request": request, "email": email, "error": None, "success": False}

    if not auth.check_user_login(email, current_password):
        ctx["error"] = "Текущий пароль неверен"
        return templates.TemplateResponse("change_password.html", ctx, status_code=401)
    if new_password != new_password2:
        ctx["error"] = "Новые пароли не совпадают"
        return templates.TemplateResponse("change_password.html", ctx, status_code=400)
    if len(new_password) < 8:
        ctx["error"] = "Пароль должен быть не короче 8 символов"
        return templates.TemplateResponse("change_password.html", ctx, status_code=400)

    h, s = auth.hash_password(new_password)
    db.upsert_admin_user(email, h, s)
    ctx["success"] = True
    return templates.TemplateResponse("change_password.html", ctx)


# ---------- главная страница панели ----------

@router.get("/", response_class=HTMLResponse)
def root(request: Request):
    return RedirectResponse("/admin", status_code=HTTP_303_SEE_OTHER)


def _llm_ready() -> bool:
    """Задан ли ключ выбранного сейчас провайдера."""
    provider = settings_store.get("llm_provider")
    key = {"openai": "openai_api_key",
           "deepseek": "deepseek_api_key"}.get(provider, "anthropic_api_key")
    return settings_store.is_set(key)


PROVIDER_LABELS = {"claude": "Claude", "openai": "ChatGPT", "deepseek": "DeepSeek"}


def _base_ctx(request: Request) -> dict:
    """Общий контекст всех страниц панели (шапка, прогресс, статус ключа)."""
    provider = settings_store.get("llm_provider")
    return {
        "request": request,
        "prog": progress.get(),
        "claude_ready": _llm_ready(),
        "provider": provider,
        "provider_label": PROVIDER_LABELS.get(provider, provider),
    }


def _render_admin(request: Request, answer=None, question: str = "", sources=None, hits=None):
    files = db.list_files()
    ready = [f for f in files if f["status"] == "ready"]
    pending = [f for f in files if f["status"] != "ready"]
    total_chunks = sum(f["chunks"] for f in ready)
    # Компактные данные для таблицы: поиск/сортировка/фильтры работают на стороне браузера.
    files_json = [
        {
            "id": f["id"],
            "name": f["filename"],
            "status": f["status"],
            "chunks": f["chunks"] or 0,
            "size": f["size"] or 0,
            "created": (f["created_at"] or "")[:10],
            "priority": 1 if f.get("priority") else 0,
            "error": (f.get("error") or "")[:200],
        }
        for f in files
    ]
    ctx = _base_ctx(request)
    ctx.update({
        "files": files, "files_ready": ready, "files_pending": pending,
        "files_json": files_json, "total_chunks": total_chunks,
        "answer": answer, "question": question,
        "sources": sources or [], "hits": hits or [],
    })
    return templates.TemplateResponse("admin.html", ctx)


@router.get("/admin", response_class=HTMLResponse)
def admin_page(request: Request):
    auth.require_login(request)
    return _render_admin(request)


@router.get("/admin/test", response_class=HTMLResponse)
def test_page(request: Request):
    """Страница «Тест агента» — диалог с ботом прямо в панели."""
    auth.require_login(request)
    return templates.TemplateResponse("test.html", _base_ctx(request))


@router.post("/admin/api/ask")
async def api_ask(request: Request):
    """JSON-ответ для чата в панели (вопрос + история диалога)."""
    auth.require_login(request)
    try:
        data = await request.json()
    except Exception:
        data = {}
    question = (data.get("question") or "").strip()
    history = data.get("history") or None
    channel = data.get("channel") or "internal"
    if channel not in ("internal", "external"):
        channel = "internal"
    if not question:
        return JSONResponse({"answer": "Пустой вопрос.", "sources": [], "hits": []})
    try:
        result = await run_in_threadpool(rag.answer_question, question, history=history, channel=channel)
    except Exception as e:  # noqa: BLE001
        log.exception("Тест агента: ошибка ответа")
        return JSONResponse({"answer": f"Ошибка: {e}", "sources": [], "hits": []}, status_code=200)
    return JSONResponse({
        "answer": result.get("answer", ""),
        "sources": result.get("sources", []),
        "hits": [
            {"score": float(h.get("score") or 0), "filename": h.get("filename", ""),
             "text": (h.get("text") or "")[:220], "priority": int(h.get("priority") or 0)}
            for h in (result.get("hits") or [])
        ],
    })


@router.get("/admin/maintenance", response_class=HTMLResponse)
def maintenance_page(request: Request):
    auth.require_login(request)
    ctx = _base_ctx(request)
    ready_ids = [r["id"] for r in db.list_files() if r["status"] == "ready"]
    fts_indexed = len(db.fts_indexed_file_ids() & set(ready_ids))
    ctx.update({"catalog": db.catalog_stats(),
                "fts": {"total": len(ready_ids), "indexed": fts_indexed}})
    return templates.TemplateResponse("maintenance.html", ctx)


# ---------- лог вопросов (отладочная панель) ----------

_LOG_PAGE_SIZE = 30


@router.get("/admin/logs", response_class=HTMLResponse)
def logs_page(request: Request, page: int = 1, q: str = ""):
    """Что реально спрашивают у агента и на каких кусках базы построен ответ —
    чтобы можно было перейти к источнику и поправить его, если ответ неверный."""
    auth.require_login(request)
    q = (q or "").strip()
    page = max(1, page)
    total = db.count_query_log(q)
    pages = max(1, -(-total // _LOG_PAGE_SIZE))
    page = min(page, pages)
    entries = db.list_query_log(limit=_LOG_PAGE_SIZE, offset=(page - 1) * _LOG_PAGE_SIZE, q=q)
    ctx = _base_ctx(request)
    ctx.update({"entries": entries, "total": total, "page": page, "pages": pages, "q": q})
    return templates.TemplateResponse("logs.html", ctx)


@router.get("/admin/metrics", response_class=HTMLResponse)
def metrics_page(request: Request):
    """Кто чаще всего спрашивает агента (Пачка) и как оценивает ответы (1-10)."""
    auth.require_login(request)
    ctx = _base_ctx(request)
    ctx.update({"overview": db.rating_overview(), "users": db.rating_stats_by_user()})
    return templates.TemplateResponse("metrics.html", ctx)


@router.get("/admin/metrics/user/{asker_user_id}", response_class=HTMLResponse)
def metrics_user_page(request: Request, asker_user_id: int):
    """Разворот одного сотрудника: все его вопросы и какие оценки он на них ставил."""
    auth.require_login(request)
    entries = db.list_by_asker(asker_user_id)
    asker_name = entries[0]["asker_name"] if entries else str(asker_user_id)
    rated = [e for e in entries if e.get("rating") is not None]
    avg_rating = round(sum(e["rating"] for e in rated) / len(rated), 1) if rated else None
    ctx = _base_ctx(request)
    ctx.update({
        "entries": entries, "asker_name": asker_name, "asker_user_id": asker_user_id,
        "questions": len(entries), "rated": len(rated), "avg_rating": avg_rating,
    })
    return templates.TemplateResponse("metrics_user.html", ctx)


# ---------- аналитика: о чём чаще всего спрашивают, чего не хватает в базе ----------

@router.get("/admin/analytics", response_class=HTMLResponse)
def analytics_page(request: Request):
    auth.require_login(request)
    ctx = _base_ctx(request)
    stats = db.topic_stats()
    gaps = sorted(
        [t for t in stats if t["no_sources"] > 0],
        key=lambda t: t["no_sources"], reverse=True,
    )
    ctx.update({
        "topics": stats, "gaps": gaps,
        "untagged": db.count_untagged(),
    })
    return templates.TemplateResponse("analytics.html", ctx)


@router.get("/admin/analytics/topic/{topic_name}", response_class=HTMLResponse)
def analytics_topic_page(request: Request, topic_name: str):
    auth.require_login(request)
    ctx = _base_ctx(request)
    ctx.update({"topic_name": topic_name, "entries": db.topic_examples(topic_name)})
    return templates.TemplateResponse("analytics_topic.html", ctx)


def _backfill_topics() -> None:
    try:
        topics.backfill()
    except llm.LLMNotConfigured:
        pass  # уже залогировано в topics.backfill — прогресс уже остановлен там же


@router.post("/admin/analytics/backfill")
def analytics_backfill(request: Request, background: BackgroundTasks):
    auth.require_login(request)
    background.add_task(_backfill_topics)
    return RedirectResponse("/admin/analytics?started=1", status_code=HTTP_303_SEE_OTHER)


@router.post("/admin/logs/clear")
def logs_clear(request: Request):
    auth.require_login(request)
    n = db.clear_query_log()
    return RedirectResponse(f"/admin/logs?cleared={n}", status_code=HTTP_303_SEE_OTHER)


# ---------- загрузка файла ----------

@router.post("/admin/upload")
async def upload(request: Request, background: BackgroundTasks,
                 files: list[UploadFile] = File(...)):
    """Общая точка загрузки: обычные документы идут в обычную индексацию, а .zip
    среди них — по тому же пайплайну распаковки, что раньше был отдельной формой
    (см. _process_zip_import). Один drop может содержать и то, и другое сразу."""
    auth.require_login(request)
    os.makedirs(settings.KB_DIR, exist_ok=True)
    file_ids = []
    zip_count = 0
    for file in files:
        if not file or not file.filename:
            continue
        ext = os.path.splitext(file.filename)[1].lower()
        if ext == ".zip":
            os.makedirs(settings.ASSETS_DIR, exist_ok=True)
            tmp_zip = os.path.join(settings.DATA_DIR_ABS, f"_upload_{uuid.uuid4().hex}.zip")
            data = await file.read()
            with open(tmp_zip, "wb") as f:
                f.write(data)
            background.add_task(_process_zip_import, tmp_zip)
            zip_count += 1
            continue
        if ext not in ALLOWED_EXT:
            continue  # неподдерживаемый тип — пропускаем
        safe = _safe_name(file.filename)
        stored = _new_stored_path(ext)
        data = await file.read()
        with open(stored, "wb") as f:
            f.write(data)
        file_ids.append(db.add_file(filename=safe, stored_path=stored, size=len(data)))

    if file_ids:
        background.add_task(_ingest_many, file_ids)
    if zip_count:
        return RedirectResponse("/admin/maintenance?zip=started", status_code=HTTP_303_SEE_OTHER)
    return RedirectResponse("/admin", status_code=HTTP_303_SEE_OTHER)


# ---------- загрузка ZIP-архива (например, экспорт из Slite) ----------
# Сохраняет структуру папок ровно настолько, насколько нужно, чтобы найти картинку,
# на которую markdown-файл ссылается относительным путём (![alt](images/pic.png)),
# и превратить эту ссылку в настоящий публичный URL (см. _rewrite_markdown_images).

# Путь в скобках может содержать экранированные символы — markdown-экспортёры (в т.ч.
# Slite) экранируют круглые скобки внутри пути обратным слэшем (например, файл
# «Untitled Project (10).jpg» превращается в «...project%20\(10\).jpg»), иначе они бы
# закрыли ссылку раньше времени. Простое «до первой )» это не учитывает и обрезает
# путь ровно на такой экранированной скобке — ссылка не резолвится и остаётся сырым
# markdown в тексте. Поэтому здесь путь — это чередование «экранированный любой символ»
# и «любой символ, кроме ) и \» — экранированные скобки внутри так не закрывают группу.
_IMG_MD_RE = re.compile(r'!\[([^\]]*)\]\(((?:\\.|[^()\\])*)\)')

# Магические байты для распознавания картинок без расширения — экспорт Slite иногда
# кладёт файлы вида «<id>-imported-image» вообще без .png/.jpg.
_IMG_MAGIC = (
    (b"\x89PNG\r\n\x1a\n", ".png"),
    (b"\xff\xd8\xff", ".jpg"),
    (b"GIF87a", ".gif"),
    (b"GIF89a", ".gif"),
    (b"BM", ".bmp"),
)


def _sniff_image_ext(path: str) -> str | None:
    try:
        with open(path, "rb") as f:
            head = f.read(16)
    except OSError:
        return None
    for magic, ext in _IMG_MAGIC:
        if head.startswith(magic):
            return ext
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return ".webp"
    return None


def _rewrite_markdown_images(md_rel_path: str, text: str, file_index: dict,
                             referenced_images: set) -> str:
    """Заменяет ![alt](относительный/путь.png) на [изображение: alt] <публичный URL>,
    копируя найденную картинку в settings.ASSETS_DIR. Ссылки, которые не удалось
    разрешить (внешний URL, битый путь, не картинка), оставляет как есть.

    URL строится по короткому хешу ПУТИ внутри архива (не абсолютного, не по
    случайному id импорта) — у Slite путь бывает двойного URL-кодирования и по
    200+ символов, что раздувает текстовый чанк при индексации. Хеш от пути внутри
    архива (а не от id конкретной загрузки) даёт стабильный URL: при повторной
    загрузке того же архива одна и та же картинка ложится в тот же файл на диске,
    а не плодит копии."""
    base_url = (settings_store.get("public_base_url") or "").rstrip("/")
    md_dir = posixpath.dirname(md_rel_path)

    def _repl(m):
        alt, link = m.group(1), m.group(2).strip().strip("<>")
        link = re.sub(r'\\(.)', r'\1', link)  # снимаем markdown-экранирование (\(, \), \_ и т.п.)
        link = link.split(" ", 1)[0]  # отбрасываем markdown-title после пробела, если есть
        if link.startswith(("http://", "https://", "data:", "mailto:")):
            return m.group(0)
        decoded = urllib.parse.unquote(link.split("#")[0].split("?")[0])
        # NFC — архивы, запакованные на macOS, хранят кириллические имена файлов в
        # разложенной форме (NFD: буква + отдельный диакритический знак), а ссылка
        # внутри самого markdown-текста обычно уже в обычной составной форме (NFC).
        # Внешне строки выглядят одинаково, но побайтово различаются — точное
        # совпадение ключей словаря без нормализации не сработает.
        resolved = unicodedata.normalize(
            "NFC", posixpath.normpath(posixpath.join(md_dir, decoded)))
        src_abs = file_index.get(resolved)
        if not src_abs:
            return m.group(0)
        ext = os.path.splitext(resolved)[1].lower()
        if ext not in ingest.IMAGE_EXT:
            ext = _sniff_image_ext(src_abs)
            if not ext:
                return m.group(0)  # не похоже на картинку — оставляем ссылку как есть
        short_id = hashlib.sha1(resolved.encode("utf-8")).hexdigest()[:16]
        dest_rel = f"{short_id}{ext}"
        dest_abs = os.path.join(settings.ASSETS_DIR, dest_rel)
        # Всегда перезаписываем: путь стабилен между загрузками, но если в источнике
        # по тому же пути оказалась ДРУГАЯ картинка (перезалита в Slite) — подхватываем
        # свежее содержимое, а не оставляем протухшую копию с прошлого раза.
        shutil.copy2(src_abs, dest_abs)
        referenced_images.add(src_abs)
        url = f"{base_url}/assets/{dest_rel}"
        label = f": {alt}" if alt.strip() else ""
        return f"\n[изображение{label}] {url}\n"

    return _IMG_MD_RE.sub(_repl, text)


def _process_zip_import(zip_path: str) -> None:
    """Фоновая обработка загруженного архива: безопасная распаковка, переписывание
    ссылок на картинки в markdown, индексация всех поддерживаемых файлов."""
    import_id = uuid.uuid4().hex[:12]
    extract_dir = os.path.join(settings.DATA_DIR_ABS, "_import_tmp", import_id)
    os.makedirs(extract_dir, exist_ok=True)
    try:
        vectorstore.ensure_collection()
    except Exception:
        log.exception("ZIP-импорт: не удалось подготовить индекс")
        shutil.rmtree(extract_dir, ignore_errors=True)
        return

    def _fix_name(raw_name: str, flag_bits: int) -> str:
        """zipfile декодирует имена как cp437, если в архиве не выставлен UTF-8-флаг —
        для кириллицы это превращает имя в мусор (и заодно раздувает его в байтах,
        отчего потом падает «File name too long»). У большинства таких архивов имена
        на самом деле в UTF-8, просто без флага — восстанавливаем исходные байты и
        перекодируем как надо."""
        if flag_bits & 0x800:
            return raw_name  # флаг стоит — zipfile уже декодировал верно
        try:
            return raw_name.encode("cp437").decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            return raw_name

    # 1. Безопасно распаковываем архив (защита от zip-slip: путь должен остаться
    #    внутри extract_dir; иначе запись из архива пропускаем). Один проблемный файл
    #    (например слишком длинное имя) пропускаем, а не роняем весь импорт из-за него.
    extract_root = os.path.normpath(extract_dir)
    skipped_members = 0
    try:
        with zipfile.ZipFile(zip_path) as zf:
            for member in zf.infolist():
                name = _fix_name(member.filename, member.flag_bits)
                if name.startswith("__MACOSX") or os.path.basename(name) == ".DS_Store":
                    continue
                target = os.path.normpath(os.path.join(extract_dir, name))
                if target != extract_root and not target.startswith(extract_root + os.sep):
                    log.warning("ZIP-импорт: подозрительный путь в архиве пропущен: %s", name)
                    skipped_members += 1
                    continue
                try:
                    if member.is_dir():
                        os.makedirs(target, exist_ok=True)
                        continue
                    os.makedirs(os.path.dirname(target), exist_ok=True)
                    with zf.open(member) as src, open(target, "wb") as dst:
                        shutil.copyfileobj(src, dst)
                except OSError:
                    log.warning("ZIP-импорт: не удалось сохранить файл из архива (пропущен): %s", name)
                    skipped_members += 1
    except Exception:
        log.exception("ZIP-импорт: не удалось распаковать архив")
        shutil.rmtree(extract_dir, ignore_errors=True)
        return
    finally:
        try:
            os.remove(zip_path)
        except OSError:
            pass
    if skipped_members:
        log.warning("ZIP-импорт: пропущено файлов при распаковке (слишком длинный путь "
                   "или подозрительное имя): %s", skipped_members)

    # 2. Индекс всех извлечённых файлов: относительный путь (posix) -> абсолютный путь.
    # NFC — см. комментарий в _rewrite_markdown_images: имена из архива (особенно
    # запакованного на macOS) бывают в разложенной Unicode-форме (NFD), приводим сразу
    # здесь, чтобы все дальнейшие source_path/сравнения были в одной форме.
    file_index = {}
    for root, _dirs, names in os.walk(extract_dir):
        for name in names:
            abs_p = os.path.join(root, name)
            rel_p = os.path.relpath(abs_p, extract_dir).replace(os.sep, "/")
            rel_p = unicodedata.normalize("NFC", rel_p)
            file_index[rel_p] = abs_p

    referenced_images: set = set()

    # 3. Markdown-файлы — сначала переписываем в них ссылки на картинки (пока индекс
    #    полный), потом ставим в очередь на индексацию как обычно.
    targets = []  # (абсолютный путь, отображаемое имя с путём)
    for rel_p, abs_p in sorted(file_index.items()):
        if os.path.splitext(rel_p)[1].lower() not in {".md", ".markdown"}:
            continue
        try:
            with open(abs_p, "r", encoding="utf-8", errors="ignore") as f:
                text = f.read()
        except Exception:
            log.exception("ZIP-импорт: не удалось прочитать %s", rel_p)
            continue
        new_text = _rewrite_markdown_images(rel_p, text, file_index, referenced_images)
        if new_text != text:
            with open(abs_p, "w", encoding="utf-8") as f:
                f.write(new_text)
        targets.append((abs_p, rel_p))

    # 4. Остальные поддерживаемые файлы: документы + картинки, на которые НЕ нашлось
    #    ссылки ни из одного markdown-файла (те, что нашлись — уже вставлены ссылкой
    #    в текст статьи, отдельной записью в базе их не дублируем).
    for rel_p, abs_p in sorted(file_index.items()):
        ext = os.path.splitext(rel_p)[1].lower()
        if ext in {".md", ".markdown"} or ext not in ALLOWED_EXT:
            continue
        if abs_p in referenced_images:
            continue
        targets.append((abs_p, rel_p))

    # 5. Индексация: новые статьи — добавляем, изменившиеся с прошлой загрузки —
    #    переиндексируем на том же id, неизменившиеся — пропускаем совсем (не читаем,
    #    не копируем, не гоняем через эмбеддинги заново). Идентичность файла между
    #    загрузками — путь ВНУТРИ архива (rel_p), а НЕ случайный id этой загрузки:
    #    иначе повторная заливка того же архива каждый раз дублировала бы всё целиком.
    #    Отображаемое имя (с полным путём статьи) хранится только в БД — на диске файл
    #    лежит под коротким opaque-именем (см. _new_stored_path), чтобы длинный
    #    вложенный путь Slite не упирался в лимит длины имени файла (255 байт).
    progress.start("Импорт ZIP", len(targets))
    added = updated = unchanged = errors = 0
    try:
        for abs_p, rel_p in targets:
            source_path = f"zip:{rel_p}"
            try:
                with open(abs_p, "rb") as f:
                    content_hash = hashlib.sha1(f.read()).hexdigest()
                existing = db.file_by_source(source_path)
                if existing and existing.get("content_hash") == content_hash:
                    unchanged += 1
                    progress.step()
                    continue

                display = _safe_name(rel_p.replace("/", " - "))
                stored = _new_stored_path(os.path.splitext(rel_p)[1].lower())
                shutil.copy2(abs_p, stored)

                if existing:
                    fid = existing["id"]
                    old_stored = existing.get("stored_path")
                    db.update_stored(fid, stored, os.path.getsize(stored))
                    if old_stored and old_stored != stored and os.path.exists(old_stored):
                        try:
                            os.remove(old_stored)
                        except OSError:
                            pass
                else:
                    fid = db.add_file(filename=display, stored_path=stored,
                                      size=os.path.getsize(stored), source_path=source_path)

                ingest.ingest_file(fid)
                db.set_content_hash(fid, content_hash)
                rec = db.get_file(fid)
                if rec and rec["status"] == "ready":
                    if existing:
                        updated += 1
                    else:
                        added += 1
                else:
                    errors += 1
            except Exception:
                errors += 1
                log.exception("ZIP-импорт: ошибка на файле %s", rel_p)
            progress.step()
        log.info("ZIP-импорт ЗАВЕРШЁН: новых %s, обновлено %s, без изменений %s, "
                 "картинок вставлено ссылками %s, ошибок %s",
                 added, updated, unchanged, len(referenced_images), errors)
    finally:
        progress.finish()
        shutil.rmtree(extract_dir, ignore_errors=True)


def _repair_image_links() -> dict:
    """Догоняет уже проиндексированные ZIP-статьи, у которых ссылка на картинку не
    резолвилась при первой загрузке (например, из-за бага с экранированными скобками
    в имени файла — путь резался посередине). Работает целиком по данным, уже
    сохранённым в БД (source_path каждого файла хранит его путь внутри архива) —
    сам архив заново распаковывать не нужно, а значит и заново заливать не нужно."""
    all_files = db.list_files()
    file_index = {}
    for f in all_files:
        sp = f.get("source_path") or ""
        if sp.startswith("zip:") and f.get("stored_path") and os.path.exists(f["stored_path"]):
            rel_p = unicodedata.normalize("NFC", sp[len("zip:"):])
            file_index[rel_p] = f["stored_path"]

    md_files = [f for f in all_files
                if (f.get("source_path") or "").startswith("zip:")
                and os.path.splitext(f["source_path"])[1].lower() in {".md", ".markdown"}]

    progress.start("Починка ссылок на картинки", len(md_files))
    repaired = unchanged = errors = 0
    try:
        for f in md_files:
            fid = f["id"]
            rel_p = f["source_path"][len("zip:"):]
            stored = f.get("stored_path")
            try:
                if not stored or not os.path.exists(stored):
                    errors += 1
                    progress.step()
                    continue
                with open(stored, "r", encoding="utf-8", errors="ignore") as fh:
                    text = fh.read()
                new_text = _rewrite_markdown_images(rel_p, text, file_index, set())
                if new_text == text:
                    unchanged += 1
                    progress.step()
                    continue
                with open(stored, "w", encoding="utf-8") as fh:
                    fh.write(new_text)
                ingest.ingest_file(fid)
                db.set_content_hash(fid, hashlib.sha1(new_text.encode("utf-8")).hexdigest())
                rec = db.get_file(fid)
                if rec and rec["status"] == "ready":
                    repaired += 1
                else:
                    errors += 1
            except Exception:
                errors += 1
                log.exception("Починка картинок: ошибка на файле %s", rel_p)
            progress.step()
        log.info("Починка ссылок на картинки ЗАВЕРШЕНА: исправлено %s, без изменений %s, ошибок %s",
                 repaired, unchanged, errors)
    finally:
        progress.finish()
    return {"repaired": repaired, "unchanged": unchanged, "errors": errors}


@router.post("/admin/repair_image_links")
def repair_image_links(request: Request, background: BackgroundTasks):
    auth.require_login(request)
    background.add_task(_repair_image_links)
    return RedirectResponse("/admin/maintenance?repair=started", status_code=HTTP_303_SEE_OTHER)


def _ingest_url(url: str) -> None:
    """Скачивает страницу по ссылке, сохраняет как файл и индексирует (с картинками)."""
    progress.start("Загрузка страницы", 1)
    try:
        title, text = ingest.fetch_url_text(url)
        if not text:
            log.warning("По ссылке не удалось извлечь текст: %s", url)
            return
        os.makedirs(settings.KB_DIR, exist_ok=True)
        safe = _safe_name(title) + ".txt"
        stored = _new_stored_path(".txt")
        with open(stored, "w", encoding="utf-8") as f:
            f.write(f"Источник: {url}\n\n{text}")
        fid = db.add_file(filename=safe, stored_path=stored,
                          size=len(text.encode("utf-8")), source_path=url)
        ingest.ingest_file(fid)
    except Exception:
        log.exception("Ошибка загрузки по ссылке: %s", url)
    finally:
        progress.step()
        progress.finish()


@router.post("/admin/add_url")
def add_url(request: Request, background: BackgroundTasks, url: str = Form(...)):
    auth.require_login(request)
    url = (url or "").strip()
    if not url.startswith(("http://", "https://")):
        return RedirectResponse("/admin?err=url", status_code=HTTP_303_SEE_OTHER)
    if db.file_by_source(url):        # уже добавляли эту ссылку
        return RedirectResponse("/admin?err=dup", status_code=HTTP_303_SEE_OTHER)
    background.add_task(_ingest_url, url)
    return RedirectResponse("/admin", status_code=HTTP_303_SEE_OTHER)


@router.post("/admin/add_text")
async def add_text(request: Request, background: BackgroundTasks,
                    title: str = Form(...), text: str = Form(...),
                    images: list[UploadFile] = File(default=[])):
    """Ручной ввод текста без файла — сохраняем как обычный .md и заводим в базу тем же
    путём, что и загруженные файлы (та же индексация, тот же список, то же удаление).

    Приложенные скриншоты кладём в settings.ASSETS_DIR (тем же способом, что и картинки
    из docx/ZIP-импорта) и дописываем в конец текста маркерами [изображение] URL — их
    подхватывает тот же конвейер, что рендерит картинки в ответах бота (см. webhook.py)."""
    auth.require_login(request)
    title = (title or "").strip()
    text = (text or "").strip()
    if not title or not text:
        return RedirectResponse("/admin?err=text", status_code=HTTP_303_SEE_OTHER)

    image_markers = []
    for img in images or []:
        if not img or not img.filename:
            continue
        ext = os.path.splitext(img.filename)[1].lower()
        if ext not in ingest.IMAGE_EXT:
            continue
        blob = await img.read()
        if not blob:
            continue
        url = ingest.save_kb_image(blob, ext)
        image_markers.append(f"[изображение] {url}")

    if image_markers:
        text = text + "\n\n" + "\n".join(image_markers)

    os.makedirs(settings.KB_DIR, exist_ok=True)
    safe = _safe_name(title if title.lower().endswith((".md", ".markdown", ".txt")) else f"{title}.md")
    stored = _new_stored_path(".md")
    data = text.encode("utf-8")
    with open(stored, "wb") as f:
        f.write(data)
    file_id = db.add_file(filename=safe, stored_path=stored, size=len(data))
    background.add_task(_ingest_many, [file_id])
    return RedirectResponse("/admin", status_code=HTTP_303_SEE_OTHER)


def _ingest_many(file_ids: list) -> None:
    """Индексация нескольких файлов с прогресс-баром (если нет другой операции)."""
    busy = progress.get().get("active")
    if not busy:
        progress.start("Индексация", len(file_ids))
    try:
        for fid in file_ids:
            try:
                ingest.ingest_file(fid)
            except Exception:
                log.exception("Индексация: ошибка на файле id=%s", fid)
            if not busy:
                progress.step()
    finally:
        if not busy:
            progress.finish()


# ---------- просмотр текста файла ----------

@router.get("/admin/file/{file_id}", response_class=HTMLResponse)
def view_file(request: Request, file_id: int):
    """«Провалиться» в файл: показать текст, который реально лежит в индексе."""
    auth.require_login(request)
    rec = db.get_file(file_id)
    if not rec:
        return RedirectResponse("/admin", status_code=HTTP_303_SEE_OTHER)

    chunks, note = [], None
    try:
        chunks = vectorstore.get_file_chunks(file_id, limit=1500)
    except Exception:
        log.exception("Просмотр файла: не удалось получить куски id=%s", file_id)

    # Файл не проиндексирован (или индекс пуст) — покажем текст, извлечённый с диска.
    if not chunks:
        try:
            if rec.get("stored_path") and os.path.exists(rec["stored_path"]):
                text = ingest.extract_text(rec["stored_path"])
                if text and text.strip():
                    chunks = [{"chunk_index": 0, "text": text.strip()}]
                    note = ("Файл не проиндексирован — показан текст, извлечённый напрямую из "
                            "файла (не из поискового индекса).")
        except Exception:
            log.exception("Просмотр файла: не удалось извлечь текст id=%s", file_id)

    total = rec.get("chunks") or len(chunks)
    chars = sum(len(c["text"]) for c in chunks)
    ctx = _base_ctx(request)
    ctx.update({"f": rec, "chunks": chunks, "shown": len(chunks),
                "total": total, "chars": chars, "note": note,
                "truncated": bool(total) and len(chunks) < total})
    return templates.TemplateResponse("file_view.html", ctx)


# ---------- редактирование файла ----------

@router.get("/admin/file/{file_id}/edit", response_class=HTMLResponse)
def edit_file_form(request: Request, file_id: int):
    auth.require_login(request)
    rec = db.get_file(file_id)
    if not rec:
        return RedirectResponse("/admin", status_code=HTTP_303_SEE_OTHER)
    ext = os.path.splitext(rec["filename"])[1].lower()
    editable = ext in ingest.TEXT_EXT
    content = ""
    if editable and rec.get("stored_path") and os.path.exists(rec["stored_path"]):
        try:
            with open(rec["stored_path"], "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()
        except Exception:
            log.exception("Редактирование: не удалось прочитать файл id=%s", file_id)
    ctx = _base_ctx(request)
    ctx.update({"f": rec, "editable": editable, "content": content})
    return templates.TemplateResponse("file_edit.html", ctx)


@router.post("/admin/file/{file_id}/edit")
async def edit_file_save(request: Request, background: BackgroundTasks, file_id: int,
                         text: str = Form(...)):
    """Сохраняет отредактированный текст поверх исходного файла (только для текстовых
    форматов — .md/.txt/.yml/.json и т.п., см. ingest.TEXT_EXT) и переиндексирует."""
    auth.require_login(request)
    rec = db.get_file(file_id)
    if not rec:
        return RedirectResponse("/admin", status_code=HTTP_303_SEE_OTHER)
    ext = os.path.splitext(rec["filename"])[1].lower()
    if ext not in ingest.TEXT_EXT:
        return RedirectResponse(f"/admin/file/{file_id}", status_code=HTTP_303_SEE_OTHER)
    data = text.encode("utf-8")
    with open(rec["stored_path"], "wb") as f:
        f.write(data)
    db.update_stored(file_id, rec["stored_path"], len(data))
    background.add_task(_ingest_many, [file_id])
    return RedirectResponse(f"/admin/file/{file_id}?saved=1", status_code=HTTP_303_SEE_OTHER)


@router.post("/admin/file/{file_id}/replace")
async def replace_file(request: Request, background: BackgroundTasks, file_id: int,
                       file: UploadFile = File(...)):
    """Заменяет содержимое файла целиком новой загрузкой (для форматов, которые нельзя
    редактировать как текст — docx/pdf/xlsx/картинки, — а также как альтернатива для
    текстовых, если проще залить новую версию файлом, чем править в текстовом поле)."""
    auth.require_login(request)
    rec = db.get_file(file_id)
    if not rec:
        return RedirectResponse("/admin", status_code=HTTP_303_SEE_OTHER)
    if not file or not file.filename:
        return RedirectResponse(f"/admin/file/{file_id}/edit", status_code=HTTP_303_SEE_OTHER)
    ext = os.path.splitext(file.filename)[1].lower()
    if ext not in ALLOWED_EXT:
        return RedirectResponse(f"/admin/file/{file_id}/edit?err=type", status_code=HTTP_303_SEE_OTHER)
    old_path = rec.get("stored_path")
    stored = _new_stored_path(ext)
    data = await file.read()
    with open(stored, "wb") as f:
        f.write(data)
    db.update_stored(file_id, stored, len(data))
    db.rename_file(file_id, _safe_name(file.filename))
    if old_path and old_path != stored and os.path.exists(old_path):
        try:
            os.remove(old_path)
        except Exception:
            pass
    background.add_task(_ingest_many, [file_id])
    return RedirectResponse(f"/admin/file/{file_id}?saved=1", status_code=HTTP_303_SEE_OTHER)


# ---------- удаление файла ----------

def _delete_one(file_id: int) -> bool:
    """Удаляет файл из индекса, с диска и из БД. True — если что-то удалили."""
    rec = db.get_file(file_id)
    if not rec:
        return False
    try:
        vectorstore.delete_file(file_id)
    except Exception:
        pass
    db.delete_chunks_fts(file_id)
    try:
        if rec["stored_path"] and os.path.exists(rec["stored_path"]):
            os.remove(rec["stored_path"])
    except Exception:
        pass
    db.delete_file_row(file_id)
    return True


@router.post("/admin/delete/{file_id}")
def delete(request: Request, file_id: int):
    auth.require_login(request)
    _delete_one(file_id)
    return RedirectResponse("/admin", status_code=HTTP_303_SEE_OTHER)


def _bulk_delete(file_ids: list) -> None:
    """Быстрое массовое удаление: векторы — одним запросом, затем файлы и записи БД."""
    progress.start("Удаление", len(file_ids))
    try:
        try:
            vectorstore.delete_files(file_ids)   # один запрос к индексу вместо сотен
        except Exception:
            log.exception("Массовое удаление: ошибка удаления векторов")
        try:
            db.delete_chunks_fts_many(file_ids)
        except Exception:
            log.exception("Массовое удаление: ошибка очистки лексического индекса")
        n = 0
        for fid in file_ids:
            rec = db.get_file(fid)
            if rec:
                try:
                    if rec["stored_path"] and os.path.exists(rec["stored_path"]):
                        os.remove(rec["stored_path"])
                except Exception:
                    pass
                db.delete_file_row(fid)
                n += 1
            progress.step()
        log.info("Массовое удаление ЗАВЕРШЕНО: удалено файлов %s", n)
    finally:
        progress.finish()


@router.post("/admin/delete_selected")
async def delete_selected(request: Request, background: BackgroundTasks):
    """Массовое удаление выбранных чекбоксами файлов (быстро, в фоне)."""
    auth.require_login(request)
    form = await request.form()
    ids = []
    for raw in form.getlist("ids"):
        try:
            ids.append(int(raw))
        except (TypeError, ValueError):
            continue
    background.add_task(_bulk_delete, ids)
    return RedirectResponse(f"/admin?deleted={len(ids)}", status_code=HTTP_303_SEE_OTHER)


@router.get("/admin/progress")
def progress_status(request: Request):
    auth.require_login(request)
    return JSONResponse(progress.get())


# ---------- диск и обслуживание ----------

@router.get("/admin/disk")
def disk_info(request: Request):
    auth.require_login(request)
    return JSONResponse(maintenance.disk_stats())


@router.post("/admin/cleanup")
def cleanup_now(request: Request):
    auth.require_login(request)
    rep = maintenance.cleanup()
    freed = rep["orphan_bytes"] + max(0, rep["db_before"] - rep["db_after"])
    return RedirectResponse(
        f"/admin/maintenance?cleaned={rep['orphans']}&threads={rep['threads_removed']}&freed={freed}",
        status_code=HTTP_303_SEE_OTHER,
    )


# Фраза, которую нужно ввести буквально для подтверждения полной очистки —
# страховка от случайного нажатия на необратимое действие.
_WIPE_CONFIRM_PHRASE = "УДАЛИТЬ ВСЁ"


def _wipe_everything() -> int:
    """Необратимо очищает базу знаний ЭТОГО инстанса целиком: все записи БД, весь
    поисковый индекс, все файлы и все картинки. Настройки, ключи API и промпты — НЕ
    трогает (они в settings_store, отдельная таблица).

    Работает исключительно через объекты текущего процесса (db/vectorstore/settings),
    которые уже настроены на СВОЮ коллекцию/базу/папки этого инстанса — как и все
    остальные admin-роуты. Отдельным скриптом в обход процесса ЗАПУСКАТЬ НЕЛЬЗЯ:
    именно так однажды случайно задело продакшн (стандартный SSH-шелл не подхватывает
    .env, и os.getenv откатывается на дефолт — имя продакшн-коллекции).
    """
    n = db.wipe_all()
    try:
        vectorstore.wipe_all()
    except Exception:
        log.exception("Полная очистка: ошибка очистки индекса")
    shutil.rmtree(settings.KB_DIR, ignore_errors=True)
    os.makedirs(settings.KB_DIR, exist_ok=True)
    shutil.rmtree(settings.ASSETS_DIR, ignore_errors=True)
    os.makedirs(settings.ASSETS_DIR, exist_ok=True)
    log.warning("ПОЛНАЯ ОЧИСТКА БАЗЫ ЗНАНИЙ выполнена (записей было: %s)", n)
    return n


@router.post("/admin/wipe_all")
def wipe_all(request: Request, confirm: str = Form("")):
    auth.require_login(request)
    if confirm.strip() != _WIPE_CONFIRM_PHRASE:
        return RedirectResponse("/admin/maintenance?err=confirm", status_code=HTTP_303_SEE_OTHER)
    n = _wipe_everything()
    return RedirectResponse(f"/admin/maintenance?wiped={n}", status_code=HTTP_303_SEE_OTHER)


@router.post("/admin/priority")
async def set_priority(request: Request, value: int = 0):
    """Пометить выбранные файлы приоритетными (value=1) или снять (value=0)."""
    auth.require_login(request)
    form = await request.form()
    ids = []
    for raw in form.getlist("ids"):
        try:
            ids.append(int(raw))
        except (TypeError, ValueError):
            continue
    if ids:
        db.set_priority(ids, value)
        for fid in ids:
            try:
                vectorstore.set_file_priority(fid, value)   # обновляем и индекс
            except Exception:
                log.exception("Приоритет: не обновился индекс для id=%s", fid)
    return RedirectResponse("/admin", status_code=HTTP_303_SEE_OTHER)


# ---------- переиндексация ----------

@router.post("/admin/reindex/{file_id}")
def reindex(request: Request, background: BackgroundTasks, file_id: int):
    auth.require_login(request)
    if db.get_file(file_id):
        db.set_status(file_id, "pending")
        background.add_task(ingest.ingest_file, file_id)
    return RedirectResponse("/admin", status_code=HTTP_303_SEE_OTHER)


# ---------- тест вопроса прямо из панели ----------

@router.post("/admin/test")
def test_question_legacy(request: Request):
    """Совместимость со старой формой — теперь тест живёт на своей странице."""
    auth.require_login(request)
    return RedirectResponse("/admin/test", status_code=HTTP_303_SEE_OTHER)


# ---------- настройки и API-ключи ----------

@router.get("/admin/settings", response_class=HTMLResponse)
def settings_page(request: Request, saved: int = 0):
    auth.require_login(request)
    ctx = _base_ctx(request)
    ctx.update({"s": settings_store.all_for_form(), "saved": bool(saved),
                "gigachat_cert": gigachat_client.cert_status()})
    return templates.TemplateResponse("settings.html", ctx)


@router.post("/admin/gigachat/update_cert")
def gigachat_update_cert(request: Request):
    """Перекачивает актуальный сертификат российского CA для TLS к GigaChat (кнопка
    в панели) — сам сертификат меняется редко, но раз в несколько лет истекает."""
    auth.require_login(request)
    try:
        status = gigachat_client.update_cert_bundle()
        return JSONResponse({"ok": True, "status": status})
    except Exception as e:  # noqa: BLE001
        log.exception("Не удалось обновить сертификат GigaChat")
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@router.post("/admin/settings")
async def settings_save(request: Request):
    auth.require_login(request)
    form = await request.form()

    # Обычные (несекретные) поля сохраняем как есть.
    for key in ["llm_provider", "agent_role", "rag_rules",
                "claude_model", "openai_model", "deepseek_model",
                "claude_max_tokens", "claude_effort", "openai_effort", "deepseek_effort",
                "gigachat_model", "gigachat_scope",
                "claude_proxy", "top_k", "priority_boost", "min_relevance",
                "search_mode", "public_base_url",
                "pachca_api_url", "pachca_allowed_ip", "reaction_indicator",
                "omnidesk_domain", "omnidesk_staff_email", "omnidesk_staff_id"]:
        if key in form:
            settings_store.set_value(key, form.get(key))

    # Чекбоксы приходят только когда включены — отсутствие поля в форме нельзя отличить
    # от «выключено» иначе как по этому маркеру. Без него любой частичный POST на этот
    # эндпоинт (например, точечное изменение одной настройки скриптом/API) молча сбросил
    # бы ВСЕ чекбоксы в выключенное состояние — так уже было и сломало нативные картинки
    # и контекст треда в Пачке.
    if "full_form" in form:
        settings_store.set_value("pachca_verify_ip", "verify_ip" in form)
        settings_store.set_value("pachca_thread_context", "pachca_thread_context" in form)
        settings_store.set_value("pachca_thread_replies", "pachca_thread_replies" in form)
        settings_store.set_value("pachca_native_images", "pachca_native_images" in form)
        settings_store.set_value("pachca_ask_rating", "pachca_ask_rating" in form)
        settings_store.set_value("omnidesk_reply_as_note", "omnidesk_reply_as_note" in form)

    # Секреты обновляем ТОЛЬКО если поле непустое (пустое = «оставить как было»).
    for key in ["anthropic_api_key", "openai_api_key", "deepseek_api_key", "gigachat_auth_key",
                "pachca_bot_token", "pachca_webhook_secret",
                "omnidesk_api_key", "omnidesk_webhook_token"]:
        val = (form.get(key) or "").strip()
        if val:
            settings_store.set_value(key, val)

    return RedirectResponse("/admin/settings?saved=1", status_code=HTTP_303_SEE_OTHER)


# ---------- массовая загрузка из папки на сервере ----------

def _bulk_ingest(folder: str, exts: set) -> None:
    """Рекурсивно индексирует поддерживаемые файлы из папки. Идёт в фоне сервиса."""
    log.info("Массовая загрузка из %s (расширения: %s)", folder, ", ".join(sorted(exts)))
    if not os.path.isdir(folder):
        log.error("Массовая загрузка: папка не найдена: %s", folder)
        return
    os.makedirs(settings.KB_DIR, exist_ok=True)
    try:
        vectorstore.ensure_collection()
    except Exception:
        log.exception("Массовая загрузка: не удалось подготовить индекс")
        return

    # Сначала соберём список файлов к загрузке (уже загруженные пропускаем) — для прогресса.
    targets = []
    skipped = 0
    for root, _dirs, files in os.walk(folder):
        for name in sorted(files):
            if os.path.splitext(name)[1].lower() not in exts:
                continue
            src = os.path.join(root, name)
            if db.file_by_source(src):
                skipped += 1
            else:
                targets.append(src)

    progress.start("Загрузка", len(targets))
    done = errors = 0
    try:
        for src in targets:
            name = os.path.basename(src)
            try:
                safe = _safe_name(name)
                stored = _new_stored_path(os.path.splitext(name)[1].lower())
                shutil.copy2(src, stored)
                fid = db.add_file(filename=safe, stored_path=stored,
                                  size=os.path.getsize(stored), source_path=src)
                ingest.ingest_file(fid)
                rec = db.get_file(fid)
                if rec and rec["status"] == "ready":
                    done += 1
                else:
                    errors += 1
            except Exception:
                errors += 1
                log.exception("Массовая загрузка: ошибка на файле %s", name)
            progress.step()
        log.info("Массовая загрузка ЗАВЕРШЕНА: проиндексировано %s, пропущено %s, ошибок %s",
                 done, skipped, errors)
    finally:
        progress.finish()


@router.post("/admin/bulk")
def bulk_ingest(request: Request, background: BackgroundTasks,
                path: str = Form(...), extensions: str = Form("md,txt,docx,yml,yaml")):
    auth.require_login(request)
    exts = {"." + e.strip().lstrip(".").lower() for e in extensions.split(",") if e.strip()}
    exts = {e for e in exts if e in ALLOWED_EXT}
    background.add_task(_bulk_ingest, path.strip(), exts)
    return RedirectResponse("/admin/maintenance?bulk=started", status_code=HTTP_303_SEE_OTHER)


# ---------- переиндексация всех файлов (напр. после смены хранилища) ----------

def _reindex_all() -> None:
    files = db.list_files()
    progress.start("Переиндексация", len(files))
    try:
        try:
            vectorstore.ensure_collection()
        except Exception:
            log.exception("Переиндексация: не удалось подготовить индекс")
        for f in files:
            try:
                ingest.ingest_file(f["id"])
            except Exception:
                log.exception("Переиндексация: ошибка на файле %s", f.get("filename"))
            progress.step()
        log.info("Переиндексация ЗАВЕРШЕНА: %s файлов", len(files))
    finally:
        progress.finish()


@router.post("/admin/reindex_all")
def reindex_all(request: Request, background: BackgroundTasks):
    auth.require_login(request)
    background.add_task(_reindex_all)
    return RedirectResponse("/admin/maintenance?reindex=started", status_code=HTTP_303_SEE_OTHER)


def _reindex_embeddings_only() -> None:
    """Как «Переиндексировать всё», но БЕЗ пересчёта summary для каталога — ни одного
    вызова LLM. Нужна после изменений в том, что попадает в эмбеддинг куска (например
    добавления заголовка статьи как контекста), когда сам текст и summary не менялись —
    пересчитывать summary для 1000+ файлов заново было бы чистой тратой API-вызовов."""
    files = db.list_files()
    progress.start("Переиндексация (только эмбеддинги)", len(files))
    try:
        try:
            vectorstore.ensure_collection()
        except Exception:
            log.exception("Переиндексация эмбеддингов: не удалось подготовить индекс")
        for f in files:
            try:
                ingest.ingest_file(f["id"], skip_summary=True)
            except Exception:
                log.exception("Переиндексация эмбеддингов: ошибка на файле %s", f.get("filename"))
            progress.step()
        log.info("Переиндексация эмбеддингов ЗАВЕРШЕНА: %s файлов", len(files))
    finally:
        progress.finish()


@router.post("/admin/reindex_embeddings")
def reindex_embeddings(request: Request, background: BackgroundTasks):
    auth.require_login(request)
    background.add_task(_reindex_embeddings_only)
    return RedirectResponse("/admin/maintenance?reindex=started", status_code=HTTP_303_SEE_OTHER)


# ---------- каталог (summary файлов для агрегационных вопросов) ----------

def _backfill_summaries() -> None:
    """Строит summary для уже проиндексированных файлов, у которых его ещё нет."""
    ids = db.files_missing_summary()
    progress.start("Построение каталога", len(ids))
    done = errors = 0
    try:
        for fid in ids:
            rec = db.get_file(fid)
            if not rec:
                progress.step()
                continue
            try:
                text = ""
                if rec.get("stored_path") and os.path.exists(rec["stored_path"]):
                    text = ingest.extract_text(rec["stored_path"])
                summary = ingest.generate_summary(rec["filename"], text)
                if summary:
                    db.set_summary(fid, summary)
                    done += 1
                else:
                    errors += 1
            except Exception:
                errors += 1
                log.exception("Каталог: не удалось построить summary для %s", rec.get("filename"))
            progress.step()
        log.info("Построение каталога ЗАВЕРШЕНО: готово %s, ошибок %s", done, errors)
    finally:
        progress.finish()


@router.post("/admin/backfill_summaries")
def backfill_summaries(request: Request, background: BackgroundTasks):
    auth.require_login(request)
    background.add_task(_backfill_summaries)
    return RedirectResponse("/admin/maintenance?catalog=started", status_code=HTTP_303_SEE_OTHER)


# ---------- лексический индекс (BM25 для гибридного поиска, см. rag._hybrid_rerank) ----------

def _backfill_fts() -> None:
    """Строит лексический индекс кусков для уже проиндексированных файлов, у которых
    его ещё нет (например файлы, загруженные до появления гибридного поиска). Текст
    берём заново из исходника и режем тем же chunk_text(), что и обычная индексация —
    без обращения к Qdrant/эмбеддингам, поэтому дёшево и не трогает векторный индекс."""
    ids = [fid for fid in (r["id"] for r in db.list_files() if r["status"] == "ready")
           if fid not in db.fts_indexed_file_ids()]
    progress.start("Лексический индекс", len(ids))
    done = errors = 0
    try:
        for fid in ids:
            rec = db.get_file(fid)
            if not rec:
                progress.step()
                continue
            try:
                text = ""
                if rec.get("stored_path") and os.path.exists(rec["stored_path"]):
                    text = ingest.extract_text(rec["stored_path"])
                chunks = ingest.chunk_text(text)
                if chunks:
                    db.index_chunks_fts(fid, chunks)
                    done += 1
                else:
                    errors += 1
            except Exception:
                errors += 1
                log.exception("Лексический индекс: не удалось построить для %s", rec.get("filename"))
            progress.step()
        log.info("Построение лексического индекса ЗАВЕРШЕНО: готово %s, ошибок %s", done, errors)
    finally:
        progress.finish()


@router.post("/admin/backfill_fts")
def backfill_fts(request: Request, background: BackgroundTasks):
    auth.require_login(request)
    background.add_task(_backfill_fts)
    return RedirectResponse("/admin/maintenance?fts=started", status_code=HTTP_303_SEE_OTHER)
