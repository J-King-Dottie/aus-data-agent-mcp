"""Parallel catalogue refresh with per-source freshness and explicit partial coverage."""

import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from uuid import uuid4

from .catalog_sources import PROVIDERS, fetch_source
from .runtime import CATALOG_TTL_SECONDS

SCHEMA_VERSION = 1
RETRY_SECONDS = 60


class CatalogueUnavailableError(RuntimeError):
    """Keep failed source attempts in memory so cold failures respect retry timing."""

    def __init__(self, payload):
        self.payload = payload
        errors = "; ".join(
            f"{provider}: {status.get('error')}" for provider, status in payload["sources"].items()
        )
        super().__init__(
            "No live catalogues available; retry discovery after 60 seconds. " + errors
        )


def valid_entry(row, provider=None):
    return (
        isinstance(row, dict)
        and all(
            isinstance(row.get(key), str)
            for key in ("datasetId", "provider", "title", "description", "searchText", "sourceUrl")
        )
        and bool(row["datasetId"])
        and row["provider"] in PROVIDERS
        and (provider is None or row["provider"] == provider)
        and isinstance(row.get("requiresMetadataBeforeRetrieval"), bool)
    )


def utc(timestamp):
    return datetime.fromtimestamp(timestamp, UTC).isoformat()


def due_sources(payload, force=False, now=None):
    now = time.time() if now is None else now
    sources = payload.get("sources", {})
    return [
        provider
        for provider in PROVIDERS
        if force or now >= sources.get(provider, {}).get("refresh_after", 0)
    ]


def _fetch(provider):
    start = time.perf_counter()
    try:
        entries = fetch_source(provider)
        if not entries or any(not valid_entry(row, provider) for row in entries):
            raise RuntimeError("Invalid or empty normalized catalogue response.")
        return entries, None, time.perf_counter() - start
    except Exception as exc:
        return [], f"{type(exc).__name__}: {exc}", time.perf_counter() - start


def refresh(previous, force=False):
    start = time.perf_counter()
    due = due_sources(previous, force)
    by_provider = {provider: [] for provider in PROVIDERS}
    for entry in previous.get("entries", []):
        if entry.get("provider") in by_provider:
            by_provider[entry["provider"]].append(entry)
    sources = {key: dict(value) for key, value in previous.get("sources", {}).items()}
    with ThreadPoolExecutor(max_workers=len(PROVIDERS), thread_name_prefix="catalogue") as pool:
        futures = {pool.submit(_fetch, provider): provider for provider in due}
        for future in as_completed(futures):
            provider = futures[future]
            entries, error, seconds = future.result()
            now = time.time()
            old = sources.get(provider, {})
            if error is None:
                by_provider[provider] = entries
            sources[provider] = {
                "status": "fresh"
                if error is None
                else ("stale" if by_provider[provider] else "unavailable"),
                "entry_count": len(by_provider[provider]),
                "fetched_at": utc(now) if error is None else old.get("fetched_at"),
                "fetched_timestamp": now if error is None else old.get("fetched_timestamp"),
                "attempted_at": utc(now),
                "fetch_seconds": round(seconds, 3),
                "refresh_after": now + (CATALOG_TTL_SECONDS if error is None else RETRY_SECONDS),
                "error": error,
            }
    entries = [entry for provider in PROVIDERS for entry in by_provider[provider]]
    payload = {
        "schema_version": SCHEMA_VERSION,
        "generation": uuid4().hex,
        "lastUpdated": utc(time.time()),
        "sources": sources,
        "entries": entries,
        "fetch_seconds": round(time.perf_counter() - start, 3),
    }
    if not entries:
        raise CatalogueUnavailableError(payload)
    return payload
