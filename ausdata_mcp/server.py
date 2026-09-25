from __future__ import annotations

import json
import logging
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from uuid import uuid4

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from .domestic_data import get_domestic_service
from .pacific_data import get_pacific_service
from .macro_data import (
    MacroCatalogEntry,
    _build_comtrade_metadata_payload,
    _fetch_comtrade,
    _fetch_imf,
    _fetch_oecd,
    _fetch_world_bank,
)
from .runtime import RUNTIME_DIR, SESSION_ID
from .unified_catalog import (
    get_unified_catalog_entry,
    search_unified_catalog,
)


logger = logging.getLogger("ausdata.mcp")
if not logger.handlers:
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("[%(asctime)s] %(levelname)s %(name)s - %(message)s"))
    logger.addHandler(handler)
logger.setLevel(logging.INFO)
logger.propagate = False

PROJECT_ROOT = Path(__file__).resolve().parents[1]
LARGE_ARTIFACT_BYTES = 50 * 1024 * 1024


def _utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _mcp_instructions() -> str:
    return (PROJECT_ROOT / "AGENT_SYSTEM_PROMPT.md").read_text(encoding="utf-8")


def _cid_prefix() -> str:
    return f"session={SESSION_ID} " if SESSION_ID else ""


def _clean_text(value: Any) -> str:
    return str(value or "").strip()


def _artifact_path(artifact_id: str) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,159}", artifact_id):
        raise ValueError("Invalid artifactId. Use an artifact_id returned by this MCP session.")
    directory = (RUNTIME_DIR / "sessions" / SESSION_ID / "artifacts").resolve()
    path = (directory / f"{artifact_id}.json").resolve()
    if path.parent != directory:
        raise ValueError("Artifact path must stay within this session.")
    return path


