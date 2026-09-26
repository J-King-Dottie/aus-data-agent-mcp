"""Launch from any working directory using an absolute path to this file."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ausdata_mcp.runtime import validate_local_runtime
from ausdata_mcp.server import server

if __name__ == "__main__":
    validate_local_runtime()
    server.run(transport="stdio")
