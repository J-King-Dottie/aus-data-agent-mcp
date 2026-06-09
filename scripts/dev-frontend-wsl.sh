#!/bin/bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FRONTEND_DIR="$ROOT_DIR/frontend"
PORT="${FRONTEND_PORT:-3000}"
SKIP_INSTALL="${SKIP_INSTALL:-0}"

cd "$ROOT_DIR"

echo "Starting frontend on http://127.0.0.1:$PORT"
echo "Vite will hot-reload frontend code changes."

if [ -f .env ]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

# Local Vite dev should use the configured /api proxy, matching dottie-ai-studio.
# Keeping VITE_API_BASE_URL set makes browser requests go cross-origin to :5000.
unset VITE_API_BASE_URL

cd "$FRONTEND_DIR"

if [ "$SKIP_INSTALL" != "1" ]; then
  npm install
fi

npm run dev -- --host 127.0.0.1 --port "$PORT"
