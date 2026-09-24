from __future__ import annotations

import json
import re
import sqlite3
import time
from functools import wraps
from threading import RLock
from functools import lru_cache
from uuid import uuid4
from typing import Any, Dict, List, Optional


from .runtime import RUNTIME_DIR, SESSION_ID
from .catalog_refresh import SCHEMA_VERSION, due_sources, refresh
from .catalog_sources import PROVIDERS

CATALOG_PATH = RUNTIME_DIR / "sessions" / SESSION_ID / "catalogue" / "catalog.json"
FTS_DB_PATH = CATALOG_PATH.with_name("search.sqlite3")
CATALOG_LOCK = RLock()
_payload = None
_payload_path = None


def _locked(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        with CATALOG_LOCK:
            return function(*args, **kwargs)
    return wrapped

STOPWORDS = {
    "the",
    "and",
    "for",
    "with",
    "from",
    "that",
    "this",
    "into",
    "over",
    "under",
    "using",
    "show",
    "data",
    "series",
    "table",
    "tables",
    "latest",
    "time",
    "timeseries",
    "trend",
    "what",
    "which",
    "where",
}


def _clean_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip())


def _normalize_tokens(query: str) -> List[str]:
    raw_tokens = re.findall(r"[A-Za-z0-9]+", str(query or "").lower())
    return [token for token in raw_tokens if len(token) > 1 and token not in STOPWORDS]


def _build_match_query(tokens: List[str], operator: str) -> str:
    if not tokens:
        return ""
    return f" {operator} ".join(f'"{token}"*' for token in tokens)


def _relaxed_match_query(query: str) -> str:
    return _build_match_query(_normalize_tokens(query), "OR")


def _invalidate_caches() -> None:
    global _payload, _payload_path
    _payload = None
    _payload_path = None
    _catalog_entries_by_id.cache_clear()


