from __future__ import annotations

import hashlib
import json
import logging
import re
import copy
from contextlib import contextmanager
from datetime import datetime, timezone
from threading import Lock
from typing import Any, Dict

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Json

from .config import get_settings


logger = logging.getLogger("abs.backend.validated_variables")
_MCP_REPLAY_LOCK = Lock()
ALLOWED_MCP_REPLAY_TOOLS = {
    "retrieve",
    "narrow_artifact",
}
TRANSFORMATION_KEYS = {"transform_code", "code", "formula", "steps"}
URL_KEYS = (
    "validated_api_url",
    "api_request_urls",
    "api_request_url",
    "request_url",
)
SUPPORTED_OFFICIAL_SOURCE_MARKERS = {
    "ABS": ("abs", "abs.gov.au", "australian bureau of statistics", "data.api.abs.gov.au"),
    "OECD": ("oecd", "sdmx.oecd.org"),
    "World Bank": ("world bank", "worldbank", "api.worldbank.org"),
    "IMF": ("imf", "imf.org"),
    "RBA": ("rba", "reserve bank of australia", "rba.gov.au"),
    "UN Comtrade": ("un comtrade", "comtrade", "comtradeapi.un.org"),
}
PERIOD_KEYS = (
    "TIME_PERIOD",
    "time_period",
    "Time",
    "time",
    "TIME",
    "Period",
    "period",
    "Date",
    "date",
    "Quarter",
    "quarter",
    "Year",
    "year",
    "observationKey",
    "x",
)


def _as_text(value: Any) -> str:
    return str(value or "").strip()


def _as_number(value: Any) -> float | None:
    try:
        number = float(value)
    except Exception:
        return None
    if number != number:
        return None
    return number


def _jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _slug(value: str) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")
    return cleaned[:72] or "validated_variable"


def _external_key(payload: Dict[str, Any]) -> str:
    source = json.dumps(_jsonable(payload), ensure_ascii=False, sort_keys=True)
    digest = hashlib.sha256(source.encode("utf-8")).hexdigest()[:16]
    label = _slug(_as_text(payload.get("name")) or _as_text(payload.get("metric")))
    return f"{label}_{digest}"


def _connect():
    database_url = _as_text(get_settings().database_url)
    if not database_url:
        raise RuntimeError("DATABASE_URL is not configured.")
    return psycopg.connect(database_url, connect_timeout=20, row_factory=dict_row)


def _looks_like_url(value: Any) -> str:
    text = _as_text(value)
    if "…" in text or (text.endswith("...") and not text.endswith("....")):
        return ""
    if text.startswith(("https://", "http://")):
        return text
    return ""


def _extract_validated_api_url(*values: Any) -> str:
    def walk(value: Any) -> str:
        if isinstance(value, dict):
            for key in URL_KEYS:
                candidate = value.get(key)
                if isinstance(candidate, list):
                    for item in candidate:
                        found = _looks_like_url(item)
                        if found:
                            return found
                else:
                    found = _looks_like_url(candidate)
                    if found:
                        return found
            for item in value.values():
                found = walk(item)
                if found:
                    return found
        if isinstance(value, list):
            for item in value:
                found = walk(item)
                if found:
                    return found
        return _looks_like_url(value)

    for value in values:
        found = walk(value)
        if found:
            return found
    return ""


def _short_text(value: Any, limit: int = 280) -> str:
    text = re.sub(r"\s+", " ", _as_text(value))
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)].rstrip() + "..."


def _non_empty_dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _first_non_empty(*values: Any) -> str:
    for value in values:
        text = _as_text(value)
        if text:
            return text
    return ""


def _narrow_step_args(retrieval_logic: Dict[str, Any]) -> Dict[str, Any]:
    for step in retrieval_logic.get("steps") if isinstance(retrieval_logic.get("steps"), list) else []:
        if isinstance(step, dict) and _as_text(step.get("tool")) == "narrow_artifact":
            args = step.get("args")
            return args if isinstance(args, dict) else {}
    narrow = retrieval_logic.get("narrow")
    if isinstance(narrow, dict):
        args = narrow.get("args")
        return args if isinstance(args, dict) else {}
    return {}


def _contents_preview_payload(evidence_artifact: Dict[str, Any]) -> Dict[str, Any]:
    preview: Dict[str, Any] = {}
    latest_preview = evidence_artifact.get("latest_preview")
    if isinstance(latest_preview, dict) and latest_preview:
        preview["latest_preview"] = latest_preview
    validated_points = evidence_artifact.get("validated_points")
    if isinstance(validated_points, list) and validated_points:
        preview["validated_points"] = validated_points[:12]
    manifest = _non_empty_dict(evidence_artifact.get("artifact_manifest"))
    preview_rows = manifest.get("preview_rows")
    if isinstance(preview_rows, list) and preview_rows:
        preview["source_preview_rows"] = preview_rows[:5]
    return preview


def _period_value_from_row(row: Dict[str, Any]) -> str:
    for key in PERIOD_KEYS:
        value = _as_text(row.get(key))
        if value:
            return value
    for key, value in row.items():
        key_text = _as_text(key).lower()
        if key_text in {"time_period", "time", "period", "date", "quarter", "year"}:
            text = _as_text(value)
            if text:
                return text
    return ""


def _value_key_from_headers(headers: list[str]) -> str:
    for key in ("value", "y", "VALUE", "OBS_VALUE", "obs_value"):
        if key in headers:
            return key
    for key in headers:
        if _as_text(key).lower() in {"value", "y", "obs_value"}:
            return key
    return headers[-1] if headers else "value"


def _series_label_from_key(key: str) -> str:
    words = [
        word
        for word in _as_text(key).replace("-", "_").split("_")
        if word and word.lower() not in {"value", "values", "usd", "aud", "billion", "million", "number", "count"}
    ]
    return " ".join(words).capitalize() if words else _as_text(key)


def _period_key_from_headers(headers: list[str], rows: list[Dict[str, Any]]) -> str:
    for key in PERIOD_KEYS:
        if key in headers:
            return key
    for key in headers:
        if any(_period_value_from_row(row) == _as_text(row.get(key)) and _as_text(row.get(key)) for row in rows):
            return key
    return "period"


