#!/bin/bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FRONTEND_DIR="$ROOT_DIR/frontend"
VENV_DIR="$ROOT_DIR/.venv-wsl"
VENV_ACTIVATE="$VENV_DIR/bin/activate"

if [ ! -f "$VENV_ACTIVATE" ]; then
  echo "Creating WSL virtualenv at .venv-wsl"
  python3 -m venv "$VENV_DIR"
  source "$VENV_ACTIVATE"
  python3 -m pip install -r "$ROOT_DIR/backend/requirements.txt"
else
  source "$VENV_ACTIVATE"
fi

cleanup() {
  if [ -n "${VITE_PID:-}" ]; then
    kill "$VITE_PID" 2>/dev/null || true
  fi
}

trap cleanup EXIT INT TERM

echo "Starting frontend on http://127.0.0.1:3000"
(cd "$FRONTEND_DIR" && npm run dev -- --host 127.0.0.1 --port 3000) &
VITE_PID=$!

echo "Starting backend on http://127.0.0.1:5000"
uvicorn backend.app.main:app --host 127.0.0.1 --port 5000 --reload --reload-dir "$ROOT_DIR/backend"
