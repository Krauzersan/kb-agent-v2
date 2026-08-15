"""Хранилище настроек и API-ключей в SQLite (вводятся в админ-панели).

Здесь живут все «изменяемые на лету» параметры: ключ Claude, токены Пачки и т.п.
Значения читаются из таблицы settings; если параметр не задан — берётся дефолт.
"""
from __future__ import annotations

import base64
import hashlib
import logging
import os
import sqlite3
import threading
from contextlib import contextmanager

from config import settings as cfg

log = logging.getLogger("settings_store")
_lock = threading.Lock()

_ENC_PREFIX = "enc:v1:"


def _cipher():
    """Fernet-шифр для секретов (ENCRYPTION_KEY -> 32-байтный ключ). None, если
    ключ не задан в .env — тогда секреты хранятся как раньше, без шифрования."""
    if not cfg.ENCRYPTION_KEY:
        return None
    try:
        from cryptography.fernet import Fernet
    except ImportError:
        log.warning("ENCRYPTION_KEY задан, но пакет cryptography не установлен — "
                     "секреты хранятся без шифрования")
        return None
    key = base64.urlsafe_b64encode(hashlib.sha256(cfg.ENCRYPTION_KEY.encode()).digest())
    return Fernet(key)


def _encrypt(value: str) -> str:
    c = _cipher()
    if not c or not value:
        return value
    return _ENC_PREFIX + c.encrypt(value.encode()).decode()


def _decrypt(value: str) -> str:
    if not value or not value.startswith(_ENC_PREFIX):
        return value  # старое значение без шифрования — читаем как есть
    c = _cipher()
    if not c:
        return value  # ключа нет — расшифровать нечем, отдаём как есть (не должно происходить)
    try:
        from cryptography.fernet import InvalidToken
        return c.decrypt(value[len(_ENC_PREFIX):].encode()).decode()
    except InvalidToken:
        log.error("Не удалось расшифровать секрет (неверный ENCRYPTION_KEY?)")
        return ""

