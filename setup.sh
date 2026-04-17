#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# --- 仮想環境 ---
if [ ! -d "${SCRIPT_DIR}/venv" ]; then
  echo "[setup] Creating virtual environment..."
  python3 -m venv "${SCRIPT_DIR}/venv"
fi

echo "[setup] Installing dependencies..."
"${SCRIPT_DIR}/venv/bin/pip" install --upgrade pip -q
"${SCRIPT_DIR}/venv/bin/pip" install -r "${SCRIPT_DIR}/requirements.txt" -q
echo "[setup] Dependencies installed."

# --- .env ---
if [ ! -f "${SCRIPT_DIR}/.env" ]; then
  cp "${SCRIPT_DIR}/.env.example" "${SCRIPT_DIR}/.env"
  echo ""
  echo "[setup] .env created from .env.example"
  echo "        -> Set OPENROUTER_API_KEY in .env before starting the server."
  echo ""
  exit 0
fi

# --- サーバー起動 ---
HOST="${SERVER_HOST:-127.0.0.1}"
PORT="${SERVER_PORT:-4141}"

echo "[setup] Starting proxy on http://${HOST}:${PORT} ..."
exec "${SCRIPT_DIR}/venv/bin/uvicorn" main:app \
  --host "${HOST}" \
  --port "${PORT}" \
  --reload \
  --app-dir "${SCRIPT_DIR}"
