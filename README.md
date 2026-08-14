<h1 align="center">KB Agent</h1>
<p align="center">A self-hosted RAG agent that turns your knowledge base into an AI that actually answers support questions correctly.</p>

<p align="center">
  <img alt="Python" src="https://img.shields.io/badge/python-3.11-3776AB?logo=python&logoColor=white">
  <img alt="FastAPI" src="https://img.shields.io/badge/FastAPI-009688?logo=fastapi&logoColor=white">
  <img alt="Docker" src="https://img.shields.io/badge/Docker-ready-2496ED?logo=docker&logoColor=white">
  <img alt="Qdrant" src="https://img.shields.io/badge/vector%20store-Qdrant-DC244C">
</p>

<p align="center"><a href="#english">English</a> · <a href="#русский">Русский</a></p>

---

<a id="english"></a>
## English

I built this because support agents kept answering the same questions differently depending on who was on shift — the actual answer was buried somewhere across a pile of PDFs, spreadsheets and old chat exports, and nobody had time to search through all of it every time a customer asked something. KB Agent is my answer to that: throw your documents at it, it indexes them for semantic search, and it can either answer questions directly through a built-in admin console or plug straight into your support channels and answer on its own.

It's a single FastAPI service, one admin panel, no separate moving parts to babysit.

### What it does

**Knowledge base**
- Upload PDFs, Word/Excel files, YAML, plain text, or images — scanned documents and photos of whiteboards get OCR'd automatically (Russian + English).
- Add content straight from a URL, or paste raw text if you don't have a file for it.
- Edit, replace, delete, bulk-select, and mark individual files as higher priority so the agent leans on them more.
- Two search modes: a fast "catalog" pass over auto-generated summaries, or a slower full read-through of every matching document when you need it to be thorough.
- Full or partial reindexing (embeddings only, catalog summaries, full-text search) when you change how the pipeline works, without re-uploading anything.
- A test console right in the admin panel to see exactly what the agent would answer before it goes anywhere near a customer.

**AI providers** — pick one, switch anytime from the settings tab, no redeploy:
- Anthropic Claude
- OpenAI (GPT)
- DeepSeek
- GigaChat (Sber) — including its OAuth token dance and the Russian trusted root CA it needs for TLS

**Talks to your support tools**
- **Pachca** — receives questions via webhook (with signature verification and an optional IP allowlist), replies as the bot.
- **Omnidesk** — same idea over their webhook + HTTP Basic API, so it can pick up tickets and reply inline.

**Runs itself**
- Password-protected admin panel with its own session handling — no shared login with anything else.
- API keys and integration tokens live encrypted in the app's own settings store, not in a config file sitting on disk in plain text.
- Built-in analytics: what people are actually asking about, clustered by topic, plus per-user activity if you need to dig into one conversation.
- Log viewer, disk usage and cleanup tools, and a "wipe everything" switch for when a test environment needs to start clean.

### Under the hood