# Ключ -> значение по умолчанию. Тип определяется по дефолту (bool/int/str).
DEFAULTS: dict[str, object] = {
    # Выбор LLM-провайдера: claude | openai | deepseek
    "llm_provider": "claude",
    # Claude (Anthropic)
    "anthropic_api_key": "",
    "claude_model": "claude-sonnet-5",
    "claude_max_tokens": 1024,
    # Глубина размышлений/качество ответа: low | medium | high | xhigh | max.
    # Выше — точнее и подробнее, но дольше и дороже. high — сбалансированный дефолт API.
    "claude_effort": "high",
    # OpenAI (ChatGPT)
    "openai_api_key": "",
    "openai_model": "gpt-4o-mini",
    # Глубина размышлений для «думающих» моделей OpenAI (gpt-5.x): minimal|low|medium|high.
    # На обычных моделях (gpt-4o и т.п.) не применяется — параметр им не отправляется.
    "openai_effort": "medium",
    # DeepSeek (совместим с OpenAI API)
    "deepseek_api_key": "",
    "deepseek_model": "deepseek-chat",
    # Глубина размышлений для рассуждающих моделей DeepSeek: low|high|max.
    "deepseek_effort": "high",
    # GigaChat (Сбер)
    "gigachat_auth_key": "",            # "Authorization key" из личного кабинета developers.sber.ru
    "gigachat_scope": "GIGACHAT_API_PERS",   # GIGACHAT_API_PERS | GIGACHAT_API_B2B | GIGACHAT_API_CORP
    "gigachat_model": "GigaChat-2",
    # Прокси для обхода региональной блокировки Anthropic (напр. http://user:pass@host:port)
    "claude_proxy": "",
    # Роль/персона агента (редактируется в панели). Правила работы с базой знаний
    # дописываются к ней автоматически в коде.
    "agent_role": (
        "Ты — эксперт платформы MAXMA. Работаешь сразу в трёх ролях, выбирай подходящую под "
        "вопрос (иногда нужны несколько сразу):\n"
        "1. Инженер технической поддержки — глубоко разбираешься в работе платформы, решаешь "
        "технические вопросы и ошибки, объясняешь настройки пошагово.\n"
        "2. Интегратор — разбираешь задачи по API и интеграциям, предлагаешь технические решения, "
        "проектируешь и адаптируешь механики интеграции в рамках программ лояльности MAXMA "
        "(подключение, начисление и списание баллов, акции и промокоды, кассовые и онлайн-"
        "интеграции, вебхуки, обмен данными).\n"
        "3. CRM-маркетолог — сегментация, коммуникации и цепочки, акции, аналитика; даёшь "
        "профессиональные рекомендации по удержанию и росту выручки.\n"
        "При необходимости пишешь техническое задание (ТЗ) по маркетингу и интеграциям — "
        "структурно, по шагам, понятно.\n\n"
        "Общайся профессионально, честно и по делу на русском языке.\n\n"
        "По вопросам об API и интеграции в первую очередь опирайся на документ openapi.yml — "
        "конкретные эндпоинты, методы, поля и параметры бери только оттуда, не изобретай их. "
        "Файлы с «omnidesk»/«pachca» в названии — это переписки поддержки с клиентами: полезны "
        "для примеров и контекста, но не как первичный источник правил (общий приоритет "
        "источников — по пометке ★, она задаётся отдельно и одинакова для всей базы).\n\n"
        "Когда предлагаешь интеграцию, маркетинговую механику или ТЗ — уточни бизнес-задачу, "
        "предложи 1–2 рабочих варианта в рамках возможностей MAXMA, укажи нужные части "
        "API/настроек (со ссылкой на источник) и предупреди о нюансах и ограничениях."
    ),
    # Правила работы с базой знаний — тоже редактируются в панели.
    "rag_rules": (
        "Работа с источниками:\n"
        "- Выдержки из базы знаний ниже — приоритетный источник. Сверяйся с ними в первую "
        "очередь. Названия файлов и пометки источника (скобки, ★ и т.п.) нигде в ответе не "
        "упоминай — пользователь их не увидит.\n"
        "- Если в базе есть ответ — отвечай по нему.\n"
        "- Если прямого ответа в базе нет — можешь рассуждать и предлагать решения на основе "
        "своих профессиональных знаний, помечая это как своё экспертное предложение, "
        "а не факт из базы.\n"
        "- Не выдумывай конкретные детали продукта (точные названия пунктов меню, настроек, "
        "цены), которых нет в базе — предложи их проверить."
    ),
    # Поиск
    "top_k": 5,
    # Роутер режимов обработки вопроса (см. router.py):
    # auto — классификатор сам решает FAST/AGGREGATION/DEEP по смыслу вопроса
    # fast — форсировать обычный гибридный поиск для ЛЮБОГО вопроса (роутер не вызывается)
    # deep — форсировать декомпозицию (deep.py) для ЛЮБОГО вопроса
    "router_mode": "auto",
    # Режим ответа на «агрегационные» вопросы (перечисли все / список всех / какие есть):
    # auto       — быстрый обход по краткому каталогу (summary) всех файлов
    # full_scan  — медленный, но исчерпывающий обход РЕАЛЬНОГО текста всех файлов (map-reduce)
    "search_mode": "auto",
    # Насколько сильнее приоритетные файлы (прибавка к близости 0..1). 0 = выкл.
    "priority_boost": "0.06",
    # Минимальная близость найденного куска (по косинусу вектора). Ниже — считаем, что
    # в базе ответа нет. 0 = выключено. Применяется, только если реранкер НЕ отработал
    # на этом запросе — иначе см. min_relevance_rerank ниже (разные шкалы, разные пороги).
    "min_relevance": "0",
    # Кросс-энкодер реранкер поверх RRF-пула (см. reranker.py) — точнее ранжирует, но
    # добавляет CPU-задержку на каждый вопрос. По умолчанию выключен: новая фича не
    # должна молча менять поведение прода при деплое, включать осознанно после теста.
    "reranker_enabled": False,
    # Тот же предохранитель, что и min_relevance, но по скору реранкера (сигмоида
    # кросс-энкодера, 0..1) — НЕ то же самое число, что косинус: 0.72 по одной шкале и
    # 0.72 по другой означают разную степень уверенности, поэтому порог отдельный.
    # Применяется только когда реранкер реально отработал на запросе. 0 = выключено.
    "min_relevance_rerank": "0",
    # Публичный адрес этого сервиса (без слэша на конце) — используется, чтобы строить
    # ссылки на картинки, извлечённые из ZIP-импорта (папка kb_assets, раздаётся статикой
    # по /assets/...). Поменять, если изменится IP/домен сервиса.
    "public_base_url": "",
    # Пачка
    "pachca_api_url": "https://api.pachca.com/api/shared/v1",
    "pachca_bot_token": "",
    "pachca_webhook_secret": "",
    "pachca_verify_ip": True,
    "pachca_allowed_ip": "37.200.70.177",
    "reaction_indicator": "",
    "pachca_thread_context": True,  # в тредах помнить контекст обсуждения
    # False (по умолчанию, старое поведение) — на новый вопрос вне треда отвечаем прямо
    # в чат репликой на сообщение. True — создаём тред на этом сообщении (Пачка API
    # POST /messages/{id}/thread) и отвечаем в нём; дальнейшие ответы в этом треде
    # ведут себя как обычный pachca-тред (включая память, если pachca_thread_context=True).
    "pachca_thread_replies": False,
    # True — картинки из ответа (свои же, из папки kb_assets) грузятся в Пачку и
    # вставляются как настоящее вложение вместо текстовой ссылки. Если для конкретной
    # картинки загрузка не удалась — тихо остаётся обычная ссылка (старое поведение).
    "pachca_native_images": True,
    # True — после ответа в треде бот просит оценить ответ (1-10), просто числом в ответ.
    # Оценка сохраняется в лог вопросов, привязана к автору вопроса — для метрик.
    "pachca_ask_rating": True,
    # Omnidesk
    "omnidesk_domain": "",          # поддомен: <domain>.omnidesk.ru
    "omnidesk_staff_email": "",     # email сотрудника для API
    "omnidesk_api_key": "",         # API-ключ
    "omnidesk_staff_id": "",        # ID сотрудника-бота (необязательно)
    "omnidesk_webhook_token": "",   # общий секрет для проверки вебхука
    "omnidesk_reply_as_note": False,  # отвечать внутренней заметкой (для проверки человеком)
    # Telegram (базовый уровень интеграции — см. telegram_client.py/telegram_webhook.py)
    "telegram_bot_token": "",       # токен от @BotFather
    # Кому бот отвечает: пусто = всем, кто напишет. Иначе — список через запятую или
    # с новой строки: числовой user_id и/или @username (без учёта регистра).
    "telegram_allowed_users": "",
    # WhatsApp Cloud API, Meta Business Platform (базовый уровень интеграции)
    "whatsapp_access_token": "",    # постоянный access-токен из Meta for Developers
    "whatsapp_phone_number_id": "",  # Phone Number ID оттуда же
    "whatsapp_verify_token": "",    # придуманная строка для подтверждения вебхука (GET)
    # Кому бот отвечает: пусто = всем, иначе — номера через запятую/с новой строки,
    # в том же формате, что присылает WhatsApp (цифры, без "+").
    "whatsapp_allowed_numbers": "",
}

