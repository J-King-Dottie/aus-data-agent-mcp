#!/bin/bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$ROOT_DIR/.venv-wsl"
VENV_ACTIVATE="$VENV_DIR/bin/activate"

if [ ! -f "$VENV_ACTIVATE" ]; then
  echo "Missing .venv-wsl. Run this once first:"
  echo "python3 -m venv .venv-wsl && source .venv-wsl/bin/activate && python3 -m pip install -r backend/requirements.txt"
  exit 1
fi

source "$VENV_ACTIVATE"
uvicorn backend.app.main:app --host 127.0.0.1 --port 5000 --reload --reload-dir "$ROOT_DIR/backend"
