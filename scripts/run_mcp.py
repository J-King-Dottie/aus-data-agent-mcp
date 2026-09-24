"""Launch from any working directory using an absolute path to this file."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ausdata_mcp.server import server

if __name__ == "__main__":
    server.run(transport="stdio")