FastAPI + Jinja2 templates for the admin UI, an embedded Qdrant instance for vector search (no separate server to run — it's just files on disk), multilingual `e5` sentence embeddings, SQLite for metadata and full-text search, and Tesseract for OCR.

### Quick start (Docker)

```bash
git clone https://github.com/Krauzersan/kb-agent-v2.git
cd kb-agent-v2
cp .env.example .env      # then edit ADMIN_PASSWORD at the very least

docker build -t kb-agent-v2 .
docker run -d \
  --name kb-agent-v2 \
  --env-file .env \
  -p 8746:8746 \
  -v kb-agent-data:/data \
  kb-agent-v2
```

Open `http://localhost:8746/` and log in with the password you set. AI provider keys and integration tokens are entered later, inside the admin panel's Settings tab — they're never stored in `.env`.

### Quick start (without Docker)

If you'd rather run it directly on a Linux box with systemd:

```bash
python3 -m venv venv
venv/bin/pip install torch==2.5.1 --index-url https://download.pytorch.org/whl/cpu
venv/bin/pip install -r app/requirements.txt

cp .env.example .env      # edit ADMIN_PASSWORD, generate a SESSION_SECRET

venv/bin/uvicorn main:app --app-dir app --host 0.0.0.0 --port 8746
```

Or skip the manual steps and run `sudo bash deploy/install.sh` from the repo root — it does all of the above and installs `kb-agent-v2` as a systemd service.

### Configuration

Everything in `.env` is infrastructure only — ports, storage location, the admin password. API keys and third-party tokens are configured later, from the admin panel, and stored encrypted.

| Variable | Default | What it's for |
|---|---|---|
| `PORT` | `8746` | Port the service listens on |
| `DATA_DIR` | `./data` | Where the knowledge base, database, vector index and model cache live |
| `EMBEDDING_MODEL` | `intfloat/multilingual-e5-base` | Sentence embedding model (restart to change) |
| `ADMIN_PASSWORD` | — | Password for the admin panel — change this before going live |
| `SESSION_SECRET` | — | Signs session cookies — generate a random value, don't reuse it |
| `SESSION_COOKIE_NAME` | `session` | Only matters if you run more than one instance on the same domain |
| `ENCRYPTION_KEY` | *(empty)* | Optional key to encrypt stored API keys/tokens at rest |

### Project layout

```
kb-agent-v2/
├── Dockerfile
├── .env.example            # infrastructure only — no API keys
├── deploy/
│   └── kb-agent-v2.service # systemd unit, for a non-Docker setup
├── caddy/
│   └── Caddyfile           # optional reverse-proxy example
└── app/                    # the FastAPI service
    ├── main.py             # entrypoint
    ├── config.py           # infra settings, read from .env
    ├── settings_store.py   # API keys & tunables, entered in the admin UI
    ├── admin.py            # admin panel: upload, search, settings, analytics
    ├── webhook.py           / omnidesk_webhook.py   # inbound integrations
    ├── pachca.py            / omnidesk.py           # outbound integration clients
    ├── claude_client.py / openai_client.py / gigachat_client.py  # AI providers
    ├── rag.py / ingest.py / embeddings.py / vectorstore.py / db.py
    └── templates/           # admin UI pages
```

### A note on security

This is meant to run on a server you control, behind HTTPS. Set a real `ADMIN_PASSWORD` and a random `SESSION_SECRET` before exposing it to the internet, and set `ENCRYPTION_KEY` if you want the stored provider keys encrypted at rest rather than in plain SQLite.

---

<a id="русский"></a>
## Русский

Я сделал этого агента, потому что саппорт отвечал на одни и те же вопросы по-разному в зависимости от того, кто на смене — правильный ответ был где-то погребён среди кучи PDF-ов, таблиц и старых переписок, и перечитывать всё это каждый раз ни у кого не было времени. KB Agent — это решение: скармливаешь ему документы, он индексирует их для смыслового поиска, а дальше либо сам отвечает через встроенную админку, либо подключается напрямую к каналам поддержки и отвечает без вашего участия.

Это один FastAPI-сервис с одной админкой — не нужно поднимать и следить за кучей отдельных частей.

### Что умеет

**База знаний**
- Загрузка PDF, Word/Excel, YAML, обычного текста и изображений — сканы и фото досок распознаются автоматически через OCR (русский + английский).
- Добавление контента прямо по ссылке или вставкой текста, если файла как такового нет.
- Редактирование, замена, удаление, массовые операции, и приоритет для отдельных файлов, чтобы агент опирался на них сильнее.
- Два режима поиска: быстрый — по автоматически собранным кратким описаниям файлов, и медленный — полное прочтение всех подходящих документов, когда нужна максимальная точность.
- Полная или частичная переиндексация (только эмбеддинги, только каталог, только полнотекстовый поиск) при изменении логики — без повторной загрузки файлов.
- Тестовая консоль прямо в админке — можно проверить, что именно ответит агент, прежде чем это увидит клиент.

**AI-провайдеры** — выбираются и переключаются на лету, во вкладке настроек, без передеплоя:
- Anthropic Claude
- OpenAI (GPT)
- DeepSeek
- GigaChat (Сбер) — с полной поддержкой их OAuth-авторизации и российского корневого сертификата, без которого не пройдёт TLS

**Интеграции с саппортом**
- **Пачка** — принимает вопросы через вебхук (с проверкой подписи и опциональным списком разрешённых IP), отвечает от имени бота.
- **Omnidesk** — то же самое через их вебхук и HTTP Basic API: агент подхватывает обращения и отвечает прямо в тикете.

**Эксплуатация**
- Админ-панель с паролем и собственной сессией — не завязана на чужую авторизацию.
- API-ключи и токены интеграций хранятся в зашифрованном виде в собственном хранилище настроек приложения, а не в конфиге открытым текстом.
- Встроенная аналитика: какие вопросы реально задают, с группировкой по темам, плюс активность по конкретному пользователю, если нужно разобрать один диалог.
- Просмотр логов, статистика по диску, очистка, и рубильник «стереть всё» для тестовых окружений.

### Технически

FastAPI + Jinja2-шаблоны для админки, встроенный Qdrant для векторного поиска (без отдельного сервера — данные лежат файлами на диске), мультиязычные эмбеддинги `e5`, SQLite для метаданных и полнотекстового поиска, Tesseract для OCR.

### Быстрый старт (Docker)

```bash
git clone https://github.com/Krauzersan/kb-agent-v2.git
cd kb-agent-v2
cp .env.example .env      # обязательно поменяйте ADMIN_PASSWORD

docker build -t kb-agent-v2 .
docker run -d \
  --name kb-agent-v2 \
  --env-file .env \
  -p 8746:8746 \
  -v kb-agent-data:/data \
  kb-agent-v2
```

Откройте `http://localhost:8746/` и войдите по паролю, который задали. Ключи AI-провайдеров и токены интеграций вводятся позже, во вкладке «Настройки» в админке — в `.env` они никогда не хранятся.

### Быстрый старт (без Docker)

Если хотите запускать напрямую на Linux-сервере через systemd:

```bash
python3 -m venv venv
venv/bin/pip install torch==2.5.1 --index-url https://download.pytorch.org/whl/cpu
venv/bin/pip install -r app/requirements.txt

cp .env.example .env      # поменяйте ADMIN_PASSWORD, сгенерируйте SESSION_SECRET

venv/bin/uvicorn main:app --app-dir app --host 0.0.0.0 --port 8746
```

Либо пропустите ручные шаги и запустите `sudo bash deploy/install.sh` из корня репозитория — он сделает всё сам и поставит `kb-agent-v2` как systemd-сервис.

### Конфигурация

Всё, что в `.env` — исключительно инфраструктура: порт, где хранить данные, пароль админки. API-ключи и токены сторонних сервисов настраиваются позже, из админки, и хранятся зашифрованными.

| Переменная | По умолчанию | Назначение |
|---|---|---|
| `PORT` | `8746` | Порт, на котором слушает сервис |
| `DATA_DIR` | `./data` | Где лежат база знаний, БД, векторный индекс и кэш модели |
| `EMBEDDING_MODEL` | `intfloat/multilingual-e5-base` | Модель эмбеддингов (меняется только перезапуском) |
| `ADMIN_PASSWORD` | — | Пароль админ-панели — поменяйте перед запуском в прод |
| `SESSION_SECRET` | — | Подписывает сессионные куки — сгенерируйте случайное значение |
| `SESSION_COOKIE_NAME` | `session` | Важно только если на одном домене крутится несколько копий |
| `ENCRYPTION_KEY` | *(пусто)* | Опциональный ключ для шифрования хранимых ключей/токенов |

### Структура проекта

```
kb-agent-v2/
├── Dockerfile
├── .env.example            # только инфраструктура — без API-ключей
├── deploy/
│   └── kb-agent-v2.service # systemd-юнит для запуска без Docker
├── caddy/
│   └── Caddyfile           # пример конфига реверс-прокси (опционально)
└── app/                    # сам сервис на FastAPI
    ├── main.py             # точка входа
    ├── config.py           # инфраструктурные настройки из .env
    ├── settings_store.py   # API-ключи и параметры, вводимые в админке
    ├── admin.py            # админка: загрузка, поиск, настройки, аналитика
    ├── webhook.py           / omnidesk_webhook.py   # входящие интеграции
    ├── pachca.py            / omnidesk.py           # клиенты для ответов
    ├── claude_client.py / openai_client.py / gigachat_client.py  # AI-провайдеры
    ├── rag.py / ingest.py / embeddings.py / vectorstore.py / db.py
    └── templates/           # страницы админки
```

### О безопасности

Сервис рассчитан на запуск на своём сервере, за HTTPS. Перед тем как открывать его наружу, задайте настоящий `ADMIN_PASSWORD` и случайный `SESSION_SECRET`, а если хотите, чтобы ключи провайдеров хранились зашифрованными, а не в открытом виде в SQLite — задайте `ENCRYPTION_KEY`.
