# KB Agent — веб-сервис AI-агента базы знаний (host-режим, без Docker)

Веб-сервис на одном Linux-сервере: **админ-панель** (загрузка/удаление файлов базы
знаний + ввод API-ключей), **RAG-поиск**, интеграция с **Claude** и **ботом «Пачки»**.

Особенности этой версии:

- **Без Docker** — запускается напрямую (Python venv + systemd). Проще патчить: поправил
  файл → `systemctl restart kb-agent`.
- **API-ключи вводятся в интерфейсе** (вкладка «Настройки»), а не в конфиге. В `.env`
  только инфраструктура (порт, пароль панели).
- **Встроенный Qdrant** — векторная база лежит файлами на диске, отдельный сервис не нужен.
- **Порт 8745** — заведомо свободный (80/443 у вас заняты).

---

## Структура

```
kb-agent/
├── .env.example            # инфраструктура (порт, пароль панели) — БЕЗ api-ключей
├── deploy/
│   ├── install.sh          # установка одним скриптом (venv, зависимости, systemd)
│   └── kb-agent.service    # systemd-юнит
└── app/                    # приложение (FastAPI)
    ├── main.py             # точка входа
    ├── config.py           # инфраструктурные настройки из .env
    ├── settings_store.py   # API-ключи и параметры из админ-панели (SQLite)
    ├── admin.py            # панель: вход, загрузка, удаление, тест, НАСТРОЙКИ
    ├── webhook.py          # приём вопросов из «Пачки»
    ├── pachca.py           # отправка ответов в «Пачку»
    ├── rag.py / claude_client.py / ingest.py / embeddings.py / vectorstore.py / db.py
    └── templates/          # HTML-страницы (admin, settings, login)
```

---

## Шаг 1. Снести старый Docker-вариант (если запускали)

На сервере:

```bash
cd /opt/kb-agent
docker compose down -v 2>/dev/null || true
docker system prune -af 2>/dev/null || true
```

Если Docker вообще не запускали — пропустите этот шаг.

## Шаг 2. Залить обновлённый код с Mac

На **Mac** (см. отдельную инструкцию по выгрузке):

```bash
cp -R "/Users/evgeniy/Downloads/AI agent/kb-agent" ~/kb-agent
rsync -avz --delete --exclude '.env' --exclude 'data/' --exclude 'venv/' --exclude '__pycache__/' \
  ~/kb-agent/ root@81.163.28.127:/opt/kb-agent/
```

## Шаг 3. Установка и запуск (одним скриптом)

На **сервере**:

```bash
cd /opt/kb-agent
bash deploy/install.sh
```

Скрипт сам: поставит Python и Tesseract OCR, создаст venv, установит зависимости
(torch CPU + остальное — самый долгий шаг, 5–15 мин), создаст `.env` с авто-сгенерированным
`SESSION_SECRET`, поставит и запустит systemd-сервис на порту **8745**.

После установки задайте пароль панели:

```bash
nano /opt/kb-agent/.env      # поменяйте ADMIN_PASSWORD
systemctl restart kb-agent
```

## Шаг 4. Проверка

```bash
systemctl status kb-agent           # должно быть active (running)
curl http://localhost:8745/health   # {"status":"ok"}
journalctl -u kb-agent -f           # логи (Ctrl+C — выйти)
```

Откройте в браузере **http://81.163.28.127:8745/** и войдите по `ADMIN_PASSWORD`.

## Шаг 5. Ввести ключ Claude и проверить

1. В панели откройте **⚙ Настройки**.
2. Вставьте **API-ключ Claude** (`sk-ant-...`), при желании поменяйте модель. Сохраните.
3. Вернитесь на главную, загрузите пару файлов, дождитесь статуса `ready`.
4. В блоке «Проверить ответ агента» задайте вопрос — должен прийти ответ по вашим файлам.

Токены «Пачки» (бот + signing secret) вводятся там же, в «Настройках», на шаге
подключения бота.

---

## Управление сервисом

```bash
systemctl restart kb-agent     # перезапуск (после обновления кода)
systemctl stop kb-agent        # остановить
systemctl start kb-agent       # запустить
journalctl -u kb-agent -f      # логи
```

После повторной выгрузки кода с Mac (Шаг 2) выполните `systemctl restart kb-agent`.

## Обновить зависимости (если меняли requirements.txt)

```bash
/opt/kb-agent/venv/bin/pip install -r /opt/kb-agent/app/requirements.txt
systemctl restart kb-agent
```

## Порт и доступ

Сервис слушает `0.0.0.0:8745`. Откройте порт в файрволе, если включён:

```bash
ufw allow 8745/tcp
```

> Для подключения бота «Пачки» позже понадобится HTTPS-адрес. Сейчас, для запуска и
> тестов панели, достаточно HTTP по `http://81.163.28.127:8745/`.

---

## Где что хранится

Всё в `/opt/kb-agent/data/`: файлы БЗ (`knowledge_base/`), база `app.db` (учёт файлов +
настройки/ключи), индекс `qdrant_local/`, кэш модели `hf_cache/`. Для бэкапа достаточно
сохранить папку `data/`.

> Ключи лежат в `app.db` в открытом виде — доступ к серверу должен быть ограничен (это
> ваш приватный сервер). При желании позже добавим шифрование.