def _store_artifact(payload: Dict[str, Any], artifact_id: str) -> None:
    payload.setdefault("retrieved_at", _utc_timestamp())
    path = _artifact_path(artifact_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as file:
        json.dump(payload, file, ensure_ascii=False, separators=(",", ":"))


def _is_domestic_dataset(dataset_id: str) -> bool:
    clean = _clean_text(dataset_id)
    return clean.startswith("ABS,") or clean.startswith("CUSTOM_AUS,")


def _is_custom_domestic_dataset(dataset_id: str) -> bool:
    return _clean_text(dataset_id).startswith("CUSTOM_AUS,")


def _normalize_anchor_type(dimension_id: str, concept_id: str) -> str:
    text = " ".join(part.upper() for part in [_clean_text(dimension_id), _clean_text(concept_id)] if part)
    if "MEASURE" in text:
        return "MEASURE"
    if "DATA_ITEM" in text or text.endswith("ITEM") or " ITEM" in text:
        return "DATA_ITEM"
    if any(token in text for token in ("CAT", "CATEGORY", "SUPG", "SUPC", "PRODUCT", "COMMODITY", "INDUSTRY", "SECTOR", "FLOW")):
        return "CATEGORY"
    return ""


def _anchor_priority(anchor_type: str) -> int:
    priority_map = {"DATA_ITEM": 100, "MEASURE": 90, "CATEGORY": 80}
    return priority_map.get(_clean_text(anchor_type).upper(), 0)


def _raw_metadata_payload(dataset_id: str, metadata: Dict[str, Any]) -> Dict[str, Any]:
    dimensions = metadata.get("dimensions") if isinstance(metadata.get("dimensions"), list) else []
    concepts = metadata.get("concepts") if isinstance(metadata.get("concepts"), list) else []
    codelists = metadata.get("codelists") if isinstance(metadata.get("codelists"), list) else []
    codelist_by_id = {_clean_text(item.get("id")): item for item in codelists if isinstance(item, dict) and _clean_text(item.get("id"))}
    concept_by_id = {_clean_text(item.get("id")): item for item in concepts if isinstance(item, dict) and _clean_text(item.get("id"))}
    ordered_dimensions = sorted(
        [item for item in dimensions if isinstance(item, dict)],
        key=lambda item: int(item.get("position") or 0),
    )
    dimension_order: List[str] = []
    anchor_rows: List[Dict[str, Any]] = []

    for dimension in ordered_dimensions:
        dimension_id = _clean_text(dimension.get("id"))
        if not dimension_id:
            continue
        concept_id = _clean_text(dimension.get("conceptId"))
        concept = concept_by_id.get(concept_id, {})
        codelist_ref = dimension.get("codelist") if isinstance(dimension.get("codelist"), dict) else {}
        codelist_id = _clean_text(codelist_ref.get("id"))
        codelist = codelist_by_id.get(codelist_id, {})
        anchor_type = _normalize_anchor_type(dimension_id, concept_id)
        dimension_order.append(dimension_id)
        if not anchor_type:
            continue
        concept_name = (
            _clean_text(concept.get("name"))
            or _clean_text(concept.get("description"))
            or concept_id
            or dimension_id
        )
        anchor_codes = []
        for code in codelist.get("codes") if isinstance(codelist.get("codes"), list) else []:
            if not isinstance(code, dict):
                continue
            code_id = _clean_text(code.get("id"))
            if not code_id:
                continue
            anchor_codes.append(
                {
                    "code": code_id,
                    "label": _clean_text(code.get("name")),
                    "description": _clean_text(code.get("description")),
                }
            )
        anchor_rows.append(
            {
                "anchor_type": anchor_type,
                "dimension_id": dimension_id,
                "anchor_description": concept_name,
                "position": int(dimension.get("position") or 0),
                "anchor_codes": anchor_codes,
            }
        )

    def wildcard_template_for(anchor_dimension_id: str) -> str:
        parts = [f"{{{dimension_id}}}" if dimension_id == anchor_dimension_id else "" for dimension_id in dimension_order]
        return ".".join(parts) if parts else "all"

    anchor_candidates_by_type: Dict[str, Dict[str, Any]] = {}
    for row in anchor_rows:
        anchor_type = _clean_text(row.get("anchor_type")).upper()
        dimension_id = _clean_text(row.get("dimension_id"))
        if not anchor_type or not dimension_id:
            continue
        candidate = {
            "anchor_type": anchor_type,
            "anchor_description": _clean_text(row.get("anchor_description")) or anchor_type,
            "dimension_id": dimension_id,
            "wildcard_data_key_template": wildcard_template_for(dimension_id),
            "anchor_codes": row.get("anchor_codes") if isinstance(row.get("anchor_codes"), list) else [],
        }
        existing = anchor_candidates_by_type.get(anchor_type)
        if existing is None:
            anchor_candidates_by_type[anchor_type] = candidate
            continue
        existing_rank = dimension_order.index(_clean_text(existing.get("dimension_id")))
        current_rank = dimension_order.index(dimension_id)
        if existing_rank == -1 or (current_rank != -1 and current_rank < existing_rank):
            anchor_candidates_by_type[anchor_type] = candidate

    anchor_candidates = sorted(
        anchor_candidates_by_type.values(),
        key=lambda item: _anchor_priority(item.get("anchor_type", "")),
        reverse=True,
    )
    return {
        "kind": "raw_metadata",
        "dataset_id": dataset_id,
        "anchor_candidates": anchor_candidates,
        "metadata": metadata,
    }


def _build_wildcard_data_key(metadata_payload: Dict[str, Any], anchor_type: str, anchor_code: str) -> str:
    candidates = metadata_payload.get("anchor_candidates") if isinstance(metadata_payload.get("anchor_candidates"), list) else []
    target = None
    for item in candidates:
        if not isinstance(item, dict):
            continue
        if _clean_text(item.get("anchor_type")).upper() == _clean_text(anchor_type).upper():
            target = item
            break
    if target is None:
        raise RuntimeError(f"No anchor candidate found for anchorType={anchor_type}. Inspect metadata again.")
    allowed_codes = target.get("anchor_codes") if isinstance(target.get("anchor_codes"), list) else []
    clean_anchor_code = _clean_text(anchor_code)
    if not any(_clean_text(item.get("code")) == clean_anchor_code for item in allowed_codes if isinstance(item, dict)):
        raise RuntimeError(
            f"Invalid ABS anchor code '{clean_anchor_code}' for anchorType={anchor_type}. Choose a code from the metadata anchor_candidates list."
        )
    template = _clean_text(target.get("wildcard_data_key_template"))
    if not template:
        raise RuntimeError(f"Anchor candidate for anchorType={anchor_type} does not include a wildcard template.")
    return template.replace("{" + _clean_text(target.get("dimension_id")) + "}", clean_anchor_code)


def _validate_anchor_wildcard_data_key(dataset_id: str, data_key: str) -> None:
    clean_data_key = _clean_text(data_key)
    if not clean_data_key or clean_data_key.lower() == "all":
        raise RuntimeError(
            f"Invalid raw ABS dataKey. ABS retrieval must follow metadata-derived anchor selection; broad 'all' retrieval is not allowed. Received datasetId={dataset_id}, dataKey={data_key}."
        )
    segments = clean_data_key.split(".")
    fixed_segments = [segment.strip() for segment in segments if segment.strip()]
    if len(fixed_segments) != 1:
        raise RuntimeError(
            f"Invalid raw ABS dataKey. raw_retrieve must use exactly one anchored segment and wildcard every other segment. Received datasetId={dataset_id}, dataKey={data_key}."
        )
    anchor_token = fixed_segments[0]
    if "+" in anchor_token:
        raise RuntimeError(
            f"Invalid raw ABS dataKey. raw_retrieve must use exactly one anchor code, not multiple codes in one segment. Received datasetId={dataset_id}, dataKey={data_key}."
        )


def _store_retrieval(payload: Dict[str, Any], dataset_id: str, label: str) -> Dict[str, Any]:
    """Save complete evidence and describe it without returning the full dataset."""
    is_macro = isinstance(payload.get("selected_indicator"), dict)
    payload.setdefault("kind", "macro_retrieve" if is_macro else "domestic_retrieve")
    artifact_id = f"retrieval-{uuid4()}"
    _store_artifact(payload, artifact_id)
    path = _artifact_path(artifact_id)

    series_items = payload.get("series") if isinstance(payload.get("series"), list) else []
    period_start = period_end = None
    dimensions: set[str] = set()
    attributes: set[str] = set()
    units: set[str] = set()
    frequencies: set[str] = set()
    unit_multipliers: set[str] = set()
    preview: List[Dict[str, Any]] = []
    row_count = missing_count = 0

    for series in series_items:
        if not isinstance(series, dict):
            continue
        if is_macro:
            units.add(_clean_text(series.get("unit")))
            frequencies.add(_clean_text(series.get("frequency")))
            records = series.get("points") if isinstance(series.get("points"), list) else []
        else:
            dimensions.update((series.get("dimensions") or {}).keys())
            attributes.update((series.get("attributes") or {}).keys())
            records = series.get("observations") if isinstance(series.get("observations"), list) else []
        for record in records:
            if not isinstance(record, dict):
                continue
            row_count += 1
            record_dimensions = record.get("dimensions") if isinstance(record.get("dimensions"), dict) else {}
            if is_macro:
                period = _clean_text(record.get("x"))
            else:
                time_dimension = record_dimensions.get("TIME_PERIOD", (series.get("dimensions") or {}).get("TIME_PERIOD"))
                period = _clean_text(time_dimension.get("code") if isinstance(time_dimension, dict) else time_dimension)
                period = period or _clean_text(record.get("observationKey"))
            if period:
                period_start = min(period_start, period) if period_start else period
                period_end = max(period_end, period) if period_end else period
            value = record.get("y") if is_macro else record.get("value")
            missing_count += value is None
            if is_macro:
                if len(preview) < 3:
                    preview.append({"country_code": series.get("country_code"), "series_id": series.get("series_id"),
                                    "period": period, "value": value, "unit": series.get("unit")})
                continue
            record_attributes = record.get("attributes") if isinstance(record.get("attributes"), dict) else {}
            dimensions.update(record_dimensions.keys())
            attributes.update(record_attributes.keys())
            for key in ("UNIT", "UNIT_MEASURE", "Unit of measure"):
                unit = record_attributes.get(key, (series.get("attributes") or {}).get(key))
                if isinstance(unit, dict):
                    unit = unit.get("label") or unit.get("code")
                if _clean_text(unit):
                    units.add(_clean_text(unit))
            multiplier = record_attributes.get("UNIT_MULT", (series.get("attributes") or {}).get("UNIT_MULT"))
            if isinstance(multiplier, dict):
                multiplier = multiplier.get("code") or multiplier.get("label")
            if _clean_text(multiplier):
                unit_multipliers.add(_clean_text(multiplier))
            if len(preview) < 3:
                preview.append({"series_key": series.get("seriesKey"), "period": period, "value": value,
                                "dimension_codes": {key: str(item.get("code") if isinstance(item, dict) else item)[:80]
                                                    for key, item in list({**(series.get("dimensions") or {}), **record_dimensions}.items())[:12]}})

    references = payload.get("source_references") if isinstance(payload.get("source_references"), list) else []
    urls = payload.get("api_request_urls") if isinstance(payload.get("api_request_urls"), list) else []
    if not urls and payload.get("api_request_url"):
        urls = [payload["api_request_url"]]
    artifact_bytes = path.stat().st_size
    artifact_size_mib = round(artifact_bytes / (1024 * 1024), 2)
    large_artifact = artifact_bytes >= LARGE_ARTIFACT_BYTES
    return {
        "dataset_id": dataset_id,
        "provider": payload.get("provider") or payload.get("provider_key"),
        "label": label,
        "artifact_path": str(path),
        "artifact_format": "json",
        "artifact_bytes": artifact_bytes,
        "artifact_size_mib": artifact_size_mib,
        "large_artifact": large_artifact,
        "size_notice": (f"Saved JSON is {artifact_size_mib:.2f} MiB ({row_count:,} observations). "
                        "This is a large local file; inspect or process it selectively."
                        if large_artifact else None),
        "record_path": "series[*].points[*]" if is_macro else "series[*].observations[*]",
        "series_count": len(series_items),
        "row_count": row_count,
        "missing_value_count": missing_count,
        "period_start": period_start,
        "period_end": period_end,
        "dimension_ids": sorted(dimensions) if not is_macro else ["country_code", "series_id", "frequency", "unit"],
        "attribute_ids": sorted(attributes),
        "unit_examples": sorted(units - {""})[:10],
        "frequency_examples": sorted(frequencies - {""})[:10],
        "unit_multiplier_codes": sorted(unit_multipliers)[:10],
        "preview_rows": preview,
        "preview_truncated": row_count > len(preview),
        "api_request_urls": urls[:5],
        "api_request_url_count": len(urls),
        "source_references": references[:5],
        "source_reference_count": len(references),
        "source_annotation_keys": sorted((payload.get("source_annotations") or {}).keys()),
        "value_semantics": payload.get("value_semantics"),
        "retrieved_at": payload["retrieved_at"],
    }


def _summary(payload: Any) -> Dict[str, Any]:
    if isinstance(payload, dict):
        summary: Dict[str, Any] = {"keys": sorted(str(key) for key in list(payload.keys())[:12])}
        if isinstance(payload.get("candidates"), list):
            summary["candidates"] = len(payload["candidates"])
        if isinstance(payload.get("series"), list):
            summary["series"] = len(payload["series"])
        if isinstance(payload.get("dataflows"), list):
            summary["dataflows"] = len(payload["dataflows"])
        dataset = payload.get("dataset")
        if isinstance(dataset, dict):
            summary["datasetId"] = _clean_text(dataset.get("id"))
            summary["datasetName"] = _clean_text(dataset.get("name"))
        selected = payload.get("selected_indicator")
        if isinstance(selected, dict):
            summary["indicator"] = _clean_text(selected.get("indicator_label"))
        provider = payload.get("provider") or payload.get("provider_key")
        if provider:
            summary["provider"] = _clean_text(provider)
        if payload.get("api_request_url"):
            summary["api_request_url"] = _clean_text(payload.get("api_request_url"))
        if isinstance(payload.get("api_request_urls"), list):
            urls = [_clean_text(item) for item in payload.get("api_request_urls") if _clean_text(item)]
            if urls:
                summary["api_request_urls"] = urls[:5]
                summary["api_request_url_count"] = len(urls)
        return summary
    return {"type": type(payload).__name__}


def _route_entry(dataset_id: str) -> Dict[str, Any]:
    entry = get_unified_catalog_entry(dataset_id)
    if entry is None:
        raise RuntimeError(f"Unknown datasetId '{dataset_id}'. Search the unified catalog first.")
    return entry


def _macro_entry_from_record(record: Dict[str, Any]) -> MacroCatalogEntry:
    return MacroCatalogEntry(
        entry_id=_clean_text(record.get("datasetId")),
        provider_key=_clean_text(record.get("providerKey")),
        provider_name=_clean_text(record.get("providerName") or record.get("provider")),
        concept_id=_clean_text(record.get("conceptId")),
        concept_label=_clean_text(record.get("conceptLabel")),
        indicator_label=_clean_text(record.get("indicatorLabel")),
        unit=_clean_text(record.get("unit")),
        description=_clean_text(record.get("description")),
        search_text="",
        provider_config=dict(record.get("providerConfig") or {}),
    )


def _macro_metadata_from_record(record: Dict[str, Any], query: str) -> Dict[str, Any]:
    entry = _macro_entry_from_record(record)
    if entry.provider_key != "comtrade":
        return {
            "kind": "catalogue_metadata",
            "title": record.get("title"),
            "description": record.get("description"),
            "unit": record.get("unit"),
            "source_url": record.get("sourceUrl"),
            "source_identifiers": record.get("providerConfig"),
            "metadata_scope": "catalogue-level fields; inspect retrieved dimensions and units before analysis",
            "dataset_id": entry.entry_id,
            "provider": entry.provider_name,
            "summary": "Source identifiers and catalogue definitions; live availability and coverage are established by retrieval.",
        }
    clean_query = _clean_text(query) or entry.indicator_label or entry.concept_label or entry.entry_id
    return _build_comtrade_metadata_payload(clean_query, entry)


def _retrieve_macro_from_record(
    record: Dict[str, Any],
    query: str,
    *,
    countries: Optional[List[str]] = None,
    all_countries: bool = False,
    start_year: Optional[int] = None,
    end_year: Optional[int] = None,
    reporter_codes: Optional[List[str]] = None,
    partner_codes: Optional[List[str]] = None,
    flow_code: Optional[str] = None,
    frequency_code: Optional[str] = None,
    hs_codes: Optional[List[str]] = None,
) -> Dict[str, Any]:
    entry = _macro_entry_from_record(record)
    provider_key = entry.provider_key
    provider_config = dict(entry.provider_config)
    clean_query = _clean_text(query) or entry.indicator_label or entry.concept_label or entry.entry_id
    if provider_key == "worldbank":
        result = _fetch_world_bank(clean_query, entry, provider_config, countries or [], start_year, end_year, all_countries=all_countries)
    elif provider_key == "imf":
        result = _fetch_imf(clean_query, entry, provider_config, countries or [], start_year, end_year, all_countries=all_countries)
    elif provider_key == "oecd":
        result = _fetch_oecd(clean_query, entry, provider_config, countries or [], start_year, end_year, all_countries=all_countries)
    elif provider_key == "comtrade":
        result = _fetch_comtrade(
            clean_query,
            entry,
            provider_config,
            reporter_codes=[_clean_text(code) for code in (reporter_codes or []) if _clean_text(code)],
            partner_codes=[_clean_text(code) for code in (partner_codes or []) if _clean_text(code)],
            flow_code=_clean_text(flow_code).upper() or "",
            frequency_code=_clean_text(frequency_code).upper() or "",
            hs_codes=[_clean_text(code) for code in (hs_codes or []) if _clean_text(code)],
            start_year=start_year,
            end_year=end_year,
        )
    else:
        raise RuntimeError(f"Unsupported macro provider '{provider_key}'.")
    result["query"] = clean_query
    result["provider_key"] = provider_key
    result["selected_indicator"] = {
        "entry_id": entry.entry_id,
        "provider_key": entry.provider_key,
        "provider": entry.provider_name,
        "concept_id": entry.concept_id,
        "concept_label": entry.concept_label,
        "indicator_label": entry.indicator_label,
    }
    result["countries"] = countries or []
    result["all_countries"] = bool(all_countries)
    result["start_year"] = start_year
    result["end_year"] = end_year
    return result


server = FastMCP(
    name="australian-public-data-mcp",
    website_url="https://github.com/J-King-Dottie/australian-public-data-mcp",
    instructions=_mcp_instructions(),
)


@server.resource("ausdata://guide", mime_type="text/markdown")
def analyst_guide() -> str:
    """Canonical analyst guidance, also supplied during MCP initialization."""
    return _mcp_instructions()


@server.prompt()
def analyse_public_data(question: str) -> str:
    """Start a public-data analysis with the canonical evidence standards."""
    return f"{_mcp_instructions()}\n\nUser question:\n{question}"


@server.tool(
    title="Search catalog",
    annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True),
)
def search_catalog(query: str = "", forceRefresh: bool = False, limit: int = 50, provider: str = "", offset: int = 0) -> Dict[str, Any]:
    """Find candidate datasets using plain source/data terms.

    Returns up to limit (1-50, default 50) compact candidates, not observations.
    FTS orders matches by text relevance; no score or rank number is returned.
    Text order does not judge dataset suitability. The agent judges candidates on
    definitions and coverage. If total exceeds limit, follow next_offset with the
    same query to inspect more candidates, or refine the query.
    Optional provider: ABS, World Bank, OECD, IMF, RBA, DCCEEW, UN Comtrade,
    or PDH/SPC/Pacific Data Hub. All sources share one FTS index.
    First search fetches source catalogues concurrently and normalizes them into a
    session-local JSON file and SQLite FTS cache. Later searches reuse it for 24 hours
    (configurable); forceRefresh refreshes all sources. Allow time for a cold fetch.
    catalogue.sources reports freshness, errors, counts and fetch times. Warnings
    identify missing or stale sources; a failed source is retried after 60 seconds.
    Explicit searches of unavailable providers raise errors. This discovers datasets;
    read get_metadata for selected datasetId before retrieval.
    """
    started_at = time.perf_counter()
    logger.info("%stool=search_catalog event=start query=%r limit=%s refresh=%s", _cid_prefix(), query[:160], limit, forceRefresh)
    try:
        if not 1 <= limit <= 50:
            raise ValueError("limit must be between 1 and 50.")
        if offset < 0:
            raise ValueError("offset must be non-negative.")
        payload = search_unified_catalog(query, limit=limit, offset=offset, force_refresh=forceRefresh, provider=provider)
        logger.info(
            "%stool=search_catalog event=success duration_ms=%s summary=%s",
            _cid_prefix(),
            int((time.perf_counter() - started_at) * 1000),
            _summary(payload),
        )
        return payload
    except Exception as exc:
        logger.error("%stool=search_catalog event=error error=%s", _cid_prefix(), str(exc)[:500])
        raise