def build_catalog_index(entries: List[Dict[str, Any]], generation: str = "") -> None:
    FTS_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = FTS_DB_PATH.with_name(f".{FTS_DB_PATH.name}.{uuid4().hex}.tmp")
    conn = sqlite3.connect(temporary)
    try:
        conn.executescript(
            """
            CREATE TABLE catalog (
                dataset_id TEXT PRIMARY KEY,
                provider TEXT NOT NULL,
                title TEXT NOT NULL,
                description TEXT NOT NULL,
                search_text TEXT NOT NULL,
                source_url TEXT NOT NULL,
                requires_metadata_before_retrieval INTEGER NOT NULL
            );

            CREATE VIRTUAL TABLE catalog_fts USING fts5(
                provider,
                dataset_id,
                title,
                description,
                search_text,
                content='catalog',
                content_rowid='rowid'
            );
            """
        )
        conn.executemany(
            """
            INSERT INTO catalog (
                dataset_id,
                provider,
                title,
                description,
                search_text,
                source_url,
                requires_metadata_before_retrieval
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    entry["datasetId"],
                    entry["provider"],
                    entry["title"],
                    entry["description"],
                    entry["searchText"],
                    entry["sourceUrl"],
                    1 if entry["requiresMetadataBeforeRetrieval"] else 0,
                )
                for entry in entries
            ],
        )
        conn.execute(
            """
            INSERT INTO catalog_fts(rowid, provider, dataset_id, title, description, search_text)
            SELECT rowid, provider, dataset_id, title, description, search_text
            FROM catalog
            """
        )
        conn.execute("CREATE TABLE generation (id TEXT NOT NULL)")
        conn.execute("INSERT INTO generation VALUES (?)", (generation,))
        conn.commit()
        conn.close()
        temporary.replace(FTS_DB_PATH)
    finally:
        conn.close()
        temporary.unlink(missing_ok=True)



@_locked
def ensure_unified_catalog_artifacts(force_refresh: bool = False) -> Dict[str, Any]:
    global _payload, _payload_path
    if _payload is None or _payload_path != CATALOG_PATH:
        try:
            previous = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
            if previous.get("schema_version") != SCHEMA_VERSION or not isinstance(previous.get("entries"), list):
                previous = {}
        except (OSError, ValueError, AttributeError):
            previous = {}
        _payload, _payload_path = previous, CATALOG_PATH
    if due_sources(_payload, force_refresh):
        started = time.perf_counter()
        updated = refresh(_payload, force=force_refresh)
        indexing = time.perf_counter()
        build_catalog_index(updated["entries"], updated["generation"])
        updated["index_seconds"] = round(time.perf_counter() - indexing, 3)
        updated["refresh_seconds"] = round(time.perf_counter() - started, 3)
        temporary = CATALOG_PATH.with_name(f".{CATALOG_PATH.name}.{uuid4().hex}.tmp")
        try:
            temporary.write_text(json.dumps(updated, ensure_ascii=False), encoding="utf-8")
            temporary.replace(CATALOG_PATH)
        finally:
            temporary.unlink(missing_ok=True)
        _invalidate_caches()
        _payload, _payload_path = updated, CATALOG_PATH
    # A crash between the two atomic file replacements, a missing index, or an
    # interrupted copy must never pair one catalogue with another generation's FTS.
    try:
        with sqlite3.connect(f"file:{FTS_DB_PATH}?mode=ro", uri=True) as conn:
            generation = conn.execute("SELECT id FROM generation").fetchone()[0]
    except (sqlite3.Error, TypeError):
        generation = None
    if generation != _payload.get("generation"):
        build_catalog_index(_payload["entries"], _payload["generation"])
    return _payload


@lru_cache(maxsize=1)
def _catalog_entries_by_id() -> Dict[str, Dict[str, Any]]:
    entries = ensure_unified_catalog_artifacts(False)["entries"]
    return {entry["datasetId"]: entry for entry in entries}


@_locked
def get_unified_catalog_entry(dataset_id: str) -> Optional[Dict[str, Any]]:
    ensure_unified_catalog_artifacts(False)
    return _catalog_entries_by_id().get(_clean_text(dataset_id))


def _row_to_entry(row: sqlite3.Row) -> Dict[str, Any]:
    return {
        "datasetId": row["dataset_id"],
        "provider": row["provider"],
        "title": row["title"],
        "description": str(row["description"] or "")[:500],
        "sourceUrl": row["source_url"],
        "requiresMetadataBeforeRetrieval": bool(row["requires_metadata_before_retrieval"]),
    }


def _matching_count(connection: sqlite3.Connection, match_query: str, provider: str) -> int:
    if not match_query:
        return connection.execute(
            "SELECT COUNT(*) FROM catalog WHERE (? = '' OR provider = ? COLLATE NOCASE)",
            (provider, provider),
        ).fetchone()[0]
    return connection.execute(
        """
        SELECT COUNT(*) FROM catalog_fts f JOIN catalog c ON c.rowid = f.rowid
        WHERE catalog_fts MATCH ? AND (? = '' OR c.provider = ? COLLATE NOCASE)
        """,
        (match_query, provider, provider),
    ).fetchone()[0]


def _matching_rows(connection: sqlite3.Connection, match_query: str, provider: str, limit: int, offset: int) -> List[sqlite3.Row]:
    if not match_query:
        return connection.execute(
            """
            SELECT provider, dataset_id, title, description, source_url, requires_metadata_before_retrieval
            FROM catalog
            WHERE (? = '' OR provider = ? COLLATE NOCASE)
            ORDER BY provider, title, dataset_id
            LIMIT ? OFFSET ?
            """,
            (provider, provider, limit, offset),
        ).fetchall()
    return connection.execute(
        """
        SELECT
            c.provider,
            c.dataset_id,
            c.title,
            c.description,
            c.source_url,
            c.requires_metadata_before_retrieval
        FROM catalog_fts f
        JOIN catalog c ON c.rowid = f.rowid
        WHERE catalog_fts MATCH ? AND (? = '' OR c.provider = ? COLLATE NOCASE)
        ORDER BY bm25(catalog_fts, 3.0, 4.0, 5.0, 2.5, 1.8), c.provider, c.dataset_id
        LIMIT ? OFFSET ?
        """,
        (match_query, provider, provider, limit, offset),
    ).fetchall()


@_locked
def search_unified_catalog(query: str, limit: int = 50, *, offset: int = 0, force_refresh: bool = False, provider: str = "") -> Dict[str, Any]:
    payload = ensure_unified_catalog_artifacts(force_refresh)
    aliases = {"rba": PROVIDERS[5], "dcceew": PROVIDERS[6], "worldbank": "World Bank", "comtrade": "UN Comtrade",
               **{alias: "Pacific Data Hub" for alias in ("pdh", "spc", "pacific", "pdh.stat")},
               **{name.lower(): name for name in PROVIDERS}}
    provider = aliases.get(provider.strip().lower(), provider.strip())
    if provider and provider not in PROVIDERS:
        raise ValueError("Unknown provider. Use: " + ", ".join(PROVIDERS))
    if provider and payload["sources"][provider]["status"] == "unavailable":
        raise RuntimeError(f"{provider} catalogue unavailable: {payload['sources'][provider]['error']}")
    warnings = [f"{name}: {status['status']} catalogue; {status['error']}"
                for name, status in payload["sources"].items() if status["status"] != "fresh" and (not provider or name == provider)]
    clean_query = _clean_text(query)
    clean_limit = max(1, min(int(limit or 50), 50))
    clean_offset = max(0, int(offset))
    connection = sqlite3.connect(FTS_DB_PATH)
    connection.row_factory = sqlite3.Row
    try:
        match_query = _relaxed_match_query(clean_query)
        matching_count = _matching_count(connection, match_query, provider)
        rows = _matching_rows(connection, match_query, provider, clean_limit, clean_offset)
        entries = [_row_to_entry(row) for row in rows]
        return {
            "query": clean_query,
            "total": matching_count,
            "returned_count": len(entries),
            "limit": clean_limit,
            "offset": clean_offset,
            "next_offset": clean_offset + len(entries) if clean_offset + len(entries) < matching_count else None,
            "provider": provider or None,
            "ordering": "FTS text-match order; no dataset suitability score",
            "candidates": entries,
            "catalogue": {"entry_count": len(payload["entries"]), "generation": payload["generation"],
                          "last_updated": payload["lastUpdated"], "sources": payload["sources"],
                          "complete": all(status["status"] == "fresh" for status in payload["sources"].values()),
                          "refresh_seconds": payload.get("refresh_seconds"),
                          "cache_scope": "session", "path": str(CATALOG_PATH)},
            "warnings": warnings,
        }
    finally:
        connection.close()
