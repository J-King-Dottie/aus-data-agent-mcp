#!/usr/bin/env python3
"""Fetch live source catalogues, normalize/index them, and report measured timings."""

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ausdata_mcp.unified_catalog import (
    CATALOG_PATH,
    ensure_unified_catalog_artifacts,
    search_unified_catalog,
)


def main():
    start = time.perf_counter()
    payload = ensure_unified_catalog_artifacts(force_refresh=True)
    fresh_seconds = time.perf_counter() - start
    start = time.perf_counter()
    result = search_unified_catalog("GDP per capita", limit=10)
    cached_seconds = time.perf_counter() - start
    print(
        json.dumps(
            {
                "catalogue_path": str(CATALOG_PATH),
                "fresh_seconds": round(fresh_seconds, 3),
                "cached_search_seconds": round(cached_seconds, 4),
                "index_seconds": payload["index_seconds"],
                "entries": len(payload["entries"]),
                "sources": payload["sources"],
                "warnings": result["warnings"],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