def compact_validated_data_from_rows(
    *,
    artifact_kind: str,
    artifact_id: str,
    variable_name: str,
    headers: list[str],
    rows: list[Dict[str, Any]],
    transformation_logic: Dict[str, Any],
    source: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    clean_headers = [_as_text(header) for header in headers if _as_text(header)]
    row_dicts = [row for row in rows if isinstance(row, dict)]
    dimensions: Dict[str, Any] = {}
    varying_columns: list[str] = []
    for key in clean_headers:
        if key in {"observationKey", "observation_key"}:
            continue
        values = {_as_text(row.get(key)) for row in row_dicts if row.get(key) is not None}
        if len(values) <= 1 and row_dicts:
            dimensions[key] = row_dicts[0].get(key)
        else:
            varying_columns.append(key)
    if not varying_columns and clean_headers:
        value_key = _value_key_from_headers(clean_headers)
        if value_key in dimensions:
            dimensions.pop(value_key, None)
        varying_columns = [value_key]
    period_key = _period_key_from_headers(clean_headers, row_dicts)
    has_period = period_key in varying_columns
    sorted_rows = sorted(row_dicts, key=_period_value_from_row) if has_period else row_dicts
    records = [[row.get(key) for key in varying_columns] for row in sorted_rows]
    return {
        "version": 2,
        "kind": "validated_variable_data",
        "artifact_kind": _as_text(artifact_kind),
        "artifact_id": _as_text(artifact_id),
        "variable_name": _as_text(variable_name),
        "dimensions": dimensions,
        "columns": varying_columns,
        "records": records,
        "row_count": len(records),
        "source_columns": clean_headers,
        "period_key": period_key if has_period else "",
        "value_key": _value_key_from_headers(clean_headers),
        "transformation_logic": transformation_logic,
        "source": source or {},
        "instruction": "This is the compact approved data slice. Constant fields are in dimensions; records contain only varying columns.",
    }


def validated_data_rows(validated_data: Dict[str, Any]) -> list[Dict[str, Any]]:
    if isinstance(validated_data.get("rows"), list):
        return [row for row in validated_data.get("rows") if isinstance(row, dict)]
    records = validated_data.get("records")
    if not isinstance(records, list):
        records = validated_data.get("observations") if isinstance(validated_data.get("observations"), list) else []
    columns = validated_data.get("columns")
    if not isinstance(columns, list):
        columns = validated_data.get("observation_columns") if isinstance(validated_data.get("observation_columns"), list) else []
    dimensions = validated_data.get("dimensions") if isinstance(validated_data.get("dimensions"), dict) else {}
    if not records or not columns:
        return []
    rows: list[Dict[str, Any]] = []
    for record in records:
        if not isinstance(record, list):
            continue
        row = dict(dimensions)
        for index, column in enumerate(columns):
            if index >= len(record):
                continue
            target_key = _as_text(column)
            if target_key:
                row[target_key] = record[index]
        rows.append(row)
    return rows


def validated_data_headers(validated_data: Dict[str, Any]) -> list[str]:
    headers = validated_data.get("headers") if isinstance(validated_data.get("headers"), list) else []
    if headers:
        return [_as_text(header) for header in headers if _as_text(header)]
    source_columns = validated_data.get("source_columns") if isinstance(validated_data.get("source_columns"), list) else []
    if source_columns:
        return [_as_text(header) for header in source_columns if _as_text(header)]
    rows = validated_data_rows(validated_data)
    return list(rows[0].keys()) if rows else []


def validated_data_latest_rows(validated_data: Dict[str, Any], limit: int = 20) -> list[Dict[str, Any]]:
    latest = validated_data.get("latest_rows") if isinstance(validated_data.get("latest_rows"), list) else []
    if latest:
        return [row for row in latest[-limit:] if isinstance(row, dict)]
    return validated_data_rows(validated_data)[-limit:]


def _node_chart_data_from_validated_variable(
    *,
    node_id: str,
    node_title: str,
    unit: str,
    validated_data: Dict[str, Any],
) -> Dict[str, Any]:
    rows = validated_data_rows(validated_data)
    headers = validated_data_headers(validated_data)
    period_key = _as_text(validated_data.get("period_key"))
    value_key = _as_text(validated_data.get("value_key"))
    if not period_key:
        period_key = _period_key_from_headers(headers, rows)
    if not value_key:
        value_key = next((header for header in headers if header.lower() in {"value", "y", "obs_value"}), "")
    if not value_key:
        value_key = next(
            (
                header
                for header in headers
                if any(_as_number(row.get(header)) is not None for row in rows)
            ),
            "",
        )
    numeric_keys = [
        header
        for header in headers
        if header != period_key and any(_as_number(row.get(header)) is not None for row in rows)
    ]
    canonical_value_keys = {"value", "y", "obs_value", "OBS_VALUE", "VALUE"}
    long_category_keys = [
        header
        for header in headers
        if header not in {period_key, value_key}
        and header not in {"observationKey", "observation_key"}
        and not any(_as_number(row.get(header)) is not None for row in rows)
        and len({_as_text(row.get(header)) for row in rows if _as_text(row.get(header))}) > 1
    ]

    series: list[Dict[str, Any]] = []
    if period_key and value_key and value_key in headers and long_category_keys:
        grouped: Dict[str, list[list[Any]]] = {}
        for row in rows:
            label = " / ".join(_as_text(row.get(key)) for key in long_category_keys if _as_text(row.get(key)))
            x_value = _as_text(row.get(period_key))
            y_value = _as_number(row.get(value_key))
            if label and x_value and y_value is not None:
                grouped.setdefault(label, []).append([x_value, y_value])
        series = [{"name": label, "points": points} for label, points in grouped.items() if points]
    elif period_key and len(numeric_keys) > 1 and _as_text(validated_data.get("value_key")) not in canonical_value_keys:
        for key in numeric_keys:
            points = []
            for row in rows:
                x_value = _as_text(row.get(period_key))
                y_value = _as_number(row.get(key))
                if x_value and y_value is not None:
                    points.append([x_value, y_value])
            if points:
                series.append({"name": _series_label_from_key(key), "points": points})

    if series:
        fingerprint_payload = {"node_id": node_id, "node_title": node_title, "unit": unit, "series": series}
        return {
            "kind": "node_chart_multi_series",
            "node_id": node_id,
            "node_title": node_title,
            "unit": unit,
            "data_kind": "saved",
            "computed_at": datetime.now(timezone.utc).isoformat(),
            "input_fingerprint": hashlib.sha256(
                json.dumps(fingerprint_payload, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
            ).hexdigest(),
            "series": series,
        }

    x_key = period_key or next((header for header in headers if header != value_key), "")
    if not x_key or not value_key:
        raise RuntimeError("Validated variable data must be chartable before linking it to a graph node.")
    records = []
    for row in rows:
        x_value = _as_text(row.get(x_key))
        y_value = _as_number(row.get(value_key))
        if x_value and y_value is not None:
            records.append([x_value, y_value])
    if not records:
        raise RuntimeError("Validated variable data produced no chartable node records.")
    fingerprint_payload = {"node_id": node_id, "node_title": node_title, "unit": unit, "records": records}
    return {
        "kind": "node_chart_series",
        "node_id": node_id,
        "node_title": node_title,
        "unit": unit,
        "data_kind": "saved",
        "computed_at": datetime.now(timezone.utc).isoformat(),
        "input_fingerprint": hashlib.sha256(
            json.dumps(fingerprint_payload, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
        ).hexdigest(),
        "columns": ["period", "value"],
        "records": records,
    }


def apply_transformation_rows(rows: list[Dict[str, Any]], transformation_logic: Dict[str, Any]) -> list[Dict[str, Any]]:
    if not isinstance(transformation_logic, dict):
        return rows
    code = _as_text(transformation_logic.get("code") or transformation_logic.get("transform_code"))
    if not code:
        return rows
    if code.lower() in {
        "return the narrowed series unchanged",
        "identity",
        "no transformation",
        "return rows unchanged",
    }:
        return rows
    local_scope: Dict[str, Any] = {
        "rows": copy.deepcopy(rows),
        "metadata": copy.deepcopy(transformation_logic),
        "result": None,
    }
    safe_builtins = {
        "abs": abs,
        "all": all,
        "any": any,
        "bool": bool,
        "dict": dict,
        "enumerate": enumerate,
        "float": float,
        "int": int,
        "isinstance": isinstance,
        "len": len,
        "list": list,
        "max": max,
        "min": min,
        "range": range,
        "round": round,
        "set": set,
        "sorted": sorted,
        "str": str,
        "sum": sum,
        "tuple": tuple,
        "zip": zip,
    }
    exec(code, {"__builtins__": safe_builtins}, local_scope)
    transform = local_scope.get("transform")
    result = transform(copy.deepcopy(rows), copy.deepcopy(transformation_logic)) if callable(transform) else local_scope.get("result")
    if result is None:
        result = local_scope.get("rows")
    if not isinstance(result, list) or not all(isinstance(item, dict) for item in result):
        raise RuntimeError("Validated-variable transformation code must return a list of row dictionaries.")
    return result


def _infer_period_range_from_validated_data(validated_data: Dict[str, Any]) -> tuple[str, str]:
    rows = validated_data_rows(validated_data)
    periods = sorted(
        {
            _period_value_from_row(row)
            for row in rows
            if isinstance(row, dict) and _period_value_from_row(row)
        }
    )
    if not periods:
        return "", ""
    return periods[0], periods[-1]


def _build_variable_contents_metadata(
    *,
    name: str,
    label: str,
    source_name: str,
    provider_id: str,
    dataset_id: str,
    metric: str,
    unit: str,
    geography: str,
    frequency: str,
    seasonal_treatment: str,
    period_start: str,
    period_end: str,
    validated_api_url: str,
    retrieval_logic: Dict[str, Any],
    transformation_logic: Dict[str, Any],
    transform_summary: str,
    recreation_summary: str,
    evidence_artifact: Dict[str, Any],
    node_description: str = "",
) -> Dict[str, Any]:
    manifest = _non_empty_dict(evidence_artifact.get("artifact_manifest"))
    dimensions = manifest.get("dimensions") if isinstance(manifest.get("dimensions"), dict) else {}
    narrow_args = _narrow_step_args(retrieval_logic)
    dimension_filters = narrow_args.get("dimensionFiltersMap")
    if not isinstance(dimension_filters, dict):
        dimension_filters = narrow_args.get("dimensionFilters") if isinstance(narrow_args.get("dimensionFilters"), dict) else {}
    source_frequency = _first_non_empty(transformation_logic.get("source_frequency"), frequency)
    target_frequency = _first_non_empty(transformation_logic.get("target_frequency"), frequency)
    transform_text = _first_non_empty(transform_summary, transformation_logic.get("formula"), transformation_logic.get("transform_code"), transformation_logic.get("code"))
    display_label = _first_non_empty(label, name, metric, "Validated variable")
    source_label = _first_non_empty(source_name, provider_id, "source")
    period_text = ""
    if _as_text(period_start) and _as_text(period_end):
        period_text = f"{_as_text(period_start)} to {_as_text(period_end)}"
    elif _as_text(period_start) or _as_text(period_end):
        period_text = _as_text(period_start) or _as_text(period_end)
    parts = [
        f"{display_label} contains {_first_non_empty(metric, display_label)}",
        f"from {source_label}",
    ]
    if _as_text(geography):
        parts.append(f"for {_as_text(geography)}")
    if _as_text(unit):
        parts.append(f"in {_as_text(unit)}")
    if _as_text(frequency):
        parts.append(f"at {_as_text(frequency)} frequency")
    if _as_text(seasonal_treatment):
        parts.append(f"using {_as_text(seasonal_treatment)} treatment")
    if period_text:
        parts.append(f"covering {period_text}")
    if transform_text:
        parts.append(f"with transformation: {_short_text(transform_text, 180).rstrip('.')}")
    contents_summary = _short_text("; ".join(parts) + ".", 900)
    clean_node_description = _short_text(node_description, 700)
    return {
        "metadata_version": 1,
        "node_description": clean_node_description,
        "contents_summary": contents_summary,
        "contents": {
            "name": _as_text(name),
            "label": display_label,
            "node_description": clean_node_description,
            "description": contents_summary,
            "source": {
                "name": _as_text(source_name),
                "provider_id": _as_text(provider_id),
                "dataset_id": _as_text(dataset_id),
                "validated_api_url": _as_text(validated_api_url),
            },
            "metric": _as_text(metric),
            "unit": _as_text(unit),
            "geography": _as_text(geography),
            "frequency": _as_text(frequency),
            "seasonal_treatment": _as_text(seasonal_treatment),
            "period_start": _as_text(period_start),
            "period_end": _as_text(period_end),
            "source_frequency": source_frequency,
            "target_frequency": target_frequency,
            "transformation": {
                "summary": _as_text(transform_summary),
                "logic": transformation_logic,
            },
            "recreation_summary": _as_text(recreation_summary),
            "selection": {
                "artifact_id": _as_text(evidence_artifact.get("artifact_id")),
                "artifact_kind": _as_text(evidence_artifact.get("kind")),
                "dimension_filters": dimension_filters,
                "dimensions": dimensions,
                "series_count": manifest.get("series_count"),
                "observation_count": manifest.get("observation_count") or manifest.get("point_count"),
            },
            "preview": _contents_preview_payload(evidence_artifact),
        },
    }


def _artifact_trail_from_evidence(evidence_artifact: Dict[str, Any]) -> list[Dict[str, Any]]:
    trail = evidence_artifact.get("artifact_trail")
    if isinstance(trail, list):
        return [item for item in trail if isinstance(item, dict)]
    current = {
        "role": "narrow_artifact",
        "artifact_id": _as_text(evidence_artifact.get("artifact_id")),
        "parent_artifact_id": _as_text(evidence_artifact.get("parent_artifact_id")),
        "kind": _as_text(evidence_artifact.get("kind")),
        "label": _as_text(evidence_artifact.get("label")),
        "summary": _as_text(evidence_artifact.get("summary")),
        "api_request_url": _as_text(evidence_artifact.get("api_request_url")),
        "api_request_urls": evidence_artifact.get("api_request_urls") if isinstance(evidence_artifact.get("api_request_urls"), list) else [],
        "analysis_container_id": _as_text(evidence_artifact.get("analysis_container_id")),
        "analysis_file_id": _as_text(evidence_artifact.get("analysis_file_id")),
        "analysis_filename": _as_text(evidence_artifact.get("analysis_filename")),
        "analysis_local_path": _as_text(evidence_artifact.get("analysis_local_path")),
        "artifact_manifest": evidence_artifact.get("artifact_manifest") if isinstance(evidence_artifact.get("artifact_manifest"), dict) else {},
    }
    return [{key: value for key, value in current.items() if value not in ("", [], {})}]


def _build_replay_package(
    *,
    validated_api_url: str,
    retrieval_logic: Dict[str, Any],
    transformation_logic: Dict[str, Any],
    transform_summary: str,
    recreation_summary: str,
    evidence_artifact: Dict[str, Any],
) -> Dict[str, Any]:
    steps = _normalize_replay_steps(retrieval_logic)
    exact_steps = []
    for index, step in enumerate(steps, start=1):
        exact_steps.append(
            {
                "id": _as_text(step.get("id")) or f"step_{index}",
                "tool": _as_text(step.get("tool")),
                "args": step.get("args") if isinstance(step.get("args"), dict) else {},
                "source_id": _as_text(step.get("source_id")),
                "validated_api_url": _as_text(step.get("validated_api_url")),
                "validated_api_urls": step.get("validated_api_urls") if isinstance(step.get("validated_api_urls"), list) else [],
            }
        )
    return {
        "version": 1,
        "kind": "validated_variable_replay_package",
        "validated_api_url": _as_text(validated_api_url),
        "api_request_urls": retrieval_logic.get("api_request_urls") if isinstance(retrieval_logic.get("api_request_urls"), list) else [],
        "retrieval_logic": retrieval_logic,
        "exact_steps": exact_steps,
        "transformation_logic": transformation_logic,
        "transform_summary": _as_text(transform_summary),
        "recreation_summary": _as_text(recreation_summary),
        "artifact_trail": _artifact_trail_from_evidence(evidence_artifact),
        "final_artifact": {
            "artifact_id": _as_text(evidence_artifact.get("artifact_id")),
            "parent_artifact_id": _as_text(evidence_artifact.get("parent_artifact_id")),
            "kind": _as_text(evidence_artifact.get("kind")),
            "analysis_container_id": _as_text(evidence_artifact.get("analysis_container_id")),
            "analysis_file_id": _as_text(evidence_artifact.get("analysis_file_id")),
            "analysis_filename": _as_text(evidence_artifact.get("analysis_filename")),
            "analysis_local_path": _as_text(evidence_artifact.get("analysis_local_path")),
        },
        "instruction": (
            "Replay this variable literally. Use exact_steps and transformation_logic as saved; do not substitute "
            "another artifact, metric, URL, geography, treatment, or transform."
        ),
    }


def _build_refresh_code(refresh_metadata: Dict[str, Any]) -> str:
    payload = json.dumps(_jsonable(refresh_metadata), ensure_ascii=False, indent=2, sort_keys=True)
    return f'''"""Refresh code for a Nisaba validated variable.

Run from the project backend environment. It recreates the approved narrowed data slice.
"""

import json
from backend.app import unified_mcp_server as mcp_tools
from backend.app.validated_variables import apply_transformation_rows, compact_validated_data_from_rows

REFRESH_METADATA = json.loads({payload!r})


def _interpolate(value, outputs):
    if isinstance(value, str) and value.startswith("${{") and value.endswith("}}"):
        token = value[2:-1].strip()
        if token == "latest_artifact_id":
            return outputs.get("_latest_artifact_id", "")
        if "." in token:
            step_id, path = token.split(".", 1)
            current = outputs.get(step_id)
            for part in path.split("."):
                if isinstance(current, dict):
                    current = current.get(part)
                elif isinstance(current, list) and part.isdigit():
                    idx = int(part)
                    current = current[idx] if 0 <= idx < len(current) else None
                else:
                    current = None
            return current or ""
        return outputs.get(token, "")
    if isinstance(value, dict):
        return {{key: _interpolate(item, outputs) for key, item in value.items()}}
    if isinstance(value, list):
        return [_interpolate(item, outputs) for item in value]
    return value


def _artifact_id(payload):
    if isinstance(payload, dict):
        for key in ("artifact_id", "artifactId", "id"):
            if payload.get(key):
                return str(payload[key])
    return ""


def refresh(existing_variable=None):
    outputs = {{}}
    latest_artifact_id = ""
    source_rows = []
    source_kinds = []
    source_artifact_ids = []
    for step in REFRESH_METADATA["steps"]:
        tool = step["tool"]
        args = _interpolate(step["args"], outputs)
        if tool == "retrieve":
            result = mcp_tools.retrieve(**args)
        elif tool == "narrow_artifact":
            result = mcp_tools.narrow_artifact(**args)
        else:
            raise RuntimeError(f"Unsupported validated-variable refresh tool: {{tool}}")
        latest_artifact_id = _artifact_id(result) or latest_artifact_id
        if latest_artifact_id:
            outputs["_latest_artifact_id"] = latest_artifact_id
        outputs[step["id"]] = result
        if tool == "narrow_artifact":
            payload = mcp_tools._load_artifact_payload(latest_artifact_id)
            kind = mcp_tools._artifact_kind(latest_artifact_id, payload)
            if kind.startswith("domestic"):
                headers, rows = mcp_tools._flatten_domestic_payload(payload)
            elif kind.startswith("macro"):
                headers, rows = mcp_tools._flatten_macro_payload(payload)
            else:
                raise RuntimeError(f"Unsupported validated-variable artifact kind: {{kind}}")
            source_id = step.get("source_id") or step["id"]
            for row in rows:
                row_dict = {{headers[index]: row[index] for index in range(min(len(headers), len(row)))}}
                row_dict["_source_step_id"] = source_id
                row_dict["_source_artifact_id"] = latest_artifact_id
                source_rows.append(row_dict)
            source_kinds.append(kind)
            source_artifact_ids.append(latest_artifact_id)
    if not source_rows:
        raise RuntimeError("Validated-variable refresh did not produce narrowed source rows.")

    transformation_logic = REFRESH_METADATA.get("transformation_logic", {{}})
    transformed_rows = apply_transformation_rows(source_rows, transformation_logic)
    transformed_headers = list(transformed_rows[0].keys()) if transformed_rows else list(source_rows[0].keys())
    return compact_validated_data_from_rows(
        artifact_kind=source_kinds[-1] if len(set(source_kinds)) == 1 else "official_derived",
        artifact_id=source_artifact_ids[-1] if len(source_artifact_ids) == 1 else "official_derived",
        variable_name=REFRESH_METADATA.get("name", ""),
        headers=transformed_headers,
        rows=transformed_rows,
        transformation_logic=transformation_logic,
        source={{
            "validated_api_url": REFRESH_METADATA.get("validated_api_url", ""),
            "api_request_urls": REFRESH_METADATA.get("api_request_urls", []),
        }},
    )


if __name__ == "__main__":
    print(json.dumps(refresh(), ensure_ascii=False))
'''


def _build_refresh_metadata(
    *,
    name: str = "",
    validated_api_url: str,
    retrieval_logic: Dict[str, Any],
    transformation_logic: Dict[str, Any],
    transform_summary: str,
    recreation_summary: str,
) -> Dict[str, Any]:
    steps = []
    for index, step in enumerate(_normalize_replay_steps(retrieval_logic), start=1):
        steps.append(
            {
                "id": _as_text(step.get("id")) or f"step_{index}",
                "tool": _as_text(step.get("tool")),
                "args": step.get("args") if isinstance(step.get("args"), dict) else {},
                "source_id": _as_text(step.get("source_id")),
            }
        )
    return {
        "version": 1,
        "kind": "validated_variable_refresh_metadata",
        "name": _as_text(name),
        "validated_api_url": _as_text(validated_api_url),
        "api_request_urls": retrieval_logic.get("api_request_urls") if isinstance(retrieval_logic.get("api_request_urls"), list) else [],
        "steps": steps,
        "transformation_logic": transformation_logic,
        "transform_summary": _as_text(transform_summary),
        "recreation_summary": _as_text(recreation_summary),
    }


def _with_variable_contents_metadata(
    *,
    name: str,
    label: str,
    source_name: str,
    provider_id: str,
    dataset_id: str,
    metric: str,
    unit: str,
    geography: str,
    frequency: str,
    seasonal_treatment: str,
    period_start: str,
    period_end: str,
    validated_api_url: str,
    retrieval_logic: Dict[str, Any],
    transformation_logic: Dict[str, Any],
    transform_summary: str,
    recreation_summary: str,
    evidence_artifact: Dict[str, Any],
    node_description: str = "",
) -> Dict[str, Any]:
    enriched = dict(evidence_artifact or {})
    enriched["replay_package"] = _build_replay_package(
        validated_api_url=validated_api_url,
        retrieval_logic=retrieval_logic,
        transformation_logic=transformation_logic,
        transform_summary=transform_summary,
        recreation_summary=recreation_summary,
        evidence_artifact=enriched,
    )
    metadata = _build_variable_contents_metadata(
        name=name,
        label=label,
        source_name=source_name,
        provider_id=provider_id,
        dataset_id=dataset_id,
        metric=metric,
        unit=unit,
        geography=geography,
        frequency=frequency,
        seasonal_treatment=seasonal_treatment,
        period_start=period_start,
        period_end=period_end,
        validated_api_url=validated_api_url,
        retrieval_logic=retrieval_logic,
        transformation_logic=transformation_logic,
        transform_summary=transform_summary,
        recreation_summary=recreation_summary,
        evidence_artifact=enriched,
        node_description=node_description,
    )
    enriched.update(metadata)
    return enriched


def _lookup_path(value: Any, path: str) -> Any:
    current = value
    for part in path.split("."):
        if isinstance(current, dict):
            current = current.get(part)
        elif isinstance(current, list) and part.isdigit():
            index = int(part)
            current = current[index] if 0 <= index < len(current) else None
        else:
            return None
    return current


def _interpolate(value: Any, outputs: Dict[str, Any]) -> Any:
    if isinstance(value, str):
        if value.startswith("${") and value.endswith("}"):
            token = value[2:-1].strip()
            if token == "latest_artifact_id":
                return outputs.get("_latest_artifact_id") or ""
            if "." in token:
                step_id, path = token.split(".", 1)
                return _lookup_path(outputs.get(step_id), path) or ""
            return outputs.get(token) or ""
        return value
    if isinstance(value, dict):
        return {key: _interpolate(item, outputs) for key, item in value.items()}
    if isinstance(value, list):
        return [_interpolate(item, outputs) for item in value]
    return value


def _extract_artifact_id(payload: Any) -> str:
    if not isinstance(payload, dict):
        return ""
    for key in ("artifact_id", "artifactId", "id"):
        value = _as_text(payload.get(key))
        if value:
            return value
    artifacts = payload.get("artifacts")
    if isinstance(artifacts, list) and artifacts:
        first = artifacts[0]
        if isinstance(first, dict):
            return _as_text(first.get("artifact_id")) or _as_text(first.get("artifactId")) or _as_text(first.get("id"))
    jobs = payload.get("jobs")
    if isinstance(jobs, list):
        for job in jobs:
            if isinstance(job, dict):
                artifact_id = _extract_artifact_id(job.get("result"))
                if artifact_id:
                    return artifact_id
    return ""


def _summarize_replay_output(payload: Any) -> Dict[str, Any]:
    if not isinstance(payload, dict):
        return {"value": payload}
    summary: Dict[str, Any] = {}
    for key in (
        "artifact_id",
        "artifactId",
        "kind",
        "label",
        "dataset_id",
        "datasetId",
        "row_count",
        "series_count",
        "analysis_file",
        "download_filename",
        "summary",
        "columns",
        "dimensions",
    ):
        if key in payload:
            summary[key] = payload.get(key)
    if "jobs" in payload and isinstance(payload.get("jobs"), list):
        summary["jobs"] = [
            {
                "ok": job.get("ok"),
                "datasetId": job.get("datasetId"),
                "artifact_id": _extract_artifact_id(job.get("result")),
            }
            for job in payload.get("jobs", [])
            if isinstance(job, dict)
        ]
    return summary or {"keys": sorted(str(key) for key in payload.keys())[:20]}


@contextmanager
def _mcp_replay_context(conversation_id: str = "", code_container_id: str = ""):
    from . import unified_mcp_server as mcp_tools

    previous_conversation_id = mcp_tools.CONVERSATION_ID
    previous_code_container_id = mcp_tools.CODE_CONTAINER_ID
    if conversation_id:
        mcp_tools.CONVERSATION_ID = conversation_id
    if code_container_id:
        mcp_tools.CODE_CONTAINER_ID = code_container_id
    try:
        yield mcp_tools
    finally:
        mcp_tools.CONVERSATION_ID = previous_conversation_id
        mcp_tools.CODE_CONTAINER_ID = previous_code_container_id


def _replay_mcp_tool(mcp_tools: Any, tool: str, args: Dict[str, Any]) -> Dict[str, Any]:
    tool_map = {
        "retrieve": mcp_tools.retrieve,
        "narrow_artifact": mcp_tools.narrow_artifact,
    }
    if tool not in tool_map:
        raise RuntimeError(f"Validated-variable replay does not allow tool '{tool}'.")
    return tool_map[tool](**args)


def _row_dicts(headers: list[str], rows: list[list[Any]]) -> list[Dict[str, Any]]:
    return [
        {headers[index]: row[index] for index in range(min(len(headers), len(row)))}
        for row in rows
    ]


def _period_sort_key(row: Dict[str, Any]) -> str:
    for key in ("observationKey", "x", "TIME_PERIOD", "TIME"):
        value = _as_text(row.get(key))
        if value:
            return value
    return ""


def _analysis_table_for_artifact(mcp_tools: Any, artifact_id: str, row_limit: int = 5000) -> Dict[str, Any]:
    clean_artifact_id = _as_text(artifact_id)
    if not clean_artifact_id:
        return {}
    payload = mcp_tools._load_artifact_payload(clean_artifact_id)
    kind = mcp_tools._artifact_kind(clean_artifact_id, payload)
    if kind.startswith("domestic"):
        headers, rows = mcp_tools._flatten_domestic_payload(payload)
    elif kind.startswith("macro"):
        headers, rows = mcp_tools._flatten_macro_payload(payload)
    else:
        return {"artifact_id": clean_artifact_id, "kind": kind, "available": False}
    returned_rows = rows[: max(0, int(row_limit))]
    returned_dicts = _row_dicts(headers, returned_rows)
    all_dicts = _row_dicts(headers, rows)
    latest_rows = sorted(all_dicts, key=_period_sort_key)[-20:]
    return {
        "artifact_id": clean_artifact_id,
        "kind": kind,
        "headers": headers,
        "row_count": len(rows),
        "rows_returned": len(returned_rows),
        "rows_truncated": len(rows) > len(returned_rows),
        "rows": returned_dicts,
        "latest_rows": latest_rows,
        "payload_manifest": mcp_tools._summary(payload),
        "instruction": (
            "Rows come from the final replay artifact. If rows_truncated is true, use artifact_id or "
            "analysis_file for full-series calculation rather than expanding the whole table into chat context."
        ),
    }


def _normalize_replay_steps(retrieval_logic: Dict[str, Any]) -> list[Dict[str, Any]]:
    api_calls = retrieval_logic.get("api_calls")
    if isinstance(api_calls, list):
        steps = []
        for index, api_call in enumerate(api_calls, start=1):
            if isinstance(api_call, dict):
                steps.append(
                    {
                        "id": _as_text(api_call.get("id")) or f"api_call_{index}",
                        "tool": _as_text(api_call.get("tool")) or "retrieve",
                        "args": api_call.get("args") if isinstance(api_call.get("args"), dict) else {},
                    }
                )
        for index, narrow_step in enumerate(retrieval_logic.get("narrow_steps") or [], start=1):
            if isinstance(narrow_step, dict):
                args = dict(narrow_step.get("args") if isinstance(narrow_step.get("args"), dict) else {})
                args.setdefault("artifactId", "${latest_artifact_id}")
                steps.append(
                    {
                        "id": _as_text(narrow_step.get("id")) or f"narrow_{index}",
                        "tool": "narrow_artifact",
                        "args": args,
                    }
                )
        return steps

    api_call = retrieval_logic.get("api_call")
    if isinstance(api_call, dict):
        steps = [
            {
                "id": _as_text(api_call.get("id")) or "api_call",
                "tool": _as_text(api_call.get("tool")) or "retrieve",
                "args": api_call.get("args") if isinstance(api_call.get("args"), dict) else {},
            }
        ]
        narrow = retrieval_logic.get("narrow")
        if isinstance(narrow, dict):
            args = dict(narrow.get("args") if isinstance(narrow.get("args"), dict) else {})
            args.setdefault("artifactId", "${latest_artifact_id}")
            steps.append(
                {
                    "id": _as_text(narrow.get("id")) or "narrow",
                    "tool": "narrow_artifact",
                    "args": args,
                }
            )
        return steps

    steps = retrieval_logic.get("steps")
    if isinstance(steps, list):
        return [step for step in steps if isinstance(step, dict)]
    tool = _as_text(retrieval_logic.get("tool"))
    args = retrieval_logic.get("args")
    if tool and isinstance(args, dict):
        return [{"id": tool, "tool": tool, "args": args}]
    return []


def _validate_validated_recipe(
    *,
    retrieval_logic: Dict[str, Any],
    transformation_logic: Dict[str, Any],
) -> None:
    if _as_text(retrieval_logic.get("kind")) == "research_derived":
        if not isinstance(transformation_logic, dict) or not any(key in transformation_logic for key in TRANSFORMATION_KEYS):
            raise RuntimeError(
                "Research-derived validated variables must include transformation_logic with transform_code, code, formula, or steps."
            )
        return
    steps = _normalize_replay_steps(retrieval_logic)
    if not steps:
        raise RuntimeError(
            "Validated variables must save the approved API/MCP call as retrieval_logic.api_call "
            "or retrieval_logic.steps."
        )
    if _as_text(steps[0].get("tool")) != "retrieve":
        raise RuntimeError("A validated-variable recipe must start with the approved retrieve API/MCP call.")
    if not any(_as_text(step.get("tool")) == "narrow_artifact" for step in steps[1:]):
        raise RuntimeError(
            "A validated-variable recipe must include a narrow_artifact step after retrieve. "
            "Raw retrieve-only recipes are inspect-only and cannot be saved as validated variables."
        )
    for index, step in enumerate(steps, start=1):
        tool = _as_text(step.get("tool"))
        if tool not in ALLOWED_MCP_REPLAY_TOOLS:
            raise RuntimeError(
                f"Validated-variable recipe step {index} uses '{tool}'. "
                "Only the approved retrieve API/MCP call and required narrow_artifact call are allowed."
            )
        if not isinstance(step.get("args"), dict) or not step.get("args"):
            raise RuntimeError(f"Validated-variable recipe step {index} must include concrete saved args.")
    if not isinstance(transformation_logic, dict) or not any(key in transformation_logic for key in TRANSFORMATION_KEYS):
        raise RuntimeError(
            "Validated variables must include transformation_logic with transform_code, code, formula, or steps. "
            "For a direct series, save an identity transform such as transform_code='return the narrowed series unchanged'."
        )
    transform_code = _as_text(transformation_logic.get("code") or transformation_logic.get("transform_code"))
    identity_markers = {
        "return the narrowed series unchanged",
        "identity",
        "no transformation",
        "return rows unchanged",
    }
    has_prose_transform = bool(transformation_logic.get("formula") or transformation_logic.get("steps"))
    if has_prose_transform and not transform_code:
        raise RuntimeError(
            "Derived validated variables must include executable Python in transformation_logic.code or "
            "transformation_logic.transform_code. Prose formulas/steps alone are not refreshable."
        )
    if transform_code and transform_code.lower() not in identity_markers and "def transform" not in transform_code:
        raise RuntimeError(
            "Executable transformation code must define transform(rows, metadata) and return a list of row dictionaries."
        )


def _matched_supported_official_source(*parts: Any) -> str:
    text = "\n".join(_as_text(part).lower() for part in parts)
    tokens = set(re.split(r"[^a-z0-9]+", text))
    for label, markers in SUPPORTED_OFFICIAL_SOURCE_MARKERS.items():
        for marker in markers:
            marker_text = marker.lower()
            if " " in marker_text or "." in marker_text:
                if marker_text in text:
                    return label
            elif marker_text in tokens:
                return label
    return ""


def _assert_official_source_refresh_contract(
    *,
    source_name: str,
    provider_id: str,
    dataset_id: str,
    validated_api_url: str,
    retrieval_logic: Dict[str, Any],
    refresh_metadata: Dict[str, Any],
) -> None:
    matched_source = _matched_supported_official_source(
        source_name,
        provider_id,
        dataset_id,
        validated_api_url,
        json.dumps(retrieval_logic, ensure_ascii=False, default=str),
        json.dumps(refresh_metadata, ensure_ascii=False, default=str),
    )
    if not matched_source:
        return
    if _as_text(retrieval_logic.get("kind")) == "research_derived" or _as_text(refresh_metadata.get("kind")).startswith("research_derived"):
        raise RuntimeError(
            f"{matched_source} validated variables must be saved from MCP retrieve/narrow_artifact steps, "
            "not research-derived/custom-data packages. Official-source refresh_code must replay the live source."
        )


def _validated_data_from_analysis_table(
    *,
    row: Dict[str, Any],
    analysis_table: Dict[str, Any],
    transformation_logic: Dict[str, Any],
) -> Dict[str, Any]:
    rows = analysis_table.get("rows") if isinstance(analysis_table.get("rows"), list) else []
    headers = analysis_table.get("headers") if isinstance(analysis_table.get("headers"), list) else []
    if not headers or not rows:
        raise RuntimeError("Refresh did not produce compact validated data rows.")
    transformed_rows = apply_transformation_rows([item for item in rows if isinstance(item, dict)], transformation_logic)
    transformed_headers = list(transformed_rows[0].keys()) if transformed_rows else [_as_text(header) for header in headers]
    return compact_validated_data_from_rows(
        artifact_kind=_as_text(analysis_table.get("kind")),
        artifact_id=_as_text(analysis_table.get("artifact_id")),
        variable_name=_as_text(row.get("name")),
        headers=transformed_headers,
        rows=transformed_rows,
        transformation_logic=transformation_logic,
        source={"validated_api_url": _as_text(row.get("validated_api_url"))},
    )


def _execute_refresh_code(*, row: Dict[str, Any]) -> Dict[str, Any]:
    refresh_code = _as_text(row.get("refresh_code"))
    if not refresh_code:
        raise RuntimeError("This validated variable has no saved refresh_code.")
    existing_variable = {
        "id": _as_text(row.get("id")),
        "name": _as_text(row.get("name")),
        "label": _as_text(row.get("label")),
        "validated_data": row.get("validated_data") if isinstance(row.get("validated_data"), dict) else {},
        "period_start": _as_text(row.get("period_start")),
        "period_end": _as_text(row.get("period_end")),
        "metadata": {
            "source_name": _as_text(row.get("source_name")),
            "provider_id": _as_text(row.get("provider_id")),
            "dataset_id": _as_text(row.get("dataset_id")),
            "metric": _as_text(row.get("metric")),
            "unit": _as_text(row.get("unit")),
            "geography": _as_text(row.get("geography")),
            "frequency": _as_text(row.get("frequency")),
            "seasonal_treatment": _as_text(row.get("seasonal_treatment")),
            "validated_api_url": _as_text(row.get("validated_api_url")),
        },
    }
    globals_scope: Dict[str, Any] = {"__name__": "validated_variable_refresh"}
    exec(refresh_code, globals_scope, globals_scope)
    refresh_fn = globals_scope.get("refresh")
    if not callable(refresh_fn):
        raise RuntimeError("Saved refresh_code must define refresh(existing_variable=None).")
    try:
        refreshed = refresh_fn(existing_variable=existing_variable)
    except TypeError:
        refreshed = refresh_fn()
    if not isinstance(refreshed, dict):
        raise RuntimeError("Saved refresh_code must return compact validated_data as a dictionary.")
    rows = validated_data_rows(refreshed)
    headers = validated_data_headers(refreshed)
    if not rows or not headers:
        raise RuntimeError("Saved refresh_code returned invalid compact data.")
    return refreshed


def _refresh_reproducibility_payload(validated_data: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "columns": validated_data.get("columns") if isinstance(validated_data.get("columns"), list) else [],
        "records": validated_data.get("records") if isinstance(validated_data.get("records"), list) else [],
        "dimensions": validated_data.get("dimensions") if isinstance(validated_data.get("dimensions"), dict) else {},
        "source_columns": validated_data.get("source_columns") if isinstance(validated_data.get("source_columns"), list) else [],
        "period_key": _as_text(validated_data.get("period_key")),
        "value_key": _as_text(validated_data.get("value_key")),
        "row_count": validated_data.get("row_count"),
    }


def _assert_refresh_code_reproduces_validated_data(
    *,
    refresh_code: str,
    validated_data: Dict[str, Any],
    variable_row: Dict[str, Any],
) -> None:
    test_row = dict(variable_row)
    test_row["refresh_code"] = refresh_code
    test_row["validated_data"] = validated_data
    refreshed = _execute_refresh_code(row=test_row)
    expected = _refresh_reproducibility_payload(validated_data)
    actual = _refresh_reproducibility_payload(refreshed)
    if actual != expected:
        raise RuntimeError(
            "Generated refresh_code does not reproduce the approved compact validated_data. "
            f"Expected columns={expected.get('columns')} row_count={expected.get('row_count')}; "
            f"got columns={actual.get('columns')} row_count={actual.get('row_count')}. "
            "Regenerate the variable from the exact executed retrieve, narrow, and transform code before saving."
        )


def _assert_refresh_code_compiles(refresh_code: str) -> None:
    globals_scope: Dict[str, Any] = {"__name__": "validated_variable_refresh_check"}
    exec(refresh_code, globals_scope, globals_scope)
    if not callable(globals_scope.get("refresh")):
        raise RuntimeError("Saved refresh_code must define refresh(existing_variable=None).")


def _refresh_validated_variable_data(
    *,
    row: Dict[str, Any],
    conversation_id: str,
    code_container_id: str,
) -> Dict[str, Any]:
    del conversation_id, code_container_id
    validated_data = _execute_refresh_code(row=row)
    inferred_period_start, inferred_period_end = _infer_period_range_from_validated_data(validated_data)
    period_start = inferred_period_start or _as_text(row.get("period_start"))
    period_end = inferred_period_end or _as_text(row.get("period_end"))
    contents_metadata = _build_variable_contents_metadata(
        name=_as_text(row.get("name")),
        label=_as_text(row.get("label")),
        source_name=_as_text(row.get("source_name")),
        provider_id=_as_text(row.get("provider_id")),
        dataset_id=_as_text(row.get("dataset_id")),
        metric=_as_text(row.get("metric")),
        unit=_as_text(row.get("unit")),
        geography=_as_text(row.get("geography")),
        frequency=_as_text(row.get("frequency")),
        seasonal_treatment=_as_text(row.get("seasonal_treatment")),
        period_start=period_start,
        period_end=period_end,
        validated_api_url=_as_text(row.get("validated_api_url")),
        retrieval_logic={},
        transformation_logic={},
        transform_summary=_as_text(row.get("transform_summary")),
        recreation_summary="",
        evidence_artifact={},
        node_description=_as_text(row.get("node_description")),
    )
    with _connect() as conn:
        conn.execute(
            """
            update public.validated_variables
            set validated_data = %s,
                period_start = %s,
                period_end = %s,
                contents_summary = %s,
                updated_at = now()
            where id = %s and user_id = %s
            """,
            (
                Json(_jsonable(validated_data)),
                period_start,
                period_end,
                contents_metadata["contents_summary"],
                row.get("id"),
                row.get("user_id"),
            ),
        )
        conn.commit()
    return validated_data


def _active_project_refs_for_variable(conn, *, user_id: str, variable_id: str) -> list[Dict[str, Any]]:
    rows = conn.execute(
        """
        select id, name
        from public.modelling_projects
        where user_id = %s
          and active_validated_variable_ids ? %s
        order by updated_at desc
        """,
        (user_id, variable_id),
    ).fetchall()
    return [dict(row) for row in rows]


def _sync_saved_variable_to_project_state(
    conn,
    *,
    user_id: str,
    project_id: str,
    variable_id: str,
    variable: Dict[str, Any],
    validated_data: Dict[str, Any],
    node_description: str,
    contents_summary: str,
) -> None:
    project = conn.execute(
        """
        select model_builder_state, model_graph_state
        from public.modelling_projects
        where id = %s and user_id = %s
        limit 1
        """,
        (project_id, user_id),
    ).fetchone()
    if not project:
        return
    state = project.get("model_builder_state") if isinstance(project.get("model_builder_state"), dict) else {}
    graph = project.get("model_graph_state") if isinstance(project.get("model_graph_state"), dict) else {}
    variables = state.get("variables") if isinstance(state.get("variables"), list) else []
    nodes = graph.get("nodes") if isinstance(graph.get("nodes"), list) else state.get("nodes")
    nodes = nodes if isinstance(nodes, list) else []
    edges = graph.get("edges") if isinstance(graph.get("edges"), list) else state.get("edges")
    edges = edges if isinstance(edges, list) else []

    variable_payload = {
        "id": variable_id,
        "name": _as_text(variable.get("name")) or variable_id,
        "label": _as_text(variable.get("label")) or _as_text(variable.get("name")) or variable_id,
        "sourceName": _as_text(variable.get("source_name")),
        "metric": _as_text(variable.get("metric")),
        "unit": _as_text(variable.get("unit")),
        "geography": _as_text(variable.get("geography")),
        "frequency": _as_text(variable.get("frequency")),
        "seasonalTreatment": _as_text(variable.get("seasonal_treatment")),
        "periodStart": _as_text(variable.get("period_start")),
        "periodEnd": _as_text(variable.get("period_end")),
        "transformSummary": _as_text(variable.get("transform_summary")),
        "nodeDescription": _as_text(node_description),
        "contentsSummary": _as_text(contents_summary),
        "contents": validated_data,
        "validationStatus": "validated",
    }
    next_variables = []
    replaced_variable = False
    for item in variables:
        if isinstance(item, dict) and _as_text(item.get("id")) == variable_id:
            next_variables.append(variable_payload)
            replaced_variable = True
        else:
            next_variables.append(item)
    if not replaced_variable:
        next_variables.append(variable_payload)

    variable_node_count = sum(1 for node in nodes if isinstance(node, dict) and _as_text(node.get("nodeType")) == "variable")
    next_nodes = []
    replaced_node = False
    variable_node_id = variable_id
    for node in nodes:
        if isinstance(node, dict) and (_as_text(node.get("variableId")) == variable_id or _as_text(node.get("id")) == variable_id):
            updated = dict(node)
            updated["node_title"] = variable_payload["label"]
            updated["node_description"] = variable_payload["nodeDescription"]
            updated["nodeType"] = "variable"
            updated["variableId"] = variable_id
            updated.pop("label", None)
            updated.pop("description", None)
            updated.pop("nodeDescription", None)
            variable_node_id = _as_text(updated.get("id")) or variable_id
            next_nodes.append(updated)
            replaced_node = True
        else:
            next_nodes.append(node)
    if not replaced_node:
        next_nodes.append(
            {
                "id": variable_id,
                "node_title": variable_payload["label"],
                "node_description": variable_payload["nodeDescription"],
                "nodeType": "variable",
                "variableId": variable_id,
                "positionX": 80 + (variable_node_count % 2) * 390,
                "positionY": 80 + (variable_node_count // 2) * 210,
            }
        )

    next_state = {
        "variables": next_variables,
        "nodes": next_nodes,
        "edges": edges,
    }
    next_graph = {"nodes": next_nodes, "edges": edges}
    node_data_entry = _node_chart_data_from_validated_variable(
        node_id=variable_node_id,
        node_title=variable_payload["label"],
        unit=variable_payload["unit"],
        validated_data=validated_data,
    )
    conn.execute(
        """
        update public.modelling_projects
        set model_builder_state = %s,
            model_graph_state = %s,
            node_data = jsonb_set(
              coalesce(node_data, '{}'::jsonb),
              array[%s]::text[],
              %s::jsonb,
              true
            ),
            updated_at = now()
        where id = %s and user_id = %s
        """,
        (
            Json(_jsonable(next_state)),
            Json(_jsonable(next_graph)),
            variable_node_id,
            Json(_jsonable(node_data_entry)),
            project_id,
            user_id,
        ),
    )


def list_validated_variable_records(
    *,
    user_id: str,
    project_id: str = "",
    query: str = "",
    limit: int = 25,
) -> Dict[str, Any]:
    user_id = _as_text(user_id)
    project_id = _as_text(project_id)
    query = _as_text(query)
    if not user_id:
        raise RuntimeError("A Supabase user_id is required to list validated variables.")
    limit = max(1, min(int(limit or 25), 100))
    pattern = f"%{query}%"
    with _connect() as conn:
        rows = conn.execute(
            """
            select
              vv.id,
              vv.name,
              vv.label,
              vv.source_name,
              vv.metric,
              vv.unit,
              vv.geography,
              vv.frequency,
              vv.seasonal_treatment,
              vv.period_start,
              vv.period_end,
              vv.updated_at,
              vv.node_description,
              vv.contents_summary,
              coalesce(active_refs.active_project_count, 0) as active_project_count,
              coalesce(active_refs.active_projects, '[]'::jsonb) as active_projects,
              case
                when %(project_id)s = '' then false
                else coalesce(mp.active_validated_variable_ids ? vv.id::text, false)
              end as active_in_project
            from public.validated_variables vv
            left join public.modelling_projects mp
              on mp.id = %(project_id)s and mp.user_id = %(user_id)s
            left join lateral (
              select
                count(*)::int as active_project_count,
                jsonb_agg(jsonb_build_object('id', active_mp.id, 'name', active_mp.name) order by active_mp.updated_at desc) as active_projects
              from public.modelling_projects active_mp
              where active_mp.user_id = vv.user_id
                and active_mp.active_validated_variable_ids ? vv.id::text
            ) active_refs on true
            where vv.user_id = %(user_id)s
              and (
                %(query)s = ''
                or vv.name ilike %(pattern)s
                or vv.label ilike %(pattern)s
                or vv.metric ilike %(pattern)s
                or vv.source_name ilike %(pattern)s
                or vv.geography ilike %(pattern)s
              )
            order by active_in_project desc, vv.updated_at desc
            limit %(limit)s
            """,
            {
                "user_id": user_id,
                "project_id": project_id,
                "query": query,
                "pattern": pattern,
                "limit": limit,
            },
        ).fetchall()
    return {
        "variables": [dict(row) for row in rows],
        "count": len(rows),
        "instruction": (
            "Use these ids to distinguish updating an existing validated variable from creating a new one. "
            "If more than one result could match the user's request, ask which variable to update before saving."
        ),
    }


def save_validated_variable_record(
    *,
    user_id: str,
    project_id: str,
    name: str,
    label: str = "",
    source_name: str = "",
    provider_id: str = "",
    dataset_id: str = "",
    metric: str = "",
    unit: str = "",
    geography: str = "",
    frequency: str = "",
    seasonal_treatment: str = "",
    period_start: str = "",
    period_end: str = "",
    validated_api_url: str = "",
    retrieval_logic: Dict[str, Any] | None = None,
    transformation_logic: Dict[str, Any] | None = None,
    transform_summary: str = "",
    recreation_summary: str = "",
    node_description: str = "",
    validated_data: Dict[str, Any] | None = None,
    refresh_code: str = "",
    refresh_metadata: Dict[str, Any] | None = None,
    evidence_artifact: Dict[str, Any] | None = None,
    external_key: str = "",
    update_variable_id: str = "",
    allow_shared_update: bool = False,
    validate_refresh_execution: bool = True,
) -> Dict[str, Any]:
    user_id = _as_text(user_id)
    project_id = _as_text(project_id)
    if not user_id or not project_id:
        raise RuntimeError("A Supabase user_id and active project_id are required to save a validated variable.")

    retrieval_logic = retrieval_logic or {}
    transformation_logic = transformation_logic or {}
    validated_data = validated_data or {}
    evidence_artifact = evidence_artifact or {}
    validated_api_url = _as_text(validated_api_url) or _extract_validated_api_url(
        retrieval_logic,
        evidence_artifact,
    )
    if not validated_api_url:
        raise RuntimeError(
            "Validated variables must save the working validated_api_url from the API/MCP call that passed validation."
        )
    if not isinstance(validated_data, dict):
        raise RuntimeError("Validated variables must save compact validated_data.")
    saved_rows = validated_data_rows(validated_data)
    saved_headers = validated_data_headers(validated_data)
    if not saved_headers or not saved_rows:
        raise RuntimeError("Validated variables must save compact validated_data with reconstructable columns and records.")
    if not _as_text(node_description):
        raise RuntimeError(
            "Validated variables must save a fresh node_description whenever data or recipe is saved. "
            "Update the note to match the current source, date range, transformation, and project meaning."
        )
    inferred_period_start, inferred_period_end = _infer_period_range_from_validated_data(validated_data)
    clean_period_start = inferred_period_start or _as_text(period_start)
    clean_period_end = inferred_period_end or _as_text(period_end)
    refresh_metadata = refresh_metadata or _build_refresh_metadata(
        name=name or label,
        validated_api_url=validated_api_url,
        retrieval_logic=retrieval_logic,
        transformation_logic=transformation_logic,
        transform_summary=transform_summary,
        recreation_summary=recreation_summary,
    )
    refresh_code = _as_text(refresh_code) or _build_refresh_code(refresh_metadata)
    if not refresh_code:
        raise RuntimeError("Validated variables must save executable refresh_code.")
    contents_metadata = _build_variable_contents_metadata(
        name=name,
        label=label,
        source_name=source_name,
        provider_id=provider_id,
        dataset_id=dataset_id,
        metric=metric,
        unit=unit,
        geography=geography,
        frequency=frequency,
        seasonal_treatment=seasonal_treatment,
        period_start=clean_period_start,
        period_end=clean_period_end,
        validated_api_url=validated_api_url,
        retrieval_logic=retrieval_logic,
        transformation_logic=transformation_logic,
        transform_summary=transform_summary,
        recreation_summary=recreation_summary,
        evidence_artifact=evidence_artifact,
        node_description=node_description,
    )
    _validate_validated_recipe(
        retrieval_logic=retrieval_logic,
        transformation_logic=transformation_logic,
    )
    _assert_official_source_refresh_contract(
        source_name=source_name,
        provider_id=provider_id,
        dataset_id=dataset_id,
        validated_api_url=validated_api_url,
        retrieval_logic=retrieval_logic,
        refresh_metadata=refresh_metadata,
    )
    clean_update_variable_id = _as_text(update_variable_id)
    if validate_refresh_execution:
        _assert_refresh_code_reproduces_validated_data(
            refresh_code=refresh_code,
            validated_data=validated_data,
            variable_row={
                "id": clean_update_variable_id,
                "name": _as_text(name) or _as_text(label),
                "label": _as_text(label) or _as_text(name),
                "source_name": _as_text(source_name),
                "provider_id": _as_text(provider_id),
                "dataset_id": _as_text(dataset_id),
                "metric": _as_text(metric),
                "unit": _as_text(unit),
                "geography": _as_text(geography),
                "frequency": _as_text(frequency),
                "seasonal_treatment": _as_text(seasonal_treatment),
                "period_start": clean_period_start,
                "period_end": clean_period_end,
                "validated_api_url": validated_api_url,
            },
        )
    else:
        _assert_refresh_code_compiles(refresh_code)

    payload_for_key = {
        "name": name,
        "provider_id": provider_id,
        "dataset_id": dataset_id,
        "metric": metric,
        "unit": unit,
        "geography": geography,
        "frequency": frequency,
        "seasonal_treatment": seasonal_treatment,
        "period_start": clean_period_start,
        "period_end": clean_period_end,
        "validated_api_url": validated_api_url,
        "retrieval_logic": retrieval_logic,
        "transformation_logic": transformation_logic,
    }
    resolved_key = _as_text(external_key) or _external_key(payload_for_key)

    with _connect() as conn:
        params = {
            "user_id": user_id,
            "project_id": project_id,
            "external_key": resolved_key,
            "name": _as_text(name) or _as_text(label) or "Validated variable",
            "label": _as_text(label) or _as_text(name) or "Validated variable",
            "source_name": _as_text(source_name),
            "provider_id": _as_text(provider_id),
            "dataset_id": _as_text(dataset_id),
            "metric": _as_text(metric),
            "unit": _as_text(unit),
            "geography": _as_text(geography),
            "frequency": _as_text(frequency),
            "seasonal_treatment": _as_text(seasonal_treatment),
            "period_start": clean_period_start,
            "period_end": clean_period_end,
            "validated_api_url": validated_api_url,
            "transform_summary": _as_text(transform_summary),
            "node_description": contents_metadata["node_description"],
            "contents_summary": contents_metadata["contents_summary"],
            "validated_data": Json(_jsonable(validated_data)),
            "refresh_code": refresh_code,
        }
        if clean_update_variable_id:
            existing_variable = conn.execute(
                """
                select id, name, label
                from public.validated_variables
                where id = %(update_variable_id)s and user_id = %(user_id)s
                limit 1
                """,
                {**params, "update_variable_id": clean_update_variable_id},
            ).fetchone()
            if not existing_variable:
                raise RuntimeError("No matching validated variable was found to update.")
            active_project_refs = _active_project_refs_for_variable(
                conn,
                user_id=user_id,
                variable_id=clean_update_variable_id,
            )
            if len(active_project_refs) > 1 and not allow_shared_update:
                return {
                    "saved": False,
                    "needs_confirmation": True,
                    "confirmation_type": "shared_validated_variable_update",
                    "variable": dict(existing_variable),
                    "active_project_count": len(active_project_refs),
                    "active_projects": active_project_refs,
                    "instruction": (
                        "This validated variable is active in more than one project. Warn the user that updating it "
                        "will affect every listed project, then ask whether to update the shared variable or create "
                        "a duplicate/new variable for this project. Only retry with allow_shared_update=true after "
                        "the user explicitly confirms the shared update."
                    ),
                }
            variable = conn.execute(
                """
                update public.validated_variables
                set project_id = %(project_id)s,
                    external_key = %(external_key)s,
                    name = %(name)s,
                    label = %(label)s,
                    source_name = %(source_name)s,
                    provider_id = %(provider_id)s,
                    dataset_id = %(dataset_id)s,
                    metric = %(metric)s,
                    unit = %(unit)s,
                    geography = %(geography)s,
                    frequency = %(frequency)s,
                    seasonal_treatment = %(seasonal_treatment)s,
                    period_start = %(period_start)s,
                    period_end = %(period_end)s,
                    validation_status = 'validated',
                    validated_api_url = %(validated_api_url)s,
                    transform_summary = %(transform_summary)s,
                    node_description = %(node_description)s,
                    contents_summary = %(contents_summary)s,
                    validated_data = %(validated_data)s,
                    refresh_code = %(refresh_code)s,
                    approved_by = %(user_id)s,
                    approved_at = now(),
                    updated_at = now()
                where id = %(update_variable_id)s and user_id = %(user_id)s
                returning id, external_key, name, label, source_name, metric, unit, validated_api_url,
                  transform_summary, validation_status, node_description, contents_summary,
                  validated_data->>'row_count' as row_count
                """,
                {**params, "update_variable_id": clean_update_variable_id},
            ).fetchone()
            if not variable:
                raise RuntimeError("No matching validated variable was found to update.")
        else:
            variable = conn.execute(
                """
                insert into public.validated_variables (
              user_id, project_id, origin_project_id, external_key, name, label,
              source_name, provider_id, dataset_id, metric, unit, geography,
              frequency, seasonal_treatment, period_start, period_end,
              validation_status, validated_api_url, transform_summary, node_description,
              contents_summary, validated_data, refresh_code, approved_by, approved_at
            )
            values (
              %(user_id)s, %(project_id)s, %(project_id)s, %(external_key)s, %(name)s, %(label)s,
              %(source_name)s, %(provider_id)s, %(dataset_id)s, %(metric)s, %(unit)s, %(geography)s,
              %(frequency)s, %(seasonal_treatment)s, %(period_start)s, %(period_end)s,
              'validated', %(validated_api_url)s, %(transform_summary)s, %(node_description)s,
              %(contents_summary)s, %(validated_data)s, %(refresh_code)s, %(user_id)s, now()
            )
            on conflict (user_id, external_key)
            do update set
              name = excluded.name,
              label = excluded.label,
              source_name = excluded.source_name,
              provider_id = excluded.provider_id,
              dataset_id = excluded.dataset_id,
              metric = excluded.metric,
              unit = excluded.unit,
              geography = excluded.geography,
              frequency = excluded.frequency,
              seasonal_treatment = excluded.seasonal_treatment,
              period_start = excluded.period_start,
              period_end = excluded.period_end,
              validation_status = 'validated',
              validated_api_url = excluded.validated_api_url,
              transform_summary = excluded.transform_summary,
              node_description = excluded.node_description,
              contents_summary = excluded.contents_summary,
              validated_data = excluded.validated_data,
              refresh_code = excluded.refresh_code,
              approved_by = excluded.approved_by,
              approved_at = now(),
              updated_at = now()
            returning id, external_key, name, label, source_name, metric, unit, validated_api_url,
              transform_summary, validation_status, node_description, contents_summary,
              validated_data->>'row_count' as row_count
            """,
                params,
            ).fetchone()
        conn.execute(
            """
            update public.modelling_projects
            set active_validated_variable_ids =
              case
                when active_validated_variable_ids ? %(variable_id)s then active_validated_variable_ids
                else active_validated_variable_ids || to_jsonb(array[%(variable_id)s]::text[])
              end,
              updated_at = now()
            where id = %(project_id)s and user_id = %(user_id)s
            """,
            {
                "user_id": user_id,
                "project_id": project_id,
                "variable_id": str(variable["id"]),
            },
        )
        _sync_saved_variable_to_project_state(
            conn,
            user_id=user_id,
            project_id=project_id,
            variable_id=str(variable["id"]),
            variable={
                **dict(variable),
                "geography": _as_text(geography),
                "frequency": _as_text(frequency),
                "seasonal_treatment": _as_text(seasonal_treatment),
                "period_start": clean_period_start,
                "period_end": clean_period_end,
                "transform_summary": _as_text(transform_summary),
            },
            validated_data=validated_data,
            node_description=contents_metadata["node_description"],
            contents_summary=contents_metadata["contents_summary"],
        )
        conn.commit()

    return {
        "saved": True,
        "variable": dict(variable),
        "active_in_project": True,
        "validated_data_saved": {
            "row_count": validated_data.get("row_count") or len(saved_rows),
            "rows_returned": len(saved_rows),
            "columns": saved_headers,
        },
        "instruction": (
            "The variable is now in the reusable validated-variable library and linked as active "
            "for the current project/model. Future runs should use the saved validated_data; use "
            "refresh_code only when the user asks to refresh the variable from source."
        ),
    }


def run_validated_variable_record(
    *,
    user_id: str,
    project_id: str,
    conversation_id: str = "",
    code_container_id: str = "",
    variable_id: str = "",
    external_key: str = "",
    name: str = "",
    refresh: bool = False,
) -> Dict[str, Any]:
    user_id = _as_text(user_id)
    project_id = _as_text(project_id)
    variable_id = _as_text(variable_id)
    external_key = _as_text(external_key)
    name = _as_text(name)
    if not user_id:
        raise RuntimeError("A Supabase user_id is required to run a validated variable.")

    with _connect() as conn:
        if variable_id:
            row = conn.execute(
                """
                select vv.*
                from public.validated_variables vv
                left join public.modelling_projects mp
                  on mp.id = %s and mp.user_id = %s
                where vv.user_id = %s and vv.id = %s
                  and (%s = '' or mp.active_validated_variable_ids ? vv.id::text)
                limit 1
                """,
                (project_id, user_id, user_id, variable_id, project_id),
            ).fetchone()
        elif external_key:
            row = conn.execute(
                """
                select vv.*
                from public.validated_variables vv
                left join public.modelling_projects mp
                  on mp.id = %s and mp.user_id = %s
                where vv.user_id = %s and vv.external_key = %s
                  and (%s = '' or mp.active_validated_variable_ids ? vv.id::text)
                limit 1
                """,
                (project_id, user_id, user_id, external_key, project_id),
            ).fetchone()
        else:
            row = conn.execute(
                """
                select vv.*
                from public.validated_variables vv
                left join public.modelling_projects mp
                  on mp.id = %s and mp.user_id = %s
                where vv.user_id = %s
                  and (%s = '' or mp.active_validated_variable_ids ? vv.id::text)
                  and lower(vv.name) = lower(%s)
                order by vv.updated_at desc
                limit 1
                """,
                (project_id, user_id, user_id, project_id, name),
            ).fetchone()

    if not row:
        raise RuntimeError("No matching active validated variable was found.")

    validated_data = row.get("validated_data") if isinstance(row.get("validated_data"), dict) else {}
    if refresh:
        validated_data = _refresh_validated_variable_data(
            row=row,
            conversation_id=conversation_id,
            code_container_id=code_container_id,
        )
    inflated_rows = validated_data_rows(validated_data)
    inflated_headers = validated_data_headers(validated_data)
    if not validated_data or not inflated_rows or not inflated_headers:
        raise RuntimeError(
            "This validated variable has not been saved in the new compact validated_data format. Revalidate it."
        )
    return {
        "variable": {
            "id": str(row.get("id")),
            "external_key": row.get("external_key"),
            "name": row.get("name"),
            "label": row.get("label"),
            "source_name": row.get("source_name"),
            "metric": row.get("metric"),
            "unit": row.get("unit"),
            "geography": row.get("geography"),
            "frequency": row.get("frequency"),
            "seasonal_treatment": row.get("seasonal_treatment"),
            "period_start": row.get("period_start"),
            "period_end": row.get("period_end"),
            "validated_api_url": row.get("validated_api_url"),
        },
        "validated_data": validated_data,
        "latest_analysis_table": {
            "kind": "validated_variable_analysis_table",
            "headers": inflated_headers,
            "rows": inflated_rows,
            "row_count": validated_data.get("row_count") or len(inflated_rows),
            "latest_rows": validated_data_latest_rows(validated_data),
        },
        "validated_api_url": row.get("validated_api_url") or "",
        "refresh_code": row.get("refresh_code") or "",
        "transform_summary": row.get("transform_summary") or "",
        "node_description": row.get("node_description") or "",
        "contents_summary": row.get("contents_summary") or "",
        "instruction": (
            "Use latest_analysis_table rows for analysis from the saved variable data. This is a saved-data read, not replay. "
            "Only execute refresh_code when the user explicitly asks to refresh/rerun from source; refresh_code can access existing_variable when refresh=True is used."
        ),
    }
