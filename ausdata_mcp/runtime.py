"""One runtime/session identity shared by discovery and evidence artifacts."""

import os
import re
import sqlite3
from contextlib import closing
from pathlib import Path
from tempfile import NamedTemporaryFile
from uuid import uuid4

from . import data_config as data_config  # Load optional .env before resolving paths.

RUNTIME_DIR = Path(
    os.getenv("AUSDATA_RUNTIME_DIR") or Path(__file__).resolve().parents[1] / "runtime"
).resolve()
SESSION_ID = str(os.getenv("AUSDATA_SESSION_ID") or f"mcp-{uuid4()}").strip()
if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", SESSION_ID):
    raise ValueError(
        "AUSDATA_SESSION_ID must be 1-128 letters, digits, dots, underscores or hyphens, starting with a letter or digit."
    )
CATALOG_TTL_SECONDS = int(os.getenv("AUSDATA_CATALOG_TTL_SECONDS", "86400"))
if CATALOG_TTL_SECONDS < 1:
    raise ValueError("AUSDATA_CATALOG_TTL_SECONDS must be positive.")


def validate_local_runtime() -> None:
    """Check local prerequisites before accepting MCP requests."""
    data_config.get_data_settings()
    try:
        with closing(sqlite3.connect(":memory:")) as connection:
            connection.execute("CREATE VIRTUAL TABLE startup_probe USING fts5(value)")
    except sqlite3.Error as exc:
        raise RuntimeError("SQLite FTS5 support is required for catalogue search.") from exc

    session = RUNTIME_DIR / "sessions" / SESSION_ID
    for directory in (session / "catalogue", session / "artifacts"):
        try:
            directory.mkdir(parents=True, exist_ok=True)
            with NamedTemporaryFile(mode="w", prefix=".startup-", dir=directory) as probe:
                probe.write("ok")
                probe.flush()
        except OSError as exc:
            raise RuntimeError(
                f"Cannot write MCP session directory {directory}: {exc}. "
                "Set AUSDATA_RUNTIME_DIR to a writable directory."
            ) from exc
