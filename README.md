<h1 align="center">RAG Agent</h1>
<p align="center"><em>Dump your docs in, get a bot that actually knows what it's talking about.</em></p>

<p align="center">
  <img alt="Python" src="https://img.shields.io/badge/python-3.11-3776AB?logo=python&logoColor=white">
  <img alt="FastAPI" src="https://img.shields.io/badge/FastAPI-009688?logo=fastapi&logoColor=white">
  <img alt="Docker" src="https://img.shields.io/badge/Docker-ready-2496ED?logo=docker&logoColor=white">
  <img alt="Qdrant" src="https://img.shields.io/badge/vector%20store-Qdrant-DC244C">
  <img alt="Hybrid search" src="https://img.shields.io/badge/search-vector%20%2B%20BM25%20%2B%20RRF-F5A623">
  <img alt="Self-hosted" src="https://img.shields.io/badge/self--hosted-your%20server%2C%20your%20data-6E56CF">
  <br>
  <img alt="Last commit" src="https://img.shields.io/github/last-commit/Krauzersan/rag-agent?color=blue">
  <img alt="Stars" src="https://img.shields.io/github/stars/Krauzersan/rag-agent?style=flat&color=yellow">
</p>

<p align="center"><a href="#english"><strong>English</strong></a> &nbsp;·&nbsp; <a href="#русский"><strong>Русский</strong></a></p>

<br>

<a id="english"></a>
## English

> Honestly? I got tired of watching support give three different answers to the same question — not because anyone was lazy, just because the *right* answer was sitting in some PDF nobody had opened in months. So I built the thing I actually wanted: feed it your docs, and instead of a folder you have to dig through, you get something you can just... ask.

<figure>
<img src="docs/architecture-en.svg" alt="Diagram: channels send a question or a document into the FastAPI service. Documents get chunked and embedded into Qdrant (vectors) and SQLite FTS5 (full-text). A question is first classified by a router into FAST, AGGREGATION, or DEEP: FAST searches both stores at once and fuses results with RRF, refined by an optional cross-encoder reranker; AGGREGATION scans the catalog or the full text of the base; DEEP decomposes the question into sub-questions, searches facts for each, drafts a plan, and generates the answer section by section. All three paths go to an LLM with automatic provider fallback, and the answer returns to whichever channel asked.">
<figcaption align="center"><sub>How a question becomes an answer — a router picks FAST (hybrid search + RRF + reranker), AGGREGATION (catalog/full-scan), or DEEP (decompose → plan → per-section generation) before handing off to whichever LLM provider is configured.</sub></figcaption>
</figure>

<details open>
<summary><b>📕 Table of contents</b></summary>