@server.tool(
    title="Get dataset metadata",
    annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True),
)
def get_metadata(
    datasetId: str = "",
    query: str = "",
    forceRefresh: bool = False,
    dimension: str = "",
    codeSearch: str = "",
    codeOffset: int = 0,
    codeLimit: int = 50,
) -> Dict[str, Any]:
    """Inspect metadata for one selected dataset candidate.

    Use after search_catalog identifies a plausible dataset. For ABS, choose one returned anchor_candidate
    and pass its anchorType and anchorCode to retrieve; do not invent data keys or filters.
    For PDH, returns ordered dimensions, attributes and codelist references. To browse codes,
    call again with dimension set to a returned dimension/attribute ID; codeSearch filters
    labels/codes, codeOffset/codeLimit page through matches (limit 1-200). Follow next_offset.
    Use exact PDH codes in retrieve.sourceFilters; UNIT_MULT codes explain value scaling.
    RBA/DCCEEW inspect the selected live file for valid dataKey choices. World Bank,
    IMF and OECD return catalogue-level definitions and source IDs, not live observation
    coverage. Comtrade returns code options; query helps find matching HS codes.
    For macro catalogue changes, refresh with search_catalog(forceRefresh=true).
    """
    started_at = time.perf_counter()
    logger.info("%stool=get_metadata event=start datasetId=%r query=%r", _cid_prefix(), datasetId[:160], query[:160])
    try:
        entry = _route_entry(datasetId)
        source_record = entry
        if datasetId.startswith("pdh::"):
            service = get_pacific_service()
            if not dimension and (codeSearch or codeOffset or codeLimit != 50):
                raise ValueError("Set dimension when browsing PDH codelist codes.")
            result = service.codes(datasetId, dimension, codeSearch, codeOffset, codeLimit, forceRefresh) if dimension else service.metadata(datasetId, forceRefresh)
        elif dimension or codeSearch or codeOffset:
            raise ValueError("Codelist browsing parameters currently apply to PDH datasets only.")
        elif _is_domestic_dataset(datasetId):
            payload = get_domestic_service().get_data_structure_for_dataflow(datasetId, bool(forceRefresh))
            result = payload if _is_custom_domestic_dataset(datasetId) else _raw_metadata_payload(datasetId, payload)
        else:
            clean_query = _clean_text(query) or _clean_text(entry.get("title")) or datasetId
            result = _macro_metadata_from_record(source_record, clean_query)
        logger.info(
            "%stool=get_metadata event=success duration_ms=%s summary=%s",
            _cid_prefix(),
            int((time.perf_counter() - started_at) * 1000),
            _summary(result),
        )
        return result
    except Exception as exc:
        logger.error("%stool=get_metadata event=error error=%s", _cid_prefix(), str(exc)[:500])
        raise


