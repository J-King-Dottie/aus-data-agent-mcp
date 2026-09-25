"""One runtime/session identity shared by discovery and evidence artifacts."""

import os
import re
from pathlib import Path
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
