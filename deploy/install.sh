#!/usr/bin/env bash
# Установка и запуск KB Agent без Docker (host-режим).
# Запускать на сервере из папки проекта:  sudo bash deploy/install.sh
set -euo pipefail

APP_DIR=/opt/kb-agent
VENV="$APP_DIR/venv"

echo "==> [1/5] Системные пакеты (Python, Tesseract OCR rus+eng)"
apt-get update
apt-get install -y --no-install-recommends \
    python3 python3-venv python3-pip \
    tesseract-ocr tesseract-ocr-rus tesseract-ocr-eng \
    libgomp1 openssl

echo "==> [2/5] Виртуальное окружение Python"
python3 -m venv "$VENV"
"$VENV/bin/pip" install --upgrade pip wheel

echo "==> [3/5] Зависимости (torch CPU + остальное). Это самый долгий шаг."
"$VENV/bin/pip" install torch==2.5.1 --index-url https://download.pytorch.org/whl/cpu
"$VENV/bin/pip" install -r "$APP_DIR/app/requirements.txt"

echo "==> [4/5] Файл .env"
if [ ! -f "$APP_DIR/.env" ]; then
    cp "$APP_DIR/.env.example" "$APP_DIR/.env"
    SECRET="$(openssl rand -hex 32)"
    sed -i "s|^SESSION_SECRET=.*|SESSION_SECRET=${SECRET}|" "$APP_DIR/.env"
    echo "    Создан $APP_DIR/.env (SESSION_SECRET сгенерирован автоматически)."
    echo "    !!! Поменяйте ADMIN_PASSWORD в этом файле."
else
    echo "    $APP_DIR/.env уже существует — не трогаю."
fi
mkdir -p "$APP_DIR/data"

echo "==> [5/5] systemd-сервис kb-agent"
cp "$APP_DIR/deploy/kb-agent.service" /etc/systemd/system/kb-agent.service
systemctl daemon-reload
systemctl enable kb-agent
systemctl restart kb-agent

echo
echo "Готово. Сервис запущен на порту 8745."
echo "Логи:        journalctl -u kb-agent -f"
echo "Статус:      systemctl status kb-agent"
echo "Проверка:    curl http://localhost:8745/health"