@server.tool(
    title="Retrieve dataset",
    annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=True),
)
def retrieve(
    datasetId: str = "",
    query: str = "",
    dataKey: str = "",
    anchorType: str = "",
    anchorCode: str = "",
    startPeriod: str = "",
    endPeriod: str = "",
    detail: str = "",
    dimensionAtObservation: str = "",
    forceRefresh: bool = False,
    countries: Optional[List[str]] = None,
    allCountries: bool = False,
    startYear: Optional[int] = None,
    endYear: Optional[int] = None,
    reporterCodes: Optional[List[str]] = None,
    partnerCodes: Optional[List[str]] = None,
    flowCode: str = "",
    frequencyCode: str = "",
    hsCodes: Optional[List[str]] = None,
    sourceFilters: Optional[Dict[str, List[str]]] = None,
) -> Dict[str, Any]:
    """Retrieve one source dataset or ABS anchor and save the complete result to JSON.

    Use a datasetId from search_catalog and inspect get_metadata first. ABS requires
    anchorType + anchorCode from anchor_candidates (dataKey is rejected); startPeriod/endPeriod
    bound source periods, e.g. 2020 or 2020-Q1. Custom Australian sources may accept dataKey.
    World Bank/IMF/OECD use countries (ISO3 codes), startYear/endYear or allCountries.
    OECD rejects dataflows that yield multiple observations per country-period because
    additional dimensions cannot be safely collapsed into one series.
    Comtrade requires explicit reporterCodes, partnerCodes (['0'] means World total),
    flowCode, frequencyCode, hsCodes (['TOTAL'] means all products), startYear and
    endYear. Inspect its metadata for valid source codes; no trade scope is guessed.
    PDH uses sourceFilters with exact dimension codes from get_metadata, e.g. GEO_PICT:
    ["FJ"] when that code exists, plus startPeriod/endPeriod. An inspected positional dataKey
    is also accepted, but cannot be combined with sourceFilters. PDH geography codes are
    source-specific, not the ISO3 countries parameter. Unit multipliers are preserved, not applied.
    Returns an absolute artifact_path, exact artifact_bytes, artifact_size_mib (MiB),
    large_artifact flag at 50 MiB, counts, coverage, a three-row preview and bounded
    provenance. When large_artifact is true, size_notice explains the local storage cost.
    Read the saved file with your own code to inspect,
    filter and analyse every required observation; preview_rows are not complete data.
    The path is on the MCP server's filesystem. Source requests may take up to 120 seconds.
    """
    started_at = time.perf_counter()
    logger.info("%stool=retrieve event=start datasetId=%r query=%r", _cid_prefix(), datasetId[:160], query[:160])
    try:
        entry = _route_entry(datasetId)
        source_record = entry
        if datasetId.startswith("pdh::"):
            if countries or allCountries or startYear is not None or endYear is not None or anchorType or anchorCode or reporterCodes or partnerCodes or flowCode or frequencyCode or hsCodes or detail or dimensionAtObservation:
                raise ValueError("PDH uses sourceFilters (or dataKey) and startPeriod/endPeriod. Inspect get_metadata for source dimensions.")
            result = get_pacific_service().retrieve(datasetId, sourceFilters, dataKey, startPeriod, endPeriod, forceRefresh)
            manifest = _store_retrieval(result, datasetId, entry["title"])
        elif sourceFilters:
            raise ValueError("sourceFilters currently applies to PDH datasets only.")
        elif _is_domestic_dataset(datasetId):
            clean_data_key = _clean_text(dataKey)
            if not _is_custom_domestic_dataset(datasetId):
                if clean_data_key:
                    raise RuntimeError(
                        "ABS retrieve does not accept dataKey input. Use get_metadata first, choose one anchor from anchor_candidates, and call retrieve with anchorType + anchorCode."
                    )
                clean_anchor_type = _clean_text(anchorType).upper()
                clean_anchor_code = _clean_text(anchorCode)
                if not clean_anchor_type or not clean_anchor_code:
                    raise RuntimeError(
                        "ABS retrieve requires anchorType + anchorCode from get_metadata. ABS always uses metadata-derived anchor + wildcard retrieval."
                    )
                metadata = get_domestic_service().get_data_structure_for_dataflow(datasetId, bool(forceRefresh))
                metadata_payload = _raw_metadata_payload(datasetId, metadata)
                clean_data_key = _build_wildcard_data_key(metadata_payload, clean_anchor_type, clean_anchor_code)
                _validate_anchor_wildcard_data_key(datasetId, clean_data_key)
            result = get_domestic_service().resolve_dataset(
                datasetId,
                data_key=clean_data_key or "",
                start_period=_clean_text(startPeriod),
                end_period=_clean_text(endPeriod),
                detail=_clean_text(detail),
                dimension_at_observation=_clean_text(dimensionAtObservation),
                force_refresh=bool(forceRefresh),
            )
            dataset = result.get("dataset") if isinstance(result.get("dataset"), dict) else {}
            if not result.get("source_references"):
                result["source_references"] = [{
                    "provider": entry.get("provider"),
                    "dataset_id": datasetId,
                    "source_url": entry.get("sourceUrl"),
                    "api_request_url": result.get("api_request_url"),
                }]
            label = _clean_text(dataset.get("name")) or _clean_text(dataset.get("id")) or datasetId
            manifest = _store_retrieval(result, datasetId, label)
        else:
            clean_query = _clean_text(query) or _clean_text(entry.get("title")) or datasetId
            result = _retrieve_macro_from_record(
                source_record,
                clean_query,
                countries=countries,
                all_countries=bool(allCountries),
                start_year=startYear,
                end_year=endYear,
                reporter_codes=reporterCodes,
                partner_codes=partnerCodes,
                flow_code=_clean_text(flowCode).upper() or None,
                frequency_code=_clean_text(frequencyCode).upper() or None,
                hs_codes=hsCodes,
            )
            label = _clean_text(
                (result.get("selected_indicator") if isinstance(result.get("selected_indicator"), dict) else {}).get("indicator_label")
                or result.get("concept_label")
                or result.get("provider")
            ) or datasetId
            manifest = _store_retrieval(result, datasetId, label)
        logger.info(
            "%stool=retrieve event=success duration_ms=%s summary=%s",
            _cid_prefix(),
            int((time.perf_counter() - started_at) * 1000),
            _summary(manifest),
        )
        return manifest
    except Exception as exc:
        logger.error("%stool=retrieve event=error error=%s", _cid_prefix(), str(exc)[:500])
        raise


if __name__ == "__main__":
    server.run()