- 🎯 [Who's this for](#who-this-is-for)
- 🌟 [What it does](#what-it-does)
- 🔎 [Under the hood](#under-the-hood)
- 🚀 [Quick start — Docker Compose + HTTPS](#quickstart-docker-caddy)
- 🐳 [Quick start — Docker, no Caddy](#quickstart-docker-no-caddy)
- 🛠️ [Quick start — without Docker](#quickstart-no-docker)
- 🔌 [Connecting messaging channels](#messaging-channels)
- ⚙️ [Configuration](#configuration)
- 📁 [Project layout](#project-layout)
- 🔒 [A note on security](#security-note)

</details>

<a id="who-this-is-for"></a>
### 🎯 Who's this for

You don't have to be a company for this to be useful.

- **Small and medium businesses** — give people the same correct answer at 3pm and at 3am, without paying someone to babysit a FAQ page all day.
- **Freelancers, consultants, solo founders** — your client gets an answer right now instead of waiting for you to dig through your notes on Monday.
- **Regular people automating their own stuff** — a course you teach, a community you run, docs for a side project, a family business where the "process" mostly just lives in someone's head.

Get what you know into files and this turns it into something people can actually talk to: through the admin panel, or straight from Telegram and WhatsApp, wherever people already are.

One FastAPI service, one admin panel. No zoo of microservices, no extra database to babysit.

<a id="what-it-does"></a>
### 🌟 What it does

**📚 Knowledge base** — the boring-but-important part
- Upload PDFs, Word/Excel files, YAML, plain text, or images. Scans and photos of whiteboards get OCR'd automatically (Russian + English), so yes, that phone photo of the whiteboard counts too.
- No file handy? Just paste a URL or drop in raw text — and attach a few screenshots right there if the text alone doesn't tell the whole story.
- Edit, replace, delete, bulk-select, and bump priority on the files that matter most, so the agent leans on those first.
- Two ways to search: a quick pass over auto-generated summaries, or a slow, thorough read-through of everything when "good enough" isn't good enough.
- Reindex all or part of it (embeddings, summaries, search index) whenever you tweak something — no need to re-upload a thing.
- There's a test console right in the panel, so you can grill the agent yourself before a real customer does.

**🧠 Brains** — pick a provider, switch whenever you feel like it, no redeploy needed:
- Anthropic Claude
- OpenAI (GPT)
- DeepSeek
- GigaChat (Sber) — OAuth dance and Russian root CA included, so you don't have to fight with that yourself
- Automatic fallback: if the provider you picked doesn't answer (no key, rate-limited, having a bad day), it quietly retries with the next provider that *does* have a key configured, instead of just showing the user an error.

**💬 Where it can chat**
- **Pachca** — webhook in, signature checked, replies as the bot.
- **Omnidesk** — same deal via their webhook + HTTP Basic API, picks up tickets and replies inline.
- **Telegram** — a plain Bot API webhook. Message the bot, get an answer from the knowledge base — screenshots from the base come through as real photos, not link-dumps.
- **WhatsApp** — the official Cloud API (Meta Business Platform). Same idea: message in, answer (and any relevant screenshot) out.

Telegram and WhatsApp are newer here, so they're still a bit more basic: plain text only, no conversation memory (each message is answered on its own — an earlier version kept per-chat history and it made the agent loop on old questions, so it's off on purpose for now), no rating collection, and no webhook signature verification (see [Connecting messaging channels](#messaging-channels) for what that means in practice). Pachca and Omnidesk have had more time in the oven — thread memory, ratings, IP allowlisting, the works. Telegram/WhatsApp will catch up if people actually end up using them.

**🛡️ Doesn't need much babysitting**
- Password-protected admin panel with its own session — doesn't piggyback on any other login.
- Keys and tokens are stored encrypted in the app's own settings store, not sitting around in a config file in plain text.
- One Analytics page, two tabs: **Answer ratings** (who's asking, how they rate the answers, a rating-distribution chart, a 30-day question trend) and **Topics & gaps** (what people ask about most, a top-topics chart, and topics the agent answered with nothing from the knowledge base, flagged as "gaps"). No charting library — the charts are inline SVG the server draws itself. Fix a gap in the base and mark it resolved with one click; it only comes back if the same topic goes unanswered again.
- One-click Excel report (`.xlsx`, formatted and filterable) — an overview with a rolling 7-day digest baked right in, a sheet of flagged problem answers (low-rated or answered with nothing from the knowledge base), and the full question log, all in one file for whoever likes spreadsheets more than dashboards.
- Logs, disk usage, cleanup tools, and a "just wipe everything" switch for when a test environment needs a fresh start.

<a id="under-the-hood"></a>
### 🔎 Under the hood

**Router** — every question is classified before it's answered, into one of three pipelines:
- **FAST** — a normal, self-contained question. Runs the hybrid search + reranker pipeline described below. The overwhelming majority of questions land here.
- **AGGREGATION** — the answer is a full list scattered across many files ("what payment methods are there", "list all integrations") — routes to the catalog/full-scan retrieval described in the Retrieval section below.
- **DEEP** — a complex multi-part question or an explicit request for a spec/plan ("write a spec for integrating with X", "compare Y and Z and propose an architecture"). Runs its own pipeline: decompose the question into sub-questions → search the knowledge base for each one independently → draft a plan (section outline) from what was found → generate each section as its own model call, grounded in the gathered facts → merge the section drafts into one coherent final answer. An order of magnitude more model calls than FAST (closer to a dozen than one), so it's reserved for questions that actually need that depth — not triggered by a normal question.

Classification is a short model call reading the question's actual meaning, not a keyword match — a regex pre-filter still catches the obvious AGGREGATION phrasings to skip that extra round-trip, but FAST vs. DEEP has no keyword shortcut; telling "explain how X works" from "write a full spec covering X, Y and Z with all the edge cases" genuinely needs the model to read the question. Override it in Settings → Search (**Auto** / **Fast** / **Deep**) — Fast and Deep force that pipeline for *every* question, bypassing the classifier (and its extra model call) entirely.

**Retrieval** — how a FAST question finds the right chunk:
- **Hybrid search** — a vector search (semantic similarity) and a BM25 lexical search (SQLite FTS5) run independently, then get combined with **Reciprocal Rank Fusion**. This matters more than it sounds: a chunk with an exact match — an error code, a version number, a product name — can rank low semantically and never make it into a pure vector top-N. Fusing two independent result lists means a strong lexical hit surfaces even when the embedding never would have found it on its own.
- **Cross-encoder reranker** (optional, off by default) — vector and BM25 each score a chunk against the question in isolation; a reranker reads the question *and* the chunk together in one pass, which ranks more accurately at the cost of a CPU pass per candidate. Runs only over the narrow pool hybrid search already picked, not the whole base. Toggle it in Settings → Search.
- Priority boost for files marked ★, and a same-topic filter so, say, a question about one POS system's refund flow doesn't get an answer built from a different POS system's docs.

> [!WARNING]
> The reranker is CPU-only, no batching, and scores the *whole* hybrid pool (`top_k × 3`, or more) on every question. `BAAI/bge-reranker-v2-m3` alone needs a good chunk of a GB resident in memory, and scoring dozens of real-length chunks on a small/shared box can take well over a minute — past most reverse-proxy timeouts. Fine on a machine with a few dedicated CPU cores and 4 GB+ RAM to spare; leave it off on a cramped VPS.

**Everything else:**

| Layer | What runs there |
|---|---|
| Web / API | FastAPI, Uvicorn, server-rendered Jinja2 admin panel (no separate frontend build) |
| Vector search | Qdrant — its own process (Docker Compose runs it as a sidecar; a bare-metal install expects one already running) |
| Metadata, full-text, settings | SQLite — file registry, query log, FTS5 for lexical search, encrypted key/value settings store |
| Embeddings & reranking | `sentence-transformers` on CPU — `intfloat/multilingual-e5-base` for embeddings, `BAAI/bge-reranker-v2-m3` for reranking (both restart-only to swap) |
| LLM providers | Anthropic Claude, OpenAI, DeepSeek, GigaChat (Sber) — picked per-request with automatic fallback if the primary one has no key or errors out |
| Document parsing | `pypdf`, `python-docx`, `openpyxl`, PyYAML, Pillow + Tesseract OCR (Russian + English) |
| Reports | `openpyxl` — formatted, filterable `.xlsx` export straight from the admin panel |
| Secrets at rest | `cryptography` (Fernet) — encrypts stored provider keys/tokens when `ENCRYPTION_KEY` is set |
| Deploy | Docker + Docker Compose, Caddy for automatic HTTPS, or plain systemd on bare metal |

Nothing exotic, just parts chosen so you don't need a DevOps team to run this thing.

<a id="quickstart-docker-caddy"></a>
### 🚀 Quick start (Docker Compose + HTTPS)

The easiest way to run this somewhere real — Caddy in front, automatic HTTPS, one command:

```bash
git clone https://github.com/Krauzersan/rag-agent.git
cd rag-agent
cp .env.example .env      # edit ADMIN_PASSWORD and DOMAIN at the very least

docker compose up -d --build
```

Point `DOMAIN` in `.env` at a real domain (`kb.example.com`) pointed at this server and Caddy gets you a certificate automatically; leave it as `:80` for plain HTTP on a local/test box. The app container isn't exposed directly — everything goes through Caddy. Open `https://your-domain/` and log in with the `ADMIN_PASSWORD` you set.

<a id="quickstart-docker-no-caddy"></a>
### 🐳 Quick start (Docker, no Caddy)

Prefer to handle TLS yourself, or run behind an existing reverse proxy? The command below runs Qdrant alongside the app on a shared Docker network.

> [!TIP]
> Vector search needs a Qdrant instance reachable at `QDRANT_URL` — the `docker run` for it below isn't optional, even in the "no Caddy" setup.

```bash
git clone https://github.com/Krauzersan/rag-agent.git
cd rag-agent
cp .env.example .env      # then edit ADMIN_PASSWORD at the very least

docker network create rag-agent-net
docker run -d --name rag-agent-qdrant --network rag-agent-net -v rag-agent-qdrant:/qdrant/storage qdrant/qdrant:latest

docker build -t rag-agent .
docker run -d \
  --name rag-agent \
  --network rag-agent-net \
  --env-file .env \
  -e QDRANT_URL=http://rag-agent-qdrant:6333 \
  -p 8746:8746 \
  -v rag-agent-data:/data \
  rag-agent
```

Open `http://localhost:8746/` and log in with the password you set. Either way — AI provider keys and integration tokens are entered later, inside the admin panel's Settings tab, never in `.env`.

<a id="quickstart-no-docker"></a>
### 🛠️ Quick start (without Docker)

If you'd rather run it directly on a Linux box with systemd. You'll need a Qdrant instance running too — see [Qdrant's own quick start](https://qdrant.tech/documentation/quickstart/) — `QDRANT_URL` in `.env` defaults to `http://127.0.0.1:6333`, so the simplest setup runs it on the same box:

```bash
python3 -m venv venv
venv/bin/pip install torch==2.5.1 --index-url https://download.pytorch.org/whl/cpu
venv/bin/pip install -r app/requirements.txt

cp .env.example .env      # edit ADMIN_PASSWORD, generate a SESSION_SECRET

venv/bin/uvicorn main:app --app-dir app --host 0.0.0.0 --port 8746
```

Or skip the manual steps and run `sudo bash deploy/install.sh` from the repo root — it installs everything above as a systemd service (still doesn't install Qdrant itself — set that up first).

<a id="messaging-channels"></a>
### 🔌 Connecting messaging channels

> [!CAUTION]
> Both integrations are basic on purpose: plain-text questions in, plain-text answers out, no signature verification on the webhook yet. That's fine behind a firewall or for a low-traffic bot; if the endpoint is going to sit on the open internet and get real traffic, add Telegram's `secret_token` header check or WhatsApp's `X-Hub-Signature-256` verification before relying on it (both endpoints are short, single-purpose files — `app/telegram_webhook.py` and `app/whatsapp_webhook.py` — easy to extend).

Both also support an allowlist — a list of Telegram usernames/IDs or WhatsApp phone numbers, set in the same Settings tab. Leave it empty and the bot answers whoever writes to it; fill it in and everyone else gets a polite "no access" reply instead of an answer.

**Telegram**

1. Message [@BotFather](https://t.me/BotFather), run `/newbot`, grab the token it gives you.
2. Paste it into the admin panel: **Settings → Telegram**, save. That's it — the app registers the webhook with Telegram itself (needs `public_base_url` set on the Search tab; the Settings page tells you if that's missing).
3. Message the bot. It should answer from the knowledge base.

**WhatsApp** (official Cloud API, via [Meta for Developers](https://developers.facebook.com))

1. Create an app, add the **WhatsApp** product, and grab a test phone number (or your own verified one) — this gives you a Phone Number ID and a temporary access token; generate a permanent one under System Users before going live.
2. In the admin panel: **Settings → WhatsApp**, paste the access token and Phone Number ID, make up any string for the verify token — Meta just echoes it back to prove you control the endpoint. Save.
3. Meta doesn't offer an API for this next part, so it's the one manual step: in the app's WhatsApp → Configuration screen, set the webhook URL to `https://your-domain.com/webhook/whatsapp`, paste the same verify token, and subscribe to the **messages** field. Meta calls the endpoint with a GET request first — it only succeeds if the verify token matches.
4. Message the test number from WhatsApp. It should answer from the knowledge base.

<a id="configuration"></a>
### ⚙️ Configuration

Everything in `.env` is infrastructure only — ports, storage location, the admin password. All API keys and third-party tokens (Claude/OpenAI/DeepSeek/GigaChat, Pachca, Omnidesk, Telegram, WhatsApp) are configured from the admin panel's Settings tab and stored encrypted — none of them live in `.env`.

| Variable | Default | What it's for |
|---|---|---|
| `PORT` | `8746` | Port the service listens on |
| `DATA_DIR` | `./data` | Where the knowledge base, database, vector index and model cache live |
| `QDRANT_URL` | `http://127.0.0.1:6333` | Where to reach Qdrant — the Docker Compose / `docker run` quick starts point this at the Qdrant container for you |
| `QDRANT_COLLECTION` | `knowledge_base` | Collection name — only matters if one Qdrant instance serves more than one deployment |
| `EMBEDDING_MODEL` | `intfloat/multilingual-e5-base` | Sentence embedding model (restart to change) |
| `RERANKER_MODEL` | `BAAI/bge-reranker-v2-m3` | Cross-encoder reranker model — only loaded if the reranker is turned on in Settings → Search (restart to change) |
| `ADMIN_PASSWORD` | — | Password for the admin panel — change this before going live |
| `SESSION_SECRET` | — | Signs session cookies — generate a random value, don't reuse it |
| `SESSION_COOKIE_NAME` | `session` | Only matters if you run more than one instance on the same domain |
| `SESSION_COOKIE_PATH` | `/kb-agent` | Cookie path — set to `/` if you're not running behind a reverse proxy that mounts the app under a subpath |
| `SESSION_COOKIE_HTTPS_ONLY` | `1` | Set to `0` for a plain-HTTP local/dev setup — a browser silently drops HTTPS-only cookies over HTTP |
| `ENCRYPTION_KEY` | *(empty)* | Optional key to encrypt stored API keys/tokens at rest |
| `DOMAIN` | `:80` | Docker Compose only — site address for the Caddy service (real domain = automatic HTTPS) |

<a id="project-layout"></a>
### 📁 Project layout

```
rag-agent/
├── Dockerfile
├── docker-compose.yml      # app + Caddy, one command, automatic HTTPS
├── .env.example            # infrastructure only — no API keys
├── docs/
│   └── architecture.svg   # the diagram above
├── deploy/
│   └── rag-agent.service # systemd unit, for a non-Docker setup
├── caddy/
│   └── Caddyfile           # used by docker-compose.yml
└── app/                    # the FastAPI service
    ├── main.py             # entrypoint
    ├── config.py           # infra settings, read from .env
    ├── settings_store.py   # API keys & tunables, entered in the admin UI
    ├── admin.py            # admin panel: upload, search, settings, analytics
    ├── llm.py              # picks the AI provider, falls back if it doesn't answer
    ├── img_markers.py      # shared [изображение] URL marker parsing (Telegram/WhatsApp)
    ├── webhook.py    / omnidesk_webhook.py / telegram_webhook.py / whatsapp_webhook.py  # inbound integrations
    ├── pachca.py     / omnidesk.py         / telegram_client.py  / whatsapp_client.py   # outbound integration clients
    ├── claude_client.py / openai_client.py / gigachat_client.py  # AI providers
    ├── export.py            # Excel report (admin: /admin/export.xlsx)
    ├── charts.py            # inline SVG chart geometry for the Analytics dashboard
    ├── rag.py                # search -> hybrid RRF fusion -> optional reranker -> LLM
    ├── router.py             # classifies each question into FAST / AGGREGATION / DEEP
    ├── deep.py               # DEEP pipeline: decompose -> search -> plan -> per-section -> merge
    ├── reranker.py           # cross-encoder reranking (optional, see Settings → Search)
    ├── ingest.py / embeddings.py / vectorstore.py / db.py
    └── templates/           # admin UI pages
```

<a id="security-note"></a>
### 🔒 A note on security

This is meant to run on a server you control, behind HTTPS. Set a real `ADMIN_PASSWORD` and a random `SESSION_SECRET` before exposing it to the internet, and set `ENCRYPTION_KEY` if you want the stored provider keys encrypted at rest rather than in plain SQLite.

---

<a id="русский"></a>
## Русский

> Если честно — я просто устал смотреть, как саппорт даёт три разных ответа на один и тот же вопрос. И дело не в том, что кто-то ленился — правильный ответ реально лежал в каком-то PDF-е, который никто не открывал с момента, как его написали. Вот и сделал то, чего самому не хватало: скармливаешь агенту документы, и вместо папки, которую надо *перерывать*, получаешь то, у чего можно просто... взять и спросить.

<figure>
<img src="docs/architecture.svg" alt="Диаграмма: каналы отправляют вопрос или документ в FastAPI-сервис. Документы режутся на куски и индексируются в Qdrant (векторы) и SQLite FTS5 (полнотекстовый поиск). Вопрос сначала классифицируется роутером на FAST, AGGREGATION или DEEP: FAST ищется в обоих хранилищах сразу, результаты объединяются RRF-фьюжном и уточняются опциональным кросс-энкодер реранкером; AGGREGATION идёт в обход каталога или полного текста базы; DEEP раскладывается на подвопросы, ищет факты по каждому, строит план и генерирует ответ по разделам. Все три пути передаются в LLM с автоматическим фоллбэком между провайдерами, и ответ возвращается в тот канал, откуда пришёл вопрос.">
<figcaption align="center"><sub>Как вопрос становится ответом — роутер выбирает FAST (гибридный поиск + RRF + реранкер), AGGREGATION (каталог/полный обход) или DEEP (декомпозиция → план → генерация по разделам), и уже потом ответ уходит в выбранного LLM-провайдера.</sub></figcaption>
</figure>

<details open>
<summary><b>📕 Оглавление</b></summary>

- 🎯 [Кому это пригодится](#komu-eto-prigoditsya)
- 🌟 [Что умеет](#chto-umeet)
- 🔎 [Технически](#tehnicheski)
- 🚀 [Быстрый старт — Docker Compose + HTTPS](#quickstart-docker-caddy-ru)
- 🐳 [Быстрый старт — Docker, без Caddy](#quickstart-docker-no-caddy-ru)
- 🛠️ [Быстрый старт — без Docker](#quickstart-no-docker-ru)
- 🔌 [Подключение мессенджеров](#подключение-мессенджеров)
- ⚙️ [Конфигурация](#konfiguratsiya)
- 📁 [Структура проекта](#struktura-proekta)
- 🔒 [О безопасности](#o-bezopasnosti)

</details>

<a id="komu-eto-prigoditsya"></a>
### 🎯 Кому это пригодится

Быть «компанией» для этого не обязательно.

- **Малому и среднему бизнесу** — чтобы клиент получал один и тот же правильный ответ что в три дня, что в три ночи, и не пришлось нанимать человека, единственная работа которого — следить, чтобы FAQ не устарел.
- **Фрилансерам, консультантам, соло-основателям** — клиент получает ответ прямо сейчас, а не ждёт до понедельника, пока вы доберётесь до своих заметок.
- **Обычным людям, которые автоматизируют свой кусок жизни** — курс, который ведёте, сообщество, которым рулите, документация к пет-проекту, семейный бизнес, где весь «процесс» на самом деле живёт в голове одного человека и больше нигде.

Соберите то, что знаете, в файлы. Дальше с этим можно разговаривать: в админке, в Telegram, в WhatsApp — где угодно, лишь бы вопрос дошёл до агента.

Один FastAPI-сервис, одна админка. Никакого зоопарка микросервисов, никакой отдельной базы, за которой нужно следить.

<a id="chto-umeet"></a>
### 🌟 Что умеет

**📚 База знаний** — скучная, но важная часть
- Загружайте PDF, Word/Excel, YAML, обычный текст или картинки. Сканы и фото досок распознаются сами через OCR (русский + английский) — да, то самое фото доски с телефона тоже сработает.
- Нет файла под рукой — просто вставьте ссылку или кусок текста, и сразу же приложите пару скриншотов, если одним текстом не обойтись.
- Редактирование, замена, удаление, массовые операции, и приоритет для важных файлов, чтобы агент в первую очередь опирался на них.
- Два режима поиска: быстрый — по коротким автоописаниям, и медленный, дотошный — когда «примерно так» не устраивает.
- Переиндексация целиком или частично (эмбеддинги, каталог, полнотекстовый поиск) при любых правках — без повторной загрузки файлов.
- Тестовая консоль прямо в админке — можно самим погонять агента вопросами, прежде чем это сделает настоящий клиент.

**🧠 Мозги** — выбираете провайдера, переключаете когда захотите, без передеплоя:
- Anthropic Claude
- OpenAI (GPT)
- DeepSeek
- GigaChat (Сбер) — с их OAuth-плясками и российским корневым сертификатом уже разобрались за вас
- Автоматический фоллбэк: если выбранный провайдер не ответил (нет ключа, упал лимит, у него просто плохой день) — агент тихо пробует следующий провайдер, у которого ключ *есть*, вместо того чтобы просто показать пользователю ошибку.

**💬 Где может общаться**
- **Пачка** — вебхук на вход, подпись проверяется, отвечает от имени бота.
- **Omnidesk** — та же идея через их вебхук и HTTP Basic API: подхватывает обращения, отвечает прямо в тикете.
- **Telegram** — обычный вебхук Bot API. Написали боту — получили ответ из базы знаний, а скриншоты из базы приходят настоящими фото, а не голыми ссылками.
- **WhatsApp** — официальный Cloud API (Meta Business Platform). Та же идея: сообщение на вход, ответ (и нужный скриншот) на выход.

Telegram и WhatsApp тут новенькие, поэтому пока попроще: только текстовые вопросы, без памяти диалога (каждое сообщение обрабатывается само по себе — раньше память была, но с ней агент зацикливался на старых вопросах, поэтому сейчас она намеренно выключена), без сбора оценок и без проверки подписи вебхука (что это значит на практике — см. [«Подключение мессенджеров»](#подключение-мессенджеров)). У Пачки и Omnidesk опыта побольше: память треда, оценки ответов, список разрешённых IP, всё как надо. Дотянем Telegram и WhatsApp до того же уровня, если станет понятно, что это того стоит.

**🛡️ Не требует особого присмотра**
- Админ-панель с паролем и своей сессией — ни от кого чужого логина не зависит.
- Ключи и токены хранятся зашифрованными в собственном хранилище настроек, а не валяются в конфиге открытым текстом.
- Один раздел «Аналитика», две вкладки: **«Оценки ответов»** (кто спрашивает, как оценивают ответы, график распределения оценок, тренд вопросов за 30 дней) и **«Темы и пробелы»** (о чём спрашивают чаще всего, график топ-тем, и темы, на которые агент ответил вообще без опоры на базу — помечаются как «пробел»). Без сторонних графических библиотек — графики это inline SVG, которые сервер рисует сам. Дополнили базу — отметили пробел исправленным одним кликом; появится снова, только если по этой теме опять ответят без источника.
- Excel-отчёт в один клик (`.xlsx`, с форматированием и фильтрами) — обзор со встроенным дайджестом за последние 7 дней, лист с проблемными ответами (низкая оценка или ответ вообще без опоры на базу) и полный лог вопросов, одним файлом для тех, кому таблицы понятнее дашбордов.
- Логи, место на диске, очистка, и кнопка «стереть всё» для тестовых окружений, которым нужен чистый старт.

<a id="tehnicheski"></a>
### 🔎 Технически

**Роутер** — каждый вопрос сначала классифицируется, и уже потом уходит в один из трёх конвейеров:
- **FAST** — обычный самодостаточный вопрос. Идёт в гибридный поиск + реранкер, описанные ниже. Подавляющее большинство вопросов — сюда.
- **AGGREGATION** — ответ размазан по многим файлам («какие есть способы оплаты», «перечисли все интеграции») — уходит в каталог/полный обход, см. раздел «Поиск» ниже.
- **DEEP** — сложный многосоставной вопрос или явный запрос на ТЗ/план («составь ТЗ на интеграцию с X», «сравни варианты Y и Z и предложи архитектуру»). Работает по своему конвейеру: разбить вопрос на подвопросы → найти факты по каждому независимо → собрать план (структуру разделов) из найденного → сгенерировать каждый раздел отдельным вызовом модели, опираясь на собранные факты → свести черновики разделов в один связный ответ. На порядок больше вызовов модели, чем FAST (ближе к десятку, чем к одному), поэтому включается только там, где реально нужна такая глубина — обычный вопрос сюда не попадёт.

Классификация — короткий вызов модели, который читает СМЫСЛ вопроса, а не сверяет его с ключевыми словами: регекс-предфильтр всё ещё ловит очевидные агрегационные формулировки (экономит лишний вызов), но различить FAST и DEEP по ключевым словам в принципе нельзя — «объясни, как работает X» и «распиши подробное ТЗ по X, Y и Z со всеми нюансами» отличаются только смыслом. Переопределяется вручную в Настройки → Поиск (**Авто** / **Быстрый** / **Глубокий анализ**) — «Быстрый» и «Глубокий анализ» форсируют соответствующий конвейер для ЛЮБОГО вопроса, минуя классификатор (и его лишний вызов модели) полностью.

**Поиск** — как FAST-вопрос находит нужный кусок текста:
- **Гибридный поиск** — векторный поиск (смысловая близость) и лексический BM25-поиск (SQLite FTS5) работают независимо друг от друга, а потом объединяются через **Reciprocal Rank Fusion**. Это не мелочь: кусок с точным совпадением — код ошибки, номер версии, название модели — может ранжироваться низко по смыслу и вообще не попасть в топ вектора. Объединение двух независимых списков результатов означает, что сильное лексическое совпадение всплывёт, даже если эмбеддинг сам по себе его бы никогда не нашёл.
- **Кросс-энкодер реранкер** (опционально, по умолчанию выключен) — вектор и BM25 оценивают кусок относительно вопроса каждый по отдельности; реранкер читает вопрос и кусок ВМЕСТЕ, за один проход — ранжирует точнее, но ценой прохода модели на процессоре на каждого кандидата. Работает только над узким пулом, который уже отобрал гибридный поиск, не над всей базой. Включается во вкладке Настройки → Поиск.
- Буст приоритетных файлов (★) и фильтр «не путать похожие темы» — например, чтобы вопрос про возврат в одной кассовой системе не собрал ответ из документации другой кассы.

> [!WARNING]
> Реранкер считает только на CPU, без батчинга, и оценивает весь пул гибридного поиска (`top_k × 3` и больше) на каждый вопрос. Одна только `BAAI/bge-reranker-v2-m3` резидентно держит в памяти около гигабайта, а прогон нескольких десятков кусков реального размера на маленьком/общем сервере может занять больше минуты — дольше таймаута большинства реверс-прокси. Нормально на машине с несколькими выделенными ядрами и запасом 4+ ГБ RAM; на тесном VPS лучше оставить выключенным.

**Всё остальное:**

| Слой | Что там работает |
|---|---|
| Веб / API | FastAPI, Uvicorn, серверный рендеринг админки на Jinja2 (без отдельной сборки фронтенда) |
| Векторный поиск | Qdrant — отдельный процесс (в Docker Compose поднимается как сайдкар; при запуске без Docker ожидается уже запущенным) |
| Метаданные, полнотекстовый поиск, настройки | SQLite — реестр файлов, лог вопросов, FTS5 для лексического поиска, зашифрованное хранилище ключей |
| Эмбеддинги и реранкинг | `sentence-transformers` на CPU — `intfloat/multilingual-e5-base` для эмбеддингов, `BAAI/bge-reranker-v2-m3` для реранкинга (обе меняются только перезапуском) |
| LLM-провайдеры | Anthropic Claude, OpenAI, DeepSeek, GigaChat (Сбер) — выбираются на лету, с автоматическим фоллбэком, если у основного нет ключа или он ответил ошибкой |
| Разбор документов | `pypdf`, `python-docx`, `openpyxl`, PyYAML, Pillow + Tesseract OCR (русский и английский) |
| Отчёты | `openpyxl` — оформленный `.xlsx`-экспорт с фильтрами прямо из админки |
| Секреты | `cryptography` (Fernet) — шифрует хранимые ключи/токены провайдеров, если задан `ENCRYPTION_KEY` |
| Деплой | Docker + Docker Compose, Caddy для автоматического HTTPS, либо обычный systemd без Docker |

Ничего экзотического — стек собран так, чтобы для запуска не нужна была отдельная DevOps-команда.

<a id="quickstart-docker-caddy-ru"></a>
### 🚀 Быстрый старт (Docker Compose + HTTPS)

Самый простой способ поднять это на реальном сервере — Caddy спереди, HTTPS сам собой, одна команда:

```bash
git clone https://github.com/Krauzersan/rag-agent.git
cd rag-agent
cp .env.example .env      # поменяйте как минимум ADMIN_PASSWORD и DOMAIN

docker compose up -d --build
```

Укажите в `.env` в `DOMAIN` настоящий домен (`kb.example.com`), направленный на этот сервер, — и Caddy сам получит сертификат. Для локального/тестового запуска оставьте `:80` — будет обычный HTTP. Контейнер приложения наружу не торчит — весь трафик идёт только через Caddy. Откройте `https://ваш-домен/` и войдите по `ADMIN_PASSWORD`.

<a id="quickstart-docker-no-caddy-ru"></a>
### 🐳 Быстрый старт (Docker, без Caddy)

Хотите сами разобраться с TLS или запускаете за уже существующим реверс-прокси? Команда ниже поднимает Qdrant рядом с приложением в общей Docker-сети.

> [!TIP]
> Векторному поиску нужен доступный по `QDRANT_URL` инстанс Qdrant — `docker run` для него ниже не опционален, даже в варианте «без Caddy».

```bash
git clone https://github.com/Krauzersan/rag-agent.git
cd rag-agent
cp .env.example .env      # обязательно поменяйте ADMIN_PASSWORD

docker network create rag-agent-net
docker run -d --name rag-agent-qdrant --network rag-agent-net -v rag-agent-qdrant:/qdrant/storage qdrant/qdrant:latest

docker build -t rag-agent .
docker run -d \
  --name rag-agent \
  --network rag-agent-net \
  --env-file .env \
  -e QDRANT_URL=http://rag-agent-qdrant:6333 \
  -p 8746:8746 \
  -v rag-agent-data:/data \
  rag-agent
```

Откройте `http://localhost:8746/` и войдите по паролю, который задали. В обоих случаях ключи AI-провайдеров и токены интеграций вводятся позже, во вкладке «Настройки» в админке — в `.env` они не хранятся никогда.

<a id="quickstart-no-docker-ru"></a>
### 🛠️ Быстрый старт (без Docker)

Если хотите запускать напрямую на Linux-сервере через systemd. Понадобится ещё и запущенный Qdrant — см. [его собственный быстрый старт](https://qdrant.tech/documentation/quickstart/) — `QDRANT_URL` в `.env` по умолчанию `http://127.0.0.1:6333`, так что проще всего запустить его на этой же машине:

```bash
python3 -m venv venv
venv/bin/pip install torch==2.5.1 --index-url https://download.pytorch.org/whl/cpu
venv/bin/pip install -r app/requirements.txt

cp .env.example .env      # поменяйте ADMIN_PASSWORD, сгенерируйте SESSION_SECRET

venv/bin/uvicorn main:app --app-dir app --host 0.0.0.0 --port 8746
```

Либо пропустите ручные шаги и запустите `sudo bash deploy/install.sh` из корня репозитория — он поставит всё вышеперечисленное как systemd-сервис (сам Qdrant install.sh не ставит — разверните его отдельно заранее).

<a id="подключение-мессенджеров"></a>
### 🔌 Подключение мессенджеров

> [!CAUTION]
> Обе интеграции намеренно базовые: текст вопроса на входе, текст ответа на выходе, без проверки подписи вебхука. Для закрытого сервера или бота с небольшим трафиком этого достаточно; если эндпоинт будет смотреть в открытый интернет и получать реальный трафик — добавьте проверку заголовка `secret_token` у Telegram или `X-Hub-Signature-256` у WhatsApp, прежде чем полагаться на это всерьёз (оба эндпоинта — короткие файлы на одну задачу: `app/telegram_webhook.py` и `app/whatsapp_webhook.py`, дополнить несложно).

В обеих есть список разрешённых пользователей — задаётся там же, во вкладке «Настройки». Пусто — отвечает всем, кто напишет. Заполнено (username/id для Telegram, номера для WhatsApp) — остальным вместо ответа приходит вежливый отказ.

**Telegram**

1. Напишите [@BotFather](https://t.me/BotFather), выполните `/newbot`, заберите токен.
2. Вставьте его в админке: **Настройки → Telegram**, сохраните. И всё — сервис сам зарегистрирует вебхук в Telegram (для этого на вкладке «Поиск» должен быть заполнен «Публичный адрес сервиса»; если нет — страница настроек так и скажет).
3. Напишите боту — должен ответить по базе знаний.

**WhatsApp** (официальный Cloud API, через [Meta for Developers](https://developers.facebook.com))

1. Создайте приложение, добавьте продукт **WhatsApp**, возьмите тестовый номер (или подключите свой верифицированный) — получите Phone Number ID и временный токен; постоянный токен генерируется позже в System Users, перед запуском в прод.
2. В админке: **Настройки → WhatsApp**, вставьте access-токен и Phone Number ID, в поле verify-токена — любая произвольная строка на ваш выбор: Meta просто вернёт её же, чтобы подтвердить, что вы владеете эндпоинтом. Сохраните.
3. Этот шаг API от Meta не предусматривает, так что он единственный ручной: в настройках приложения (WhatsApp → Configuration) укажите адрес вебхука `https://ваш-домен.ru/webhook/whatsapp`, тот же verify-токен, подпишитесь на событие **messages**. Meta сначала сделает GET-запрос — он пройдёт, только если verify-токен совпадёт.
4. Напишите на тестовый номер в WhatsApp — должен ответить по базе знаний.

<a id="konfiguratsiya"></a>
### ⚙️ Конфигурация

Всё, что в `.env` — исключительно инфраструктура: порт, где хранить данные, пароль админки. Все API-ключи и токены (Claude/OpenAI/DeepSeek/GigaChat, Пачка, Omnidesk, Telegram, WhatsApp) настраиваются из вкладки «Настройки» в админке и хранятся зашифрованными — ни один из них не лежит в `.env`.

| Переменная | По умолчанию | Назначение |
|---|---|---|
| `PORT` | `8746` | Порт, на котором слушает сервис |
| `DATA_DIR` | `./data` | Где лежат база знаний, БД, векторный индекс и кэш модели |
| `QDRANT_URL` | `http://127.0.0.1:6333` | Где искать Qdrant — быстрые старты через Docker Compose / `docker run` уже указывают сюда адрес контейнера с Qdrant |
| `QDRANT_COLLECTION` | `knowledge_base` | Имя коллекции — важно, только если один Qdrant обслуживает больше одного деплоя |
| `EMBEDDING_MODEL` | `intfloat/multilingual-e5-base` | Модель эмбеддингов (меняется только перезапуском) |
| `RERANKER_MODEL` | `BAAI/bge-reranker-v2-m3` | Модель кросс-энкодер реранкера — загружается, только если реранкер включён в Настройки → Поиск (меняется только перезапуском) |
| `ADMIN_PASSWORD` | — | Пароль админ-панели — поменяйте перед запуском в прод |
| `SESSION_SECRET` | — | Подписывает сессионные куки — сгенерируйте случайное значение |
| `SESSION_COOKIE_NAME` | `session` | Важно только если на одном домене крутится несколько копий |
| `SESSION_COOKIE_PATH` | `/kb-agent` | Путь куки — поставьте `/`, если сервис не стоит за реверс-прокси, монтирующим его в подпуть |
| `SESSION_COOKIE_HTTPS_ONLY` | `1` | Поставьте `0` для локального запуска по обычному HTTP — иначе браузер тихо не сохранит куку без HTTPS |
| `ENCRYPTION_KEY` | *(пусто)* | Опциональный ключ для шифрования хранимых ключей/токенов |
| `DOMAIN` | `:80` | Только для Docker Compose — адрес сайта для сервиса Caddy (настоящий домен = HTTPS автоматически) |

<a id="struktura-proekta"></a>
### 📁 Структура проекта

```
rag-agent/
├── Dockerfile
├── docker-compose.yml      # приложение + Caddy, одна команда, HTTPS сам собой
├── .env.example            # только инфраструктура — без API-ключей
├── docs/
│   └── architecture.svg   # диаграмма выше
├── deploy/
│   └── rag-agent.service # systemd-юнит для запуска без Docker
├── caddy/
│   └── Caddyfile           # используется docker-compose.yml
└── app/                    # сам сервис на FastAPI
    ├── main.py             # точка входа
    ├── config.py           # инфраструктурные настройки из .env
    ├── settings_store.py   # API-ключи и параметры, вводимые в админке
    ├── admin.py            # админка: загрузка, поиск, настройки, аналитика
    ├── llm.py              # выбор AI-провайдера, фоллбэк, если тот не ответил
    ├── img_markers.py      # общий разбор пометок [изображение] URL (Telegram/WhatsApp)
    ├── webhook.py    / omnidesk_webhook.py / telegram_webhook.py / whatsapp_webhook.py  # входящие интеграции
    ├── pachca.py     / omnidesk.py         / telegram_client.py  / whatsapp_client.py   # клиенты для ответов
    ├── claude_client.py / openai_client.py / gigachat_client.py  # AI-провайдеры
    ├── export.py            # Excel-отчёт (админка: /admin/export.xlsx)
    ├── charts.py            # геометрия inline SVG-графиков для дашборда «Аналитика»
    ├── rag.py                # поиск -> гибридный RRF-фьюжн -> опц. реранкер -> LLM
    ├── router.py             # классифицирует вопрос: FAST / AGGREGATION / DEEP
    ├── deep.py               # DEEP-конвейер: декомпозиция -> поиск -> план -> разделы -> сборка
    ├── reranker.py           # кросс-энкодер реранкинг (опционально, см. Настройки → Поиск)
    ├── ingest.py / embeddings.py / vectorstore.py / db.py
    └── templates/           # страницы админки
```

<a id="o-bezopasnosti"></a>
### 🔒 О безопасности

Сервис рассчитан на запуск на своём сервере, за HTTPS. Перед тем как открывать его наружу, задайте настоящий `ADMIN_PASSWORD` и случайный `SESSION_SECRET`, а если хотите, чтобы ключи провайдеров хранились зашифрованными, а не в открытом виде в SQLite — задайте `ENCRYPTION_KEY`.

---

<p align="center">Built by someone who got tired of answering the same question three different ways.<br>Hope it saves you a few afternoons too. · Сделано тем, кто устал отвечать на один вопрос по-разному. Надеюсь, вам это сэкономит пару вечеров.</p>