# Какие поля считаются «секретными» (маскируются в интерфейсе)
SECRET_KEYS = {
    "anthropic_api_key", "openai_api_key", "deepseek_api_key", "gigachat_auth_key",
    "pachca_bot_token", "pachca_webhook_secret",
    "omnidesk_api_key", "omnidesk_webhook_token",
    "telegram_bot_token", "whatsapp_access_token", "whatsapp_verify_token",
}


@contextmanager
def _conn():
    os.makedirs(cfg.DATA_DIR_ABS, exist_ok=True)
    con = sqlite3.connect(cfg.DB_PATH, timeout=30)
    con.row_factory = sqlite3.Row
    # WAL — тот же файл БД, что и db.py, оба модуля открывают свои соединения;
    # см. комментарий в db.py._conn для почему.
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA busy_timeout=30000")
    try:
        yield con
        con.commit()
    finally:
        con.close()


def init() -> None:
    with _lock, _conn() as con:
        con.execute(
            "CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)"
        )


def _cast(key: str, raw: str):
    default = DEFAULTS.get(key, "")
    if isinstance(default, bool):
        return str(raw).strip().lower() in {"1", "true", "yes", "on"}
    if isinstance(default, int):
        try:
            return int(raw)
        except (TypeError, ValueError):
            return default
    return raw


# get() дёргается по несколько раз за один ответ (top_k, priority_boost, min_relevance,
# reranker_enabled, search_mode, провайдер...) — открывать под это новое sqlite-соединение
# каждый раз накладно. Настройки меняются только через set_value (админка), поэтому
# простой in-process кэш с точечной инвалидацией на запись безопасен и не требует TTL.
_cache: dict = {}


def get(key: str):
    with _lock:
        if key in _cache:
            return _cache[key]
        with _conn() as con:
            row = con.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        if row is None or row["value"] is None or row["value"] == "":
            result = DEFAULTS.get(key, "")
        else:
            raw = row["value"]
            if key in SECRET_KEYS:
                raw = _decrypt(raw)
            result = _cast(key, raw)
        _cache[key] = result
        return result


def set_value(key: str, value) -> None:
    if isinstance(value, bool):
        value = "true" if value else "false"
    value = str(value)
    if key in SECRET_KEYS:
        value = _encrypt(value)
    with _lock:
        with _conn() as con:
            con.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )
        _cache.pop(key, None)


def is_set(key: str) -> bool:
    """Задан ли непустой секрет (для отметки «сохранён» в UI)."""
    with _lock, _conn() as con:
        row = con.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return bool(row and row["value"])


def all_for_form() -> dict:
    """Значения для отображения в форме. Секреты не отдаём, только флаг «задан»."""
    out = {}
    for key, default in DEFAULTS.items():
        if key in SECRET_KEYS:
            out[key] = ""  # не показываем секрет
            out[key + "__is_set"] = is_set(key)
        else:
            out[key] = get(key)
    return out
