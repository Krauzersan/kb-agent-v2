"""Учёт файлов базы знаний в SQLite (имя, статус индексации, число кусков)."""
import json
import os
import re
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

from config import settings

_lock = threading.Lock()


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


@contextmanager
def _conn():
    os.makedirs(settings.DATA_DIR_ABS, exist_ok=True)
    con = sqlite3.connect(settings.DB_PATH, timeout=30)
    con.row_factory = sqlite3.Row
    # WAL: писатель (индексация, лог вопросов) не блокирует читателей (админку, поиск) —
    # без него запись держит эксклюзивный лок на файл, и параллельные запросы ждут.
    # journal_mode персистентен в самом файле БД, но выставляем на каждом коннекте —
    # дёшево (no-op, если уже WAL) и не зависит от того, какой модуль подключился первым
    # (db.py и settings_store.py открывают ОДИН файл каждый своим соединением).
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA busy_timeout=30000")
    try:
        yield con
        con.commit()
    finally:
        con.close()


def init_db() -> None:
    with _lock, _conn() as con:
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS files (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                filename    TEXT    NOT NULL,
                stored_path TEXT    NOT NULL,
                size        INTEGER NOT NULL DEFAULT 0,
                status      TEXT    NOT NULL DEFAULT 'pending',  -- pending | indexing | ready | error
                chunks      INTEGER NOT NULL DEFAULT 0,
                error       TEXT,
                created_at  TEXT    NOT NULL,
                updated_at  TEXT    NOT NULL
            )
            """
        )
        # миграция: source_path — исходный путь файла (для массовой загрузки и дедупликации)
        cols = [r[1] for r in con.execute("PRAGMA table_info(files)").fetchall()]
        if "source_path" not in cols:
            con.execute("ALTER TABLE files ADD COLUMN source_path TEXT")
        if "priority" not in cols:
            con.execute("ALTER TABLE files ADD COLUMN priority INTEGER NOT NULL DEFAULT 0")
        if "summary" not in cols:
            con.execute("ALTER TABLE files ADD COLUMN summary TEXT")
        if "content_hash" not in cols:
            con.execute("ALTER TABLE files ADD COLUMN content_hash TEXT")

        # Лексический индекс кусков (BM25) — для гибридного поиска в rag.py: эмбеддинг
        # иногда ранжирует ниже точное совпадение (код ошибки, версия, название кассы),
        # BM25 это компенсирует. Отдельная таблица, не привязана к Qdrant — если её нет
        # для файла (ещё не бэкфилнули), гибридный поиск просто откатывается на чистый вектор.
        #
        # "stemmed" — единственная ИНДЕКСИРУЕМАЯ колонка (см. _stem_text): unicode61 без
        # стемминга не находил "списание" по запросу "списать" — разные словоформы для
        # FTS были просто разными словами. "text" остаётся UNINDEXED и хранит ОРИГИНАЛЬНЫЙ
        # текст — это то, что реально уходит в контекст модели (см. rag.py); стеммированную
        # кашу туда отдавать нельзя, она только для матчинга.
        cols = [r[1] for r in con.execute("PRAGMA table_info(chunks_fts)").fetchall()]
        if cols and "stemmed" not in cols:
            # Старая схема (до стемминга) — данные всё равно нужно переиндексировать
            # (см. admin._backfill_fts), пересоздаём таблицу целиком.
            con.execute("DROP TABLE chunks_fts")
        con.execute(
            "CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5("
            "file_id UNINDEXED, chunk_index UNINDEXED, text UNINDEXED, stemmed)"
        )

        # Лог реальных вопросов агенту (Пачка/Omnidesk/тест из панели) — отладочная
        # панель: какие вопросы задают и на каких кусках базы построен ответ, чтобы
        # можно было перейти к источнику и поправить его, если ответ оказался неверным.
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS query_log (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at  TEXT NOT NULL,
                channel     TEXT,
                mode        TEXT,
                question    TEXT NOT NULL,
                answer      TEXT,
                sources     TEXT
            )
            """
        )
        # миграция: кто спросил (Пачка user_id/имя), ключ треда (для привязки оценки
        # к конкретному ответу) и сама оценка 1-10 — метрики качества ответов бота.
        cols = [r[1] for r in con.execute("PRAGMA table_info(query_log)").fetchall()]
        if "thread_key" not in cols:
            con.execute("ALTER TABLE query_log ADD COLUMN thread_key TEXT")
        if "asker_user_id" not in cols:
            con.execute("ALTER TABLE query_log ADD COLUMN asker_user_id INTEGER")
        if "asker_name" not in cols:
            con.execute("ALTER TABLE query_log ADD COLUMN asker_name TEXT")
        if "rating" not in cols:
            con.execute("ALTER TABLE query_log ADD COLUMN rating INTEGER")
        if "topic" not in cols:
            con.execute("ALTER TABLE query_log ADD COLUMN topic TEXT")
        # Пометка «пробел исправлен» (страница «Аналитика» → «Пробелы в базе знаний»):
        # админ добавил недостающую информацию в базу, и старые ответы «без источников»
        # по этой теме больше не должны считаться открытой проблемой. Если тема снова
        # начнёт отвечаться без источников — это уже НОВЫЕ строки с resolved=0, пробел
        # закономерно всплывёт опять (не разовое отключение проверки, а именно квитанция
        # по конкретным старым ответам).
        if "resolved" not in cols:
            con.execute("ALTER TABLE query_log ADD COLUMN resolved INTEGER NOT NULL DEFAULT 0")

        # Без индексов эти колонки сканируются полностью на каждый запрос — аналитика,
        # темы/пробелы, история конкретного треда/пользователя. С ростом лога (тысячи
        # строк) это всё медленнее; индексы дешёвы на запись, но сильно ускоряют чтение.
        con.execute("CREATE INDEX IF NOT EXISTS idx_query_log_thread_key ON query_log(thread_key)")
        con.execute("CREATE INDEX IF NOT EXISTS idx_query_log_topic ON query_log(topic)")
        con.execute("CREATE INDEX IF NOT EXISTS idx_query_log_created_at ON query_log(created_at)")
        con.execute("CREATE INDEX IF NOT EXISTS idx_query_log_asker_user_id ON query_log(asker_user_id)")

        # Именные аккаунты админки (email + пароль) — в дополнение к общему паролю
        # (ADMIN_PASSWORD в .env), который продолжает работать как раньше.
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS admin_users (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                email         TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                salt          TEXT NOT NULL,
                created_at    TEXT NOT NULL
            )
            """
        )


def add_file(filename: str, stored_path: str, size: int, source_path: str = None) -> int:
    with _lock, _conn() as con:
        cur = con.execute(
            "INSERT INTO files (filename, stored_path, size, source_path, status, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, 'pending', ?, ?)",
            (filename, stored_path, size, source_path, _now(), _now()),
        )
        return int(cur.lastrowid)


def file_by_source(source_path: str):
    """Уже загружали файл с таким исходным путём? (для пропуска при массовой загрузке)"""
    with _lock, _conn() as con:
        row = con.execute(
            "SELECT * FROM files WHERE source_path = ?", (source_path,)
        ).fetchone()
        return dict(row) if row else None


def reset_stuck() -> int:
    """При старте: файлы, зависшие в indexing/pending (прерванные перезапуском),
    помечаем как error, чтобы их можно было переиндексировать."""
    with _lock, _conn() as con:
        cur = con.execute(
            "UPDATE files SET status='error', "
            "error='Индексация прервана (перезапуск). Нажмите ↻ для переиндексации.', "
            "updated_at=? WHERE status IN ('indexing','pending')",
            (_now(),),
        )
        return cur.rowcount


def set_status(file_id: int, status: str, chunks: int = None, error: str = None) -> None:
    with _lock, _conn() as con:
        fields = ["status = ?", "updated_at = ?"]
        values = [status, _now()]
        if chunks is not None:
            fields.append("chunks = ?")
            values.append(chunks)
        # ошибку записываем всегда (в т.ч. сбрасываем в NULL при успехе)
        fields.append("error = ?")
        values.append(error)
        values.append(file_id)
        con.execute(f"UPDATE files SET {', '.join(fields)} WHERE id = ?", values)


def list_files() -> list:
    with _lock, _conn() as con:
        rows = con.execute("SELECT * FROM files ORDER BY created_at DESC").fetchall()
        return [dict(r) for r in rows]


def get_file(file_id: int):
    with _lock, _conn() as con:
        row = con.execute("SELECT * FROM files WHERE id = ?", (file_id,)).fetchone()
        return dict(row) if row else None


def delete_file_row(file_id: int) -> None:
    with _lock, _conn() as con:
        con.execute("DELETE FROM files WHERE id = ?", (file_id,))


def set_content_hash(file_id: int, content_hash: str) -> None:
    """Отпечаток содержимого файла (sha1) — чтобы при повторном импорте того же
    источника отличать реально изменившиеся файлы от неизменных."""
    with _lock, _conn() as con:
        con.execute("UPDATE files SET content_hash = ?, updated_at = ? WHERE id = ?",
                    (content_hash, _now(), file_id))


def update_stored(file_id: int, stored_path: str, size: int) -> None:
    """Обновляет физический файл (путь + размер) уже существующей записи — используется,
    когда при повторном импорте содержимое источника изменилось (тот же file_id,
    старая копия на диске заменяется новой)."""
    with _lock, _conn() as con:
        con.execute("UPDATE files SET stored_path = ?, size = ?, updated_at = ? WHERE id = ?",
                    (stored_path, size, _now(), file_id))


def rename_file(file_id: int, filename: str) -> None:
    """Меняет отображаемое имя файла — используется при замене содержимого другим файлом
    (чтобы имя/расширение в списке соответствовали тому, что реально проиндексировано)."""
    with _lock, _conn() as con:
        con.execute("UPDATE files SET filename = ?, updated_at = ? WHERE id = ?",
                    (filename, _now(), file_id))


def wipe_all() -> int:
    """Удаляет ВСЕ записи о файлах (полная очистка базы знаний этого инстанса).
    Настройки и API-ключи лежат в отдельной таблице (settings_store) и не трогаются."""
    with _lock, _conn() as con:
        n = con.execute("SELECT COUNT(*) c FROM files").fetchone()["c"]
        con.execute("DELETE FROM files")
        con.execute("DELETE FROM chunks_fts")
        return int(n)


def set_priority(file_ids, value: int) -> None:
    """Пометить файлы приоритетными (1) или снять приоритет (0)."""
    ids = [int(i) for i in file_ids]
    if not ids:
        return
    v = 1 if value else 0
    with _lock, _conn() as con:
        q = ",".join("?" for _ in ids)
        con.execute(f"UPDATE files SET priority = ?, updated_at = ? WHERE id IN ({q})",
                    [v, _now(), *ids])


def priority_file_ids() -> set:
    """ID приоритетных файлов — источник правды для поиска (не зависит от индекса)."""
    with _lock, _conn() as con:
        rows = con.execute("SELECT id FROM files WHERE priority = 1").fetchall()
        return {int(r["id"]) for r in rows}


def has_priority() -> bool:
    """Есть ли хоть один приоритетный проиндексированный файл."""
    with _lock, _conn() as con:
        row = con.execute(
            "SELECT 1 FROM files WHERE priority = 1 AND status = 'ready' LIMIT 1"
        ).fetchone()
        return bool(row)


# ---------- каталог (краткое summary каждого файла — для агрегационных вопросов) ----------

def set_summary(file_id: int, summary: str) -> None:
    with _lock, _conn() as con:
        con.execute("UPDATE files SET summary = ?, updated_at = ? WHERE id = ?",
                    (summary, _now(), file_id))


def catalog_entries() -> list:
    """[{id, filename, summary, priority}] по всем готовым файлам с непустым summary.

    Приоритетные файлы — первыми, чтобы при обрезке контекста они не терялись
    и модель видела их раньше вспомогательных источников.
    """
    with _lock, _conn() as con:
        rows = con.execute(
            "SELECT id, filename, summary, priority FROM files "
            "WHERE status = 'ready' AND summary IS NOT NULL AND summary != '' "
            "ORDER BY priority DESC, filename"
        ).fetchall()
        return [dict(r) for r in rows]


def files_missing_summary() -> list:
    """ID готовых файлов, для которых ещё не построено summary (для бэкфилла)."""
    with _lock, _conn() as con:
        rows = con.execute(
            "SELECT id FROM files WHERE status = 'ready' "
            "AND (summary IS NULL OR summary = '')"
        ).fetchall()
        return [int(r["id"]) for r in rows]


def catalog_stats() -> dict:
    with _lock, _conn() as con:
        total = con.execute("SELECT COUNT(*) c FROM files WHERE status = 'ready'").fetchone()["c"]
        withsum = con.execute(
            "SELECT COUNT(*) c FROM files WHERE status = 'ready' "
            "AND summary IS NOT NULL AND summary != ''"
        ).fetchone()["c"]
    return {"total": int(total), "with_summary": int(withsum)}


# ---------- лексический индекс кусков (BM25 для гибридного поиска) ----------
#
# unicode61 (дефолтный токенизатор FTS5) не знает морфологии — "списание" и "списать"
# для него просто два разных слова, ноль общих токенов. Снимаем словоформы стеммером
# (Snowball, русский) ДО того, как текст попадёт в индекс/запрос: "бонусы"/"бонус",
# "кассы"/"касса" после стемминга совпадают и находятся друг через друга.
#
# Важная оговорка: это ЧИНИТ словоизменение (падеж, число, время), но НЕ словообразование
# — "списать" (глагол) и "списание" (отглагольное существительное) для стеммера тоже
# остаются разными основами ("списа" и "списан"), это не баг стеммера, а предел метода:
# производные слова — это не словоформы одного слова. Проверено эмпирически на паре
# snowball/pymorphy3 — ни один вариант эту конкретную пару не решает.
import snowballstemmer as _snowballstemmer  # noqa: E402

_FTS_TOKEN_RE = re.compile(r"\w+", re.UNICODE)
_stemmer = _snowballstemmer.stemmer("russian")


def _stem_text(text: str) -> str:
    """Токенизирует и стеммирует текст — ТОЛЬКО для колонки "stemmed" (индекс/запрос),
    никогда не для показа пользователю/модели (см. схему chunks_fts в init_db)."""
    tokens = _FTS_TOKEN_RE.findall((text or "").lower())
    if not tokens:
        return ""
    return " ".join(_stemmer.stemWords(tokens))


def index_chunks_fts(file_id: int, chunks: list) -> None:
    """Перезаписывает лексический индекс кусков файла (вызывается вместе с индексацией
    в Qdrant — chunk_index должен совпадать с тем, что кладётся в payload вектора)."""
    with _lock, _conn() as con:
        con.execute("DELETE FROM chunks_fts WHERE file_id = ?", (file_id,))
        con.executemany(
            "INSERT INTO chunks_fts (file_id, chunk_index, text, stemmed) VALUES (?, ?, ?, ?)",
            [(file_id, i, chunk, _stem_text(chunk)) for i, chunk in enumerate(chunks)],
        )


def delete_chunks_fts(file_id: int) -> None:
    with _lock, _conn() as con:
        con.execute("DELETE FROM chunks_fts WHERE file_id = ?", (file_id,))


def delete_chunks_fts_many(file_ids) -> None:
    ids = [int(i) for i in file_ids]
    if not ids:
        return
    with _lock, _conn() as con:
        q = ",".join("?" for _ in ids)
        con.execute(f"DELETE FROM chunks_fts WHERE file_id IN ({q})", ids)


def fts_indexed_file_ids() -> set:
    with _lock, _conn() as con:
        rows = con.execute("SELECT DISTINCT file_id FROM chunks_fts").fetchall()
        return {int(r["file_id"]) for r in rows}


def search_fts(query: str, limit: int = 50) -> list:
    """Лексический (BM25) поиск по кускам. Возвращает [{file_id, chunk_index, text,
    filename, rank}] в порядке релевантности (лучшие первые) — rank это bm25() SQLite:
    чем МЕНЬШЕ (отрицательнее), тем релевантнее.

    text/filename отдаём сразу (JOIN на files) — кандидаты отсюда могут пойти в контекст
    модели НАПРЯМУЮ, даже если векторный поиск их вообще не нашёл (см. rag._rrf_fuse):
    раньше BM25 использовался только чтобы дорасчитать скор уже найденным вектором
    кускам, теперь это самостоятельный источник кандидатов.

    Токенизируем запрос вручную, стеммируем (см. _stem_text) и склеиваем через OR —
    так избегаем проблем с FTS5-синтаксисом в сыром пользовательском вопросе (кавычки,
    дефисы, звёздочки трактуются MATCH как операторы, а нам нужен просто безопасный
    OR-поиск по словам) и заодно ищем по той же стеммированной колонке, что и индекс.
    """
    tokens = _FTS_TOKEN_RE.findall((query or "").lower())[:20]
    if not tokens:
        return []
    stemmed = [t for t in _stemmer.stemWords(tokens) if t]
    if not stemmed:
        return []
    match_q = " OR ".join(f'"{t}"' for t in stemmed)
    with _lock, _conn() as con:
        try:
            rows = con.execute(
                "SELECT cf.file_id AS file_id, cf.chunk_index AS chunk_index, "
                "cf.text AS text, f.filename AS filename, bm25(chunks_fts) AS rank "
                "FROM chunks_fts cf JOIN files f ON f.id = cf.file_id "
                "WHERE chunks_fts MATCH ? ORDER BY rank LIMIT ?",
                (match_q, limit),
            ).fetchall()
        except sqlite3.OperationalError:
            return []
    return [{"file_id": int(r["file_id"]), "chunk_index": int(r["chunk_index"]),
              "text": r["text"] or "", "filename": r["filename"] or "",
              "rank": float(r["rank"])} for r in rows]


# ---------- лог вопросов (отладочная панель) ----------

def log_query(channel: str, mode: str, question: str, answer: str, sources: list,
              thread_key: str = None, asker_user_id: int = None, asker_name: str = None) -> int:
    with _lock, _conn() as con:
        cur = con.execute(
            "INSERT INTO query_log (created_at, channel, mode, question, answer, sources, "
            "thread_key, asker_user_id, asker_name) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (_now(), channel, mode, question, answer,
             json.dumps(sources, ensure_ascii=False), thread_key, asker_user_id, asker_name),
        )
        return int(cur.lastrowid)


def set_rating(thread_key: str, rating: int) -> bool:
    """Проставляет оценку последнему неоценённому ответу этого треда (Пачка: сотрудник
    прислал число 1-10 в ответ на просьбу оценить). True, если нашлось что оценивать."""
    if not thread_key:
        return False
    with _lock, _conn() as con:
        row = con.execute(
            "SELECT id FROM query_log WHERE thread_key = ? AND rating IS NULL "
            "ORDER BY id DESC LIMIT 1", (thread_key,),
        ).fetchone()
        if not row:
            return False
        con.execute("UPDATE query_log SET rating = ? WHERE id = ?", (rating, row["id"]))
        return True


def _period_clause(date_from: str | None, date_to: str | None) -> tuple[str, list]:
    """Общий фрагмент WHERE для фильтра по периоду (по created_at, день в UTC как
    хранится в БД) — переиспользуется всеми аналитическими выборками аналитики,
    чтобы выбор периода на странице применялся одинаково ко всем графикам/таблицам."""
    clauses, params = [], []
    if date_from:
        clauses.append("substr(created_at, 1, 10) >= ?")
        params.append(date_from)
    if date_to:
        clauses.append("substr(created_at, 1, 10) <= ?")
        params.append(date_to)
    return (" AND " + " AND ".join(clauses)) if clauses else "", params


def rating_stats_by_user(date_from: str | None = None, date_to: str | None = None) -> list:
    """Статистика по авторам вопросов (Пачка): сколько спросили и как оценивают ответы —
    для страницы «Метрики». Отсортировано по числу вопросов (кто чаще всего спрашивает)."""
    extra, params = _period_clause(date_from, date_to)
    with _lock, _conn() as con:
        rows = con.execute(
            "SELECT COALESCE(asker_name, 'Неизвестно') AS asker_name, asker_user_id, "
            "COUNT(*) AS questions, COUNT(rating) AS rated, AVG(rating) AS avg_rating "
            f"FROM query_log WHERE thread_key IS NOT NULL{extra} "
            "GROUP BY asker_user_id, asker_name ORDER BY questions DESC", params,
        ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["avg_rating"] = round(d["avg_rating"], 1) if d["avg_rating"] is not None else None
        out.append(d)
    return out


def rating_overview(date_from: str | None = None, date_to: str | None = None) -> dict:
    extra, params = _period_clause(date_from, date_to)
    with _lock, _conn() as con:
        row = con.execute(
            "SELECT COUNT(*) AS questions, COUNT(rating) AS rated, AVG(rating) AS avg_rating "
            f"FROM query_log WHERE thread_key IS NOT NULL{extra}", params,
        ).fetchone()
    d = dict(row)
    d["avg_rating"] = round(d["avg_rating"], 1) if d["avg_rating"] is not None else None
    return d


def rating_distribution(date_from: str | None = None, date_to: str | None = None) -> list:
    """[{rating: 1..10, count}] — сколько раз ставили каждую оценку, для гистограммы
    на дашборде. Только реально оценённые ответы (rating IS NOT NULL); все 10 значений
    возвращаются всегда, даже с count=0, чтобы столбики на графике не «прыгали»."""
    extra, params = _period_clause(date_from, date_to)
    with _lock, _conn() as con:
        rows = con.execute(
            "SELECT rating, COUNT(*) AS count FROM query_log "
            f"WHERE rating IS NOT NULL{extra} GROUP BY rating", params,
        ).fetchall()
    counts = {int(r["rating"]): int(r["count"]) for r in rows}
    return [{"rating": v, "count": counts.get(v, 0)} for v in range(1, 11)]


def questions_per_day(days: int = 30, date_from: str | None = None, date_to: str | None = None) -> list:
    """[{date: 'YYYY-MM-DD', count}] по created_at, все каналы. Дни без вопросов
    возвращаются с count=0 — без зазоров, чтобы линия на графике не «телепортировалась».
    Без date_from/date_to — последние `days` дней (включая сегодня), как раньше.
    С ними — весь диапазон периода, выбранного на странице аналитики (потолок 366
    дней, чтобы линия на графике не расползлась на сотни точек при периоде «весь
    период» на многолетней базе)."""
    if date_from and date_to:
        start_date = datetime.strptime(date_from, "%Y-%m-%d").date()
        end_date = datetime.strptime(date_to, "%Y-%m-%d").date()
        if end_date < start_date:
            start_date, end_date = end_date, start_date
        span = min((end_date - start_date).days + 1, 366)
        end_date = start_date + timedelta(days=span - 1)
    else:
        end_date = datetime.now(timezone.utc).date()
        start_date = end_date - timedelta(days=days - 1)
        span = days
    since = start_date.strftime("%Y-%m-%d")
    until = end_date.strftime("%Y-%m-%d")
    with _lock, _conn() as con:
        rows = con.execute(
            "SELECT substr(created_at, 1, 10) AS day, COUNT(*) AS count FROM query_log "
            "WHERE substr(created_at, 1, 10) >= ? AND substr(created_at, 1, 10) <= ? GROUP BY day",
            (since, until),
        ).fetchall()
    counts = {r["day"]: int(r["count"]) for r in rows}
    out = []
    for i in range(span):
        d = (start_date + timedelta(days=i)).strftime("%Y-%m-%d")
        out.append({"date": d, "count": counts.get(d, 0)})
    return out


def list_by_asker(asker_user_id: int, limit: int = 200) -> list:
    """Все вопросы конкретного сотрудника (Пачка) с их оценками — для разворота
    строки в «Метриках» до списка конкретных вопрос/ответ/оценка."""
    with _lock, _conn() as con:
        rows = con.execute(
            "SELECT * FROM query_log WHERE thread_key IS NOT NULL AND asker_user_id = ? "
            "ORDER BY id DESC LIMIT ?", (asker_user_id, limit),
        ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        try:
            d["sources"] = json.loads(d.get("sources") or "[]")
        except (TypeError, ValueError):
            d["sources"] = []
        out.append(d)
    return out


# ---------- темы вопросов (аналитика: о чём чаще всего спрашивают) ----------

def set_topic(query_id: int, topic: str) -> None:
    with _lock, _conn() as con:
        con.execute("UPDATE query_log SET topic = ? WHERE id = ?", (topic, query_id))


def topics_list() -> list:
    """Уже использованные названия тем — чтобы классификатор переиспользовал их
    вместо того, чтобы плодить чуть разные формулировки для одного смысла."""
    with _lock, _conn() as con:
        rows = con.execute(
            "SELECT DISTINCT topic FROM query_log WHERE topic IS NOT NULL AND topic != '' "
            "ORDER BY topic"
        ).fetchall()
        return [r["topic"] for r in rows]


def untagged_query_ids(limit: int = 25) -> list:
    with _lock, _conn() as con:
        rows = con.execute(
            "SELECT id FROM query_log WHERE topic IS NULL ORDER BY id ASC LIMIT ?", (limit,)
        ).fetchall()
        return [int(r["id"]) for r in rows]


def count_untagged() -> int:
    with _lock, _conn() as con:
        row = con.execute("SELECT COUNT(*) c FROM query_log WHERE topic IS NULL").fetchone()
        return int(row["c"])


def query_rows_by_ids(ids: list) -> list:
    if not ids:
        return []
    with _lock, _conn() as con:
        q = ",".join("?" for _ in ids)
        rows = con.execute(
            f"SELECT id, question FROM query_log WHERE id IN ({q})", [int(i) for i in ids]
        ).fetchall()
        return [dict(r) for r in rows]


def topic_stats(date_from: str | None = None, date_to: str | None = None) -> list:
    """[{topic, questions, rated, avg_rating, no_sources}] — для страницы аналитики.
    no_sources — сколько вопросов темы agent ответил вообще без опоры на базу (пустые
    источники) И ЕЩЁ НЕ ПОМЕЧЕНЫ ИСПРАВЛЕННЫМИ: сильный сигнал, что тему стоит
    добавить/расширить в базе знаний, надёжнее низкой оценки (там ответ мог быть
    просто неточным, а не отсутствующим). Если админ пометил пробел исправленным
    (resolve_topic_gaps), эти старые строки в счётчик уже не попадают — но новый
    безысточниковый ответ по той же теме снова его поднимет."""
    extra, params = _period_clause(date_from, date_to)
    with _lock, _conn() as con:
        rows = con.execute(
            "SELECT topic, COUNT(*) AS questions, COUNT(rating) AS rated, "
            "AVG(rating) AS avg_rating, "
            "SUM(CASE WHEN (sources IS NULL OR sources = '' OR sources = '[]') "
            "AND resolved = 0 THEN 1 ELSE 0 END) AS no_sources "
            f"FROM query_log WHERE topic IS NOT NULL AND topic != ''{extra} "
            "GROUP BY topic ORDER BY questions DESC", params,
        ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["avg_rating"] = round(d["avg_rating"], 1) if d["avg_rating"] is not None else None
        out.append(d)
    return out


def resolve_topic_gaps(topic: str) -> int:
    """Помечает ВСЕ текущие безысточниковые ответы этой темы как исправленные —
    кнопка «Пометить исправленным» на странице аналитики. Возвращает, сколько
    строк реально пометил (0, если пробела уже и так не было)."""
    with _lock, _conn() as con:
        cur = con.execute(
            "UPDATE query_log SET resolved = 1 WHERE topic = ? AND resolved = 0 "
            "AND (sources IS NULL OR sources = '' OR sources = '[]')",
            (topic,),
        )
        return cur.rowcount


def set_resolved(query_id: int, resolved: bool) -> None:
    """Точечная пометка/снятие пометки одного ответа — со страницы темы, когда
    нужно исправить не всю тему целиком, а конкретный случай."""
    with _lock, _conn() as con:
        con.execute(
            "UPDATE query_log SET resolved = ? WHERE id = ?",
            (1 if resolved else 0, query_id),
        )


def topic_examples(topic: str, limit: int = 12) -> list:
    with _lock, _conn() as con:
        rows = con.execute(
            "SELECT id, created_at, channel, question, answer, rating, asker_name, "
            "sources, resolved "
            "FROM query_log WHERE topic = ? ORDER BY id DESC LIMIT ?", (topic, limit)
        ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        try:
            d["sources"] = json.loads(d.get("sources") or "[]")
        except (TypeError, ValueError):
            d["sources"] = []
        out.append(d)
    return out


def all_query_log_for_export(limit: int = 20000) -> list:
    """Весь лог вопросов для Excel-отчёта (см. export.py) — без пагинации, но с
    разумным потолком, чтобы не утащить в память лог за годы работы одним куском."""
    with _lock, _conn() as con:
        rows = con.execute(
            "SELECT * FROM query_log ORDER BY id DESC LIMIT ?", (limit,),
        ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        try:
            d["sources"] = json.loads(d.get("sources") or "[]")
        except (TypeError, ValueError):
            d["sources"] = []
        out.append(d)
    return out


def list_query_log(limit: int = 50, offset: int = 0, q: str = "") -> list:
    with _lock, _conn() as con:
        if q:
            like = f"%{q}%"
            rows = con.execute(
                "SELECT * FROM query_log WHERE question LIKE ? OR answer LIKE ? "
                "ORDER BY id DESC LIMIT ? OFFSET ?",
                (like, like, limit, offset),
            ).fetchall()
        else:
            rows = con.execute(
                "SELECT * FROM query_log ORDER BY id DESC LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        try:
            d["sources"] = json.loads(d.get("sources") or "[]")
        except (TypeError, ValueError):
            d["sources"] = []
        out.append(d)
    return out


def count_query_log(q: str = "") -> int:
    with _lock, _conn() as con:
        if q:
            like = f"%{q}%"
            row = con.execute(
                "SELECT COUNT(*) c FROM query_log WHERE question LIKE ? OR answer LIKE ?",
                (like, like),
            ).fetchone()
        else:
            row = con.execute("SELECT COUNT(*) c FROM query_log").fetchone()
    return int(row["c"]) if row else 0


def clear_query_log() -> int:
    with _lock, _conn() as con:
        cur = con.execute("DELETE FROM query_log")
        return cur.rowcount


def get_admin_user(email: str):
    with _lock, _conn() as con:
        row = con.execute(
            "SELECT * FROM admin_users WHERE email = ?", (email.strip().lower(),)
        ).fetchone()
        return dict(row) if row else None


def upsert_admin_user(email: str, password_hash: str, salt: str) -> None:
    email = email.strip().lower()
    with _lock, _conn() as con:
        con.execute(
            """
            INSERT INTO admin_users (email, password_hash, salt, created_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(email) DO UPDATE SET password_hash = excluded.password_hash,
                                              salt = excluded.salt
            """,
            (email, password_hash, salt, _now()),
        )
