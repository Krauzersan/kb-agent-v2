#!/usr/bin/env bash
# Bare-metal install (no Docker) — venv + systemd.
# Run as root from the repo root: sudo bash deploy/install.sh [install-dir]
set -euo pipefail

APP_DIR="${1:-$(pwd)}"
VENV="$APP_DIR/venv"

echo "==> [1/5] System packages (Python, Tesseract OCR rus+eng)"
apt-get update
apt-get install -y --no-install-recommends \
    python3 python3-venv python3-pip \
    tesseract-ocr tesseract-ocr-rus tesseract-ocr-eng \
    libgomp1 openssl

echo "==> [2/5] Python virtual environment"
python3 -m venv "$VENV"
"$VENV/bin/pip" install --upgrade pip wheel

echo "==> [3/5] Dependencies (torch CPU + the rest). This is the slowest step."
"$VENV/bin/pip" install torch==2.5.1 --index-url https://download.pytorch.org/whl/cpu
"$VENV/bin/pip" install -r "$APP_DIR/app/requirements.txt"

echo "==> [4/5] .env file"
if [ ! -f "$APP_DIR/.env" ]; then
    cp "$APP_DIR/.env.example" "$APP_DIR/.env"
    SECRET="$(openssl rand -hex 32)"
    sed -i "s|^SESSION_SECRET=.*|SESSION_SECRET=${SECRET}|" "$APP_DIR/.env"
    echo "    Created $APP_DIR/.env (SESSION_SECRET generated automatically)."
    echo "    !!! Now change ADMIN_PASSWORD in that file."
else
    echo "    $APP_DIR/.env already exists — leaving it alone."
fi
mkdir -p "$APP_DIR/data"

echo "==> [5/5] systemd service"
sed "s|/opt/rag-agent|$APP_DIR|g" "$APP_DIR/deploy/rag-agent.service" > /etc/systemd/system/rag-agent.service
systemctl daemon-reload
systemctl enable rag-agent
systemctl restart rag-agent

echo
echo "Done. Service running on port 8746."
echo "Logs:   journalctl -u rag-agent -f"
echo "Status: systemctl status rag-agent"
echo "Check:  curl http://localhost:8746/health"
