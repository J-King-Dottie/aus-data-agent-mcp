#!/bin/bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PORT="${BACKEND_PORT:-5000}"
SKIP_INSTALL="${SKIP_INSTALL:-0}"
VENV_DIR="$ROOT_DIR/.venv-wsl"
VENV_ACTIVATE="$VENV_DIR/bin/activate"

cd "$ROOT_DIR"

if [ ! -f "$VENV_ACTIVATE" ]; then
  echo "Creating WSL virtualenv at .venv-wsl"
  python3 -m venv "$VENV_DIR"
fi

# shellcheck disable=SC1090
source "$VENV_ACTIVATE"

if [ "$SKIP_INSTALL" != "1" ]; then
  python3 -m pip install -r backend/requirements.txt
fi

echo "Starting backend on http://127.0.0.1:$PORT"
echo "Uvicorn will reload backend code changes."

uvicorn backend.app.main:app --host 127.0.0.1 --port "$PORT" --reload --reload-dir backend
