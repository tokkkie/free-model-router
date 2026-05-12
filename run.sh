#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# uvの存在チェック
if ! command -v uv >/dev/null 2>&1; then
  echo "uv not found. Please install uv: https://astral.sh/uv/"
  exit 1
fi

# --debug オプションの処理
if [[ "${1:-}" == "--debug" ]]; then
  export LOG_LEVEL=DEBUG
  shift
fi

# --- 依存関係の同期 ---
echo "[setup] Syncing dependencies..."
cd "${SCRIPT_DIR}"
uv sync -q
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

# 既存プロセスを停止
if lsof -ti:${PORT} > /dev/null 2>&1; then
  echo "[setup] Stopping existing process on port ${PORT}..."
  lsof -ti:${PORT} | xargs kill -9 2>/dev/null || true
  sleep 1
fi

echo "[setup] Starting proxy on http://${HOST}:${PORT} ..."
exec uv run uvicorn main:app \
  --host "${HOST}" \
  --port "${PORT}" \
  --reload \
  --app-dir "${SCRIPT_DIR}"
