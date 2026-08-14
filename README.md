<h1 align="center">KB Agent</h1>
<p align="center"><em>Turn a pile of documents into a support agent that actually knows your business.</em></p>

<p align="center">
  <img alt="Python" src="https://img.shields.io/badge/python-3.11-3776AB?logo=python&logoColor=white">
  <img alt="FastAPI" src="https://img.shields.io/badge/FastAPI-009688?logo=fastapi&logoColor=white">
  <img alt="Docker" src="https://img.shields.io/badge/Docker-ready-2496ED?logo=docker&logoColor=white">
  <img alt="Qdrant" src="https://img.shields.io/badge/vector%20store-Qdrant-DC244C">
  <img alt="License" src="https://img.shields.io/badge/self--hosted-your%20server%2C%20your%20data-6E56CF">
</p>

<p align="center"><a href="#english"><strong>English</strong></a> &nbsp;·&nbsp; <a href="#русский"><strong>Русский</strong></a></p>

<br>

<a id="english"></a>
## English

> I built this after one too many afternoons watching support agents give three different answers to the same question — not because anyone was careless, but because the real answer was buried in some PDF nobody had opened since it was written. KB Agent is the fix I wanted for myself: point it at your documents, and it turns into something you can actually *ask*, instead of a folder you have to *search*.

### Who it's for

You don't need to be a company to get value out of this — it scales down just as well as it scales up.

- **Small and medium businesses** that want consistent, correct support answers around the clock, without hiring someone whose whole job is babysitting a FAQ doc.
- **Freelancers, consultants, and solo founders** who'd rather a client get an instant, accurate answer than wait until Monday for you to check your notes.
- **Anyone automating their own corner of life** — a course you teach, a community you run, a side project's documentation, a family business where half the "process" lives in one person's head and nowhere else.

If you can gather what you know into files, this will turn it into something people can talk to — through a chat window, or straight from Telegram, WhatsApp, or wherever your people already are.

It's a single FastAPI service, one admin panel — no cluster of microservices to babysit, no separate vector database to run.

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
- **Telegram** — a regular Bot API webhook. Anyone who messages the bot gets answered from the knowledge base.
- **WhatsApp** — official WhatsApp Cloud API (Meta Business Platform), same idea: webhook in, an answer back out.

