<h1 align="center">KB Agent</h1>
<p align="center"><em>Dump your docs in, get a bot that actually knows what it's talking about.</em></p>

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

> Honestly? I got tired of watching support give three different answers to the same question — not because anyone was lazy, just because the *right* answer was sitting in some PDF nobody had opened in months. So I built the thing I actually wanted: feed it your docs, and instead of a folder you have to dig through, you get something you can just... ask.

### Who's this for

Short answer: you don't need to be a "company" for this to be useful.

- **Small and medium businesses** — give people the same correct answer at 3pm and at 3am, without paying someone to babysit a FAQ page all day.
- **Freelancers, consultants, solo founders** — your client gets an answer right now instead of waiting for you to dig through your notes on Monday.
- **Regular people automating their own stuff** — a course you teach, a community you run, docs for a side project, a family business where the "process" mostly just lives in someone's head.

Basically: if you can dump what you know into a bunch of files, this turns it into something people can actually talk to — in a chat window, or straight from Telegram, WhatsApp, wherever your people already hang out.

One FastAPI service, one admin panel. No zoo of microservices, no extra database to babysit.

### What it does

**Knowledge base** — the boring-but-important part
- Upload PDFs, Word/Excel files, YAML, plain text, or images. Scans and photos of whiteboards get OCR'd automatically (Russian + English), so yes, that phone photo of the whiteboard counts too.
- No file handy? Just paste a URL or drop in raw text — and attach a few screenshots right there if the text alone doesn't tell the whole story.
- Edit, replace, delete, bulk-select, and bump priority on the files that matter most, so the agent leans on those first.
- Two ways to search: a quick pass over auto-generated summaries, or a slow, thorough read-through of everything when "good enough" isn't good enough.
- Reindex all or part of it (embeddings, summaries, search index) whenever you tweak something — no need to re-upload a thing.
- There's a test console right in the panel, so you can grill the agent yourself before a real customer does.

**Brains** — pick a provider, switch whenever you feel like it, no redeploy needed:
- Anthropic Claude
- OpenAI (GPT)
- DeepSeek
- GigaChat (Sber) — OAuth dance and Russian root CA included, so you don't have to fight with that yourself

**Where it can chat**
- **Pachca** — webhook in, signature checked, replies as the bot.
- **Omnidesk** — same deal via their webhook + HTTP Basic API, picks up tickets and replies inline.
- **Telegram** — a plain Bot API webhook. Message the bot, get an answer from the knowledge base.
- **WhatsApp** — the official Cloud API (Meta Business Platform). Same idea: message in, answer out.

Quick honesty check: Telegram and WhatsApp are the new kids here, so they're still a bit more basic — plain text only, per-chat memory but no rating collection, and no webhook signature verification yet (see [Connecting messaging channels](#connecting-messaging-channels) for what that means in practice). Tokens, though, live in the same place as everything else now — the Settings tab, no `.env` editing needed. Pachca and Omnidesk have had more time in the oven — ratings, native image replies, IP allowlisting, the works. Telegram/WhatsApp will get there too if it turns out people actually want that.

**Doesn't need much babysitting**
- Password-protected admin panel with its own session — doesn't piggyback on any other login.
- Keys and tokens are stored encrypted in the app's own settings store, not sitting around in a config file in plain text.
- Built-in analytics — what people are actually asking, grouped by topic, plus a per-user view if you need to dig into one weird conversation.
- One-click Excel report (`.xlsx`, formatted and filterable) — an overview, a sheet of flagged problem answers (low-rated or answered with nothing from the knowledge base), and the full question log, all in one file for whoever likes spreadsheets more than dashboards.
- Logs, disk usage, cleanup tools, and a "just wipe everything" switch for when a test environment needs a fresh start.

### Under the hood

