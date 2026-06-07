#!/bin/bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCHEMA_FILE="${1:-$ROOT_DIR/supabase_modelling_workspace.sql}"

cd "$ROOT_DIR"

if [ -f .env ]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

if [ ! -f "$SCHEMA_FILE" ]; then
  echo "Schema file not found: $SCHEMA_FILE" >&2
  exit 1
fi

if [ -z "${DATABASE_URL:-}" ]; then
  echo "DATABASE_URL is not set in .env." >&2
  echo "Use the Supabase direct or session-pooler Postgres URI with a password that can run DDL." >&2
  exit 1
fi

echo "Applying schema: $SCHEMA_FILE"

if command -v psql >/dev/null 2>&1; then
  psql "$DATABASE_URL" --set ON_ERROR_STOP=1 --file "$SCHEMA_FILE"
  exit 0
fi

VENV_ACTIVATE="$ROOT_DIR/.venv-wsl/bin/activate"
if [ -f "$VENV_ACTIVATE" ]; then
  # shellcheck disable=SC1090
  source "$VENV_ACTIVATE"
fi

python - "$SCHEMA_FILE" <<'PY'
import os
import sys
from pathlib import Path

try:
    import psycopg
except ImportError as exc:
    raise SystemExit(
        "psql is not installed and Python package psycopg is missing. "
        "Install one of: sudo apt-get install postgresql-client OR "
        "python -m pip install 'psycopg[binary]'"
    ) from exc

schema_path = Path(sys.argv[1])
database_url = os.environ["DATABASE_URL"]

with psycopg.connect(database_url, connect_timeout=20) as conn:
    conn.execute(schema_path.read_text())
    conn.commit()
PY