Telegram and WhatsApp are wired in at a basic level for now — plain text in, plain text out, configured via `.env` rather than the admin panel (see [Connecting messaging channels](#connecting-messaging-channels)). Pachca and Omnidesk are the two that have the full admin-panel treatment (tokens, thread context, ratings, etc.); Telegram/WhatsApp will get the same if it turns out to be worth it.

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

### Connecting messaging channels

Both integrations are basic on purpose: plain-text questions in, plain-text answers out, no signature verification on the webhook yet. That's fine behind a firewall or for a low-traffic bot; if the endpoint is going to sit on the open internet and get real traffic, add Telegram's `secret_token` header check or WhatsApp's `X-Hub-Signature-256` verification before relying on it (both endpoints are short, single-purpose files — `app/telegram_webhook.py` and `app/whatsapp_webhook.py` — easy to extend).

**Telegram**

1. Message [@BotFather](https://t.me/BotFather), run `/newbot`, grab the token it gives you.
2. Put it in `.env` as `TELEGRAM_BOT_TOKEN`, restart the service.
3. Point Telegram at your webhook (needs HTTPS):
   ```bash
   curl "https://api.telegram.org/bot<TOKEN>/setWebhook?url=https://your-domain.com/webhook/telegram"
   ```
4. Message the bot. It should answer from the knowledge base.

**WhatsApp** (official Cloud API, via [Meta for Developers](https://developers.facebook.com))

1. Create an app, add the **WhatsApp** product, and grab a test phone number (or your own verified one) — this gives you a Phone Number ID and a temporary access token; generate a permanent one under System Users before going live.
2. Put `WHATSAPP_ACCESS_TOKEN` and `WHATSAPP_PHONE_NUMBER_ID` in `.env`. Make up any string for `WHATSAPP_VERIFY_TOKEN` — Meta just echoes it back to prove you control the endpoint.
3. In the app's WhatsApp → Configuration screen, set the webhook URL to `https://your-domain.com/webhook/whatsapp`, paste the same verify token, and subscribe to the **messages** field. Meta will call the endpoint with a GET request first — it only succeeds if `WHATSAPP_VERIFY_TOKEN` matches.
4. Message the test number from WhatsApp. It should answer from the knowledge base.

### Configuration

Everything in `.env` is infrastructure only — ports, storage location, the admin password. API keys and third-party tokens for Claude/Pachca/Omnidesk are configured later, from the admin panel, and stored encrypted; Telegram/WhatsApp are the exception for now (see above).

| Variable | Default | What it's for |
|---|---|---|
| `PORT` | `8746` | Port the service listens on |
| `DATA_DIR` | `./data` | Where the knowledge base, database, vector index and model cache live |
| `EMBEDDING_MODEL` | `intfloat/multilingual-e5-base` | Sentence embedding model (restart to change) |
| `ADMIN_PASSWORD` | — | Password for the admin panel — change this before going live |
| `SESSION_SECRET` | — | Signs session cookies — generate a random value, don't reuse it |
| `SESSION_COOKIE_NAME` | `session` | Only matters if you run more than one instance on the same domain |
| `ENCRYPTION_KEY` | *(empty)* | Optional key to encrypt stored API keys/tokens at rest |
| `TELEGRAM_BOT_TOKEN` | *(empty)* | Bot token from @BotFather — leave empty to disable the channel |
| `WHATSAPP_ACCESS_TOKEN` | *(empty)* | Permanent access token from Meta for Developers |
| `WHATSAPP_PHONE_NUMBER_ID` | *(empty)* | Phone Number ID from the same place |
| `WHATSAPP_VERIFY_TOKEN` | *(empty)* | Any string you pick — used only for the webhook handshake |

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
    ├── webhook.py    / omnidesk_webhook.py / telegram_webhook.py / whatsapp_webhook.py  # inbound integrations
    ├── pachca.py     / omnidesk.py         / telegram_client.py  / whatsapp_client.py   # outbound integration clients
    ├── claude_client.py / openai_client.py / gigachat_client.py  # AI providers
    ├── rag.py / ingest.py / embeddings.py / vectorstore.py / db.py
    └── templates/           # admin UI pages
```

### A note on security

This is meant to run on a server you control, behind HTTPS. Set a real `ADMIN_PASSWORD` and a random `SESSION_SECRET` before exposing it to the internet, and set `ENCRYPTION_KEY` if you want the stored provider keys encrypted at rest rather than in plain SQLite.

---

<a id="русский"></a>
## Русский

> Я сделал этого агента после того, как в очередной раз посмотрел, как саппорт даёт три разных ответа на один и тот же вопрос — не потому что кто-то халтурил, а потому что правильный ответ лежал в каком-то PDF-е, который никто не открывал с момента, как его написали. KB Agent — это то, чего мне самому не хватало: скармливаешь ему документы, и вместо папки, которую нужно *перерывать*, получаешь то, у чего можно просто *спросить*.

### Кому это пригодится

Чтобы получить пользу от этого агента, не обязательно быть компанией — он одинаково хорошо работает и в большом, и в малом масштабе.

- **Малому и среднему бизнесу**, который хочет давать клиентам стабильные и правильные ответы круглосуточно, не нанимая отдельного человека, чья единственная работа — следить за актуальностью FAQ.
- **Фрилансерам, консультантам и соло-основателям**, которым важнее, чтобы клиент получил точный ответ сразу, а не ждал до понедельника, пока вы найдёте время заглянуть в свои заметки.
- **Всем, кто автоматизирует свой личный кусочек жизни** — курс, который вы ведёте, сообщество, которым управляете, документацию к пет-проекту, семейный бизнес, где половина «процессов» живёт исключительно в голове одного человека и больше нигде.

Если вы можете собрать то, что знаете, в файлы — агент превратит это в то, с чем можно поговорить: через чат в админке или прямо из Telegram, WhatsApp и других мест, где уже находятся ваши люди.

Это один FastAPI-сервис с одной админкой — не нужно поднимать и следить за зоопарком отдельных сервисов или отдельной векторной базой.

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
- **Telegram** — обычный вебхук Bot API. Кто угодно пишет боту — получает ответ из базы знаний.
- **WhatsApp** — официальный WhatsApp Cloud API (Meta Business Platform), та же идея: вебхук на вход, ответ на выход.

Telegram и WhatsApp пока подключены на базовом уровне: только текст, без доп. проверок подписи, настраиваются через `.env`, а не через админку (см. [«Подключение мессенджеров»](#подключение-мессенджеров)). У Пачки и Omnidesk — полноценная интеграция с настройками в админке (токены, контекст треда, оценки и т.д.); Telegram/WhatsApp дотянем до того же уровня, если понадобится.

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

<a id="подключение-мессенджеров"></a>
### Подключение мессенджеров

Обе интеграции намеренно базовые: текст вопроса на входе, текст ответа на выходе, без проверки подписи вебхука. Для закрытого сервера или бота с небольшим трафиком этого достаточно; если эндпоинт будет смотреть в открытый интернет и получать реальный трафик — добавьте проверку заголовка `secret_token` у Telegram или `X-Hub-Signature-256` у WhatsApp, прежде чем полагаться на это всерьёз (оба эндпоинта — короткие файлы на одну задачу: `app/telegram_webhook.py` и `app/whatsapp_webhook.py`, дополнить несложно).

**Telegram**

1. Напишите [@BotFather](https://t.me/BotFather), выполните `/newbot`, заберите токен.
2. Впишите его в `.env` как `TELEGRAM_BOT_TOKEN`, перезапустите сервис.
3. Укажите Telegram адрес вебхука (нужен HTTPS):
   ```bash
   curl "https://api.telegram.org/bot<TOKEN>/setWebhook?url=https://ваш-домен.ru/webhook/telegram"
   ```
4. Напишите боту — должен ответить по базе знаний.

**WhatsApp** (официальный Cloud API, через [Meta for Developers](https://developers.facebook.com))

1. Создайте приложение, добавьте продукт **WhatsApp**, возьмите тестовый номер (или подключите свой верифицированный) — получите Phone Number ID и временный токен; постоянный токен генерируется позже в System Users, перед запуском в прод.
2. Впишите `WHATSAPP_ACCESS_TOKEN` и `WHATSAPP_PHONE_NUMBER_ID` в `.env`. В `WHATSAPP_VERIFY_TOKEN` — любая произвольная строка: Meta просто вернёт её же, чтобы подтвердить, что вы владеете эндпоинтом.
3. В настройках приложения (WhatsApp → Configuration) укажите адрес вебхука `https://ваш-домен.ru/webhook/whatsapp`, тот же verify-токен, подпишитесь на событие **messages**. Meta сначала сделает GET-запрос — он пройдёт, только если `WHATSAPP_VERIFY_TOKEN` совпадёт.
4. Напишите на тестовый номер в WhatsApp — должен ответить по базе знаний.

### Конфигурация

Всё, что в `.env` — исключительно инфраструктура: порт, где хранить данные, пароль админки. Ключи Claude/Пачки/Omnidesk настраиваются позже, из админки, и хранятся зашифрованными; Telegram/WhatsApp пока исключение (см. выше).

| Переменная | По умолчанию | Назначение |
|---|---|---|
| `PORT` | `8746` | Порт, на котором слушает сервис |
| `DATA_DIR` | `./data` | Где лежат база знаний, БД, векторный индекс и кэш модели |
| `EMBEDDING_MODEL` | `intfloat/multilingual-e5-base` | Модель эмбеддингов (меняется только перезапуском) |
| `ADMIN_PASSWORD` | — | Пароль админ-панели — поменяйте перед запуском в прод |
| `SESSION_SECRET` | — | Подписывает сессионные куки — сгенерируйте случайное значение |
| `SESSION_COOKIE_NAME` | `session` | Важно только если на одном домене крутится несколько копий |
| `ENCRYPTION_KEY` | *(пусто)* | Опциональный ключ для шифрования хранимых ключей/токенов |
| `TELEGRAM_BOT_TOKEN` | *(пусто)* | Токен от @BotFather — пусто значит канал выключен |
| `WHATSAPP_ACCESS_TOKEN` | *(пусто)* | Постоянный токен доступа из Meta for Developers |
| `WHATSAPP_PHONE_NUMBER_ID` | *(пусто)* | Phone Number ID оттуда же |
| `WHATSAPP_VERIFY_TOKEN` | *(пусто)* | Любая строка на ваш выбор — только для подтверждения вебхука |

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
    ├── webhook.py    / omnidesk_webhook.py / telegram_webhook.py / whatsapp_webhook.py  # входящие интеграции
    ├── pachca.py     / omnidesk.py         / telegram_client.py  / whatsapp_client.py   # клиенты для ответов
    ├── claude_client.py / openai_client.py / gigachat_client.py  # AI-провайдеры
    ├── rag.py / ingest.py / embeddings.py / vectorstore.py / db.py
    └── templates/           # страницы админки
```

### О безопасности

Сервис рассчитан на запуск на своём сервере, за HTTPS. Перед тем как открывать его наружу, задайте настоящий `ADMIN_PASSWORD` и случайный `SESSION_SECRET`, а если хотите, чтобы ключи провайдеров хранились зашифрованными, а не в открытом виде в SQLite — задайте `ENCRYPTION_KEY`.

---

<p align="center">Built by someone who got tired of answering the same question three different ways.<br>Hope it saves you a few afternoons too. · Сделано тем, кто устал отвечать на один вопрос по-разному. Надеюсь, вам это сэкономит пару вечеров.</p>