FastAPI, an embedded Qdrant for vector search (no extra server — just files on disk), multilingual `e5` embeddings, SQLite for metadata and full-text search, Tesseract for OCR. Nothing exotic, just parts chosen so you don't need a DevOps team to run this thing.

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
2. Paste it into the admin panel: **Settings → Telegram**, save. Takes effect immediately, no restart.
3. Point Telegram at your webhook (needs HTTPS):
   ```bash
   curl "https://api.telegram.org/bot<TOKEN>/setWebhook?url=https://your-domain.com/webhook/telegram"
   ```
4. Message the bot. It should answer from the knowledge base.

**WhatsApp** (official Cloud API, via [Meta for Developers](https://developers.facebook.com))

1. Create an app, add the **WhatsApp** product, and grab a test phone number (or your own verified one) — this gives you a Phone Number ID and a temporary access token; generate a permanent one under System Users before going live.
2. In the admin panel: **Settings → WhatsApp**, paste the access token and Phone Number ID, make up any string for the verify token — Meta just echoes it back to prove you control the endpoint. Save.
3. In the app's WhatsApp → Configuration screen, set the webhook URL to `https://your-domain.com/webhook/whatsapp`, paste the same verify token, and subscribe to the **messages** field. Meta will call the endpoint with a GET request first — it only succeeds if the verify token matches.
4. Message the test number from WhatsApp. It should answer from the knowledge base.

### Configuration

Everything in `.env` is infrastructure only — ports, storage location, the admin password. All API keys and third-party tokens (Claude/OpenAI/DeepSeek/GigaChat, Pachca, Omnidesk, Telegram, WhatsApp) are configured from the admin panel's Settings tab and stored encrypted — none of them live in `.env`.

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

> Если честно — я просто устал смотреть, как саппорт даёт три разных ответа на один и тот же вопрос. И дело не в том, что кто-то ленился — правильный ответ реально лежал в каком-то PDF-е, который никто не открывал с момента, как его написали. Вот и сделал то, чего самому не хватало: скармливаешь агенту документы, и вместо папки, которую надо *перерывать*, получаешь то, у чего можно просто... взять и спросить.

### Кому это пригодится

Если коротко — «компанией» быть не обязательно, чтобы от этого была польза.

- **Малому и среднему бизнесу** — чтобы клиент получал один и тот же правильный ответ что в три дня, что в три ночи, и не пришлось нанимать человека, единственная работа которого — следить, чтобы FAQ не устарел.
- **Фрилансерам, консультантам, соло-основателям** — клиент получает ответ прямо сейчас, а не ждёт до понедельника, пока вы доберётесь до своих заметок.
- **Обычным людям, которые автоматизируют свой кусок жизни** — курс, который ведёте, сообщество, которым рулите, документация к пет-проекту, семейный бизнес, где весь «процесс» на самом деле живёт в голове одного человека и больше нигде.

По сути: если вы можете собрать то, что знаете, в файлы — агент превратит это в то, с чем реально можно поговорить. В чате админки, или прямо из Telegram, WhatsApp — там, где уже сидят ваши люди.

Один FastAPI-сервис, одна админка. Никакого зоопарка микросервисов, никакой отдельной базы, за которой нужно следить.

### Что умеет

**База знаний** — скучная, но важная часть
- Загружайте PDF, Word/Excel, YAML, обычный текст или картинки. Сканы и фото досок распознаются сами через OCR (русский + английский) — да, то самое фото доски с телефона тоже сработает.
- Нет файла под рукой — просто вставьте ссылку или кусок текста, и сразу же приложите пару скриншотов, если одним текстом не обойтись.
- Редактирование, замена, удаление, массовые операции, и приоритет для важных файлов, чтобы агент в первую очередь опирался на них.
- Два режима поиска: быстрый — по коротким автоописаниям, и медленный, дотошный — когда «примерно так» не устраивает.
- Переиндексация целиком или частично (эмбеддинги, каталог, полнотекстовый поиск) при любых правках — без повторной загрузки файлов.
- Тестовая консоль прямо в админке — можно самим погонять агента вопросами, прежде чем это сделает настоящий клиент.

**Мозги** — выбираете провайдера, переключаете когда захотите, без передеплоя:
- Anthropic Claude
- OpenAI (GPT)
- DeepSeek
- GigaChat (Сбер) — с их OAuth-плясками и российским корневым сертификатом уже разобрались за вас

**Где может общаться**
- **Пачка** — вебхук на вход, подпись проверяется, отвечает от имени бота.
- **Omnidesk** — та же идея через их вебхук и HTTP Basic API: подхватывает обращения, отвечает прямо в тикете.
- **Telegram** — обычный вебхук Bot API. Написали боту — получили ответ из базы знаний.
- **WhatsApp** — официальный Cloud API (Meta Business Platform). Та же идея: сообщение на вход, ответ на выход.

Честно говоря: Telegram и WhatsApp тут новенькие, поэтому чуть попроще — только текст, память диалога есть, а вот сбора оценок и проверки подписи вебхука пока нет (см. [«Подключение мессенджеров»](#подключение-мессенджеров), что это значит на практике). А вот токены теперь там же, где и всё остальное — во вкладке «Настройки», редактировать `.env` не нужно. У Пачки и Omnidesk опыта побольше — оценки ответов, картинки нативным вложением, список разрешённых IP, всё как надо. Telegram/WhatsApp дотянем до того же уровня, если станет понятно, что оно того стоит.

**Не требует особого присмотра**
- Админ-панель с паролем и своей сессией — ни от кого чужого логина не зависит.
- Ключи и токены хранятся зашифрованными в собственном хранилище настроек, а не валяются в конфиге открытым текстом.
- Встроенная аналитика — что реально спрашивают, с группировкой по темам, плюс разбор по конкретному пользователю, если нужно понять один странный диалог.
- Excel-отчёт в один клик (`.xlsx`, с форматированием и фильтрами) — обзор, лист с проблемными ответами (низкая оценка или ответ вообще без опоры на базу) и полный лог вопросов, одним файлом для тех, кому таблицы понятнее дашбордов.
- Логи, место на диске, очистка, и кнопка «стереть всё» для тестовых окружений, которым нужен чистый старт.

### Технически

FastAPI, встроенный Qdrant для векторного поиска (без отдельного сервера — просто файлы на диске), мультиязычные эмбеддинги `e5`, SQLite для метаданных и полнотекстового поиска, Tesseract для OCR. Ничего экзотического — стек собран так, чтобы для запуска не нужна была отдельная DevOps-команда.

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
2. Вставьте его в админке: **Настройки → Telegram**, сохраните. Применяется сразу, без перезапуска.
3. Укажите Telegram адрес вебхука (нужен HTTPS):
   ```bash
   curl "https://api.telegram.org/bot<TOKEN>/setWebhook?url=https://ваш-домен.ru/webhook/telegram"
   ```
4. Напишите боту — должен ответить по базе знаний.

**WhatsApp** (официальный Cloud API, через [Meta for Developers](https://developers.facebook.com))

1. Создайте приложение, добавьте продукт **WhatsApp**, возьмите тестовый номер (или подключите свой верифицированный) — получите Phone Number ID и временный токен; постоянный токен генерируется позже в System Users, перед запуском в прод.
2. В админке: **Настройки → WhatsApp**, вставьте access-токен и Phone Number ID, в поле verify-токена — любая произвольная строка на ваш выбор: Meta просто вернёт её же, чтобы подтвердить, что вы владеете эндпоинтом. Сохраните.
3. В настройках приложения (WhatsApp → Configuration) укажите адрес вебхука `https://ваш-домен.ru/webhook/whatsapp`, тот же verify-токен, подпишитесь на событие **messages**. Meta сначала сделает GET-запрос — он пройдёт, только если verify-токен совпадёт.
4. Напишите на тестовый номер в WhatsApp — должен ответить по базе знаний.

### Конфигурация

Всё, что в `.env` — исключительно инфраструктура: порт, где хранить данные, пароль админки. Все API-ключи и токены (Claude/OpenAI/DeepSeek/GigaChat, Пачка, Omnidesk, Telegram, WhatsApp) настраиваются из вкладки «Настройки» в админке и хранятся зашифрованными — ни один из них не лежит в `.env`.

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
