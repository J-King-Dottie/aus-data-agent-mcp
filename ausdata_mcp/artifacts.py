"""Persist complete retrieval evidence and return a bounded manifest."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from .runtime import RUNTIME_DIR, SESSION_ID

LARGE_ARTIFACT_BYTES = 50 * 1024 * 1024


def _clean_text(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _store_artifact(payload: dict[str, Any]) -> Path:
    payload.setdefault("retrieved_at", datetime.now(UTC).isoformat(timespec="milliseconds"))
    directory = RUNTIME_DIR / "sessions" / SESSION_ID / "artifacts"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"retrieval-{uuid4()}.json"
    temporary = path.with_suffix(".tmp")
    try:
        with temporary.open("w", encoding="utf-8") as file:
            json.dump(payload, file, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
    return path.resolve()


def store_retrieval(payload: dict[str, Any], dataset_id: str, label: str) -> dict[str, Any]:
    """Save complete evidence and describe it without returning the full dataset."""
    is_macro = isinstance(payload.get("selected_indicator"), dict)
    payload.setdefault("kind", "macro_retrieve" if is_macro else "domestic_retrieve")

    series_items = payload.get("series") if isinstance(payload.get("series"), list) else []
    period_start = period_end = None
    dimensions: set[str] = set()
    attributes: set[str] = set()
    units: set[str] = set()
    frequencies: set[str] = set()
    unit_multipliers: set[str] = set()
    preview: list[dict[str, Any]] = []
    row_count = missing_count = 0
    preview_dimensions_truncated = False

    for series in series_items:
        if not isinstance(series, dict):
            raise RuntimeError("Source returned an invalid series; evidence was not saved.")
        if is_macro:
            units.add(_clean_text(series.get("unit")))
            frequencies.add(_clean_text(series.get("frequency")))
            records = series.get("points") if isinstance(series.get("points"), list) else []
        else:
            dimensions.update((series.get("dimensions") or {}).keys())
            attributes.update((series.get("attributes") or {}).keys())
            records = (
                series.get("observations") if isinstance(series.get("observations"), list) else []
            )
        if not records:
            raise RuntimeError(
                "Source returned a series without observations; evidence was not saved."
            )
        for record in records:
            if not isinstance(record, dict):
                raise RuntimeError(
                    "Source returned an invalid observation; evidence was not saved."
                )
            row_count += 1
            record_dimensions = (
                record.get("dimensions") if isinstance(record.get("dimensions"), dict) else {}
            )
            if is_macro:
                period = _clean_text(record.get("x"))
            else:
                time_dimension = record_dimensions.get(
                    "TIME_PERIOD", (series.get("dimensions") or {}).get("TIME_PERIOD")
                )
                period = _clean_text(
                    time_dimension.get("code")
                    if isinstance(time_dimension, dict)
                    else time_dimension
                )
                period = period or _clean_text(record.get("observationKey"))
            if not period or ("y" if is_macro else "value") not in record:
                raise RuntimeError(
                    "Source returned an observation without a period or value field; evidence was not saved."
                )
            if period:
                period_start = min(period_start, period) if period_start else period
                period_end = max(period_end, period) if period_end else period
            value = record.get("y") if is_macro else record.get("value")
            missing_count += value is None
            if is_macro:
                if len(preview) < 3:
                    preview.append(
                        {
                            "country_code": series.get("country_code"),
                            "series_id": series.get("series_id"),
                            "period": period,
                            "value": value,
                            "unit": series.get("unit"),
                        }
                    )
                continue
            record_attributes = (
                record.get("attributes") if isinstance(record.get("attributes"), dict) else {}
            )
            dimensions.update(record_dimensions.keys())
            attributes.update(record_attributes.keys())
            for key in ("FREQ", "FREQUENCY"):
                frequency = record_attributes.get(key, (series.get("attributes") or {}).get(key))
                frequency = frequency or record_dimensions.get(
                    key, (series.get("dimensions") or {}).get(key)
                )
                if isinstance(frequency, dict):
                    frequency = frequency.get("code") or frequency.get("label")
                if frequency:
                    frequencies.add(str(frequency))
            for key in ("UNIT", "UNIT_MEASURE", "Unit of measure"):
                unit = record_attributes.get(key, (series.get("attributes") or {}).get(key))
                unit = unit or record_dimensions.get(key, (series.get("dimensions") or {}).get(key))
                if isinstance(unit, dict):
                    unit = unit.get("label") or unit.get("code")
                if _clean_text(unit):
                    units.add(_clean_text(unit))
            multiplier = record_attributes.get(
                "UNIT_MULT", (series.get("attributes") or {}).get("UNIT_MULT")
            )
            if isinstance(multiplier, dict):
                multiplier = multiplier.get("code", multiplier.get("label"))
            if _clean_text(multiplier):
                unit_multipliers.add(_clean_text(multiplier))
            if len(preview) < 3:
                preview_dimensions = {**(series.get("dimensions") or {}), **record_dimensions}
                preview_dimensions_truncated |= len(preview_dimensions) > 12 or any(
                    len(str(item.get("code") if isinstance(item, dict) else item)) > 80
                    for item in preview_dimensions.values()
                )
                preview.append(
                    {
                        "series_key": series.get("seriesKey"),
                        "period": period,
                        "value": value,
                        "dimension_codes": {
                            key: str(item.get("code") if isinstance(item, dict) else item)[:80]
                            for key, item in list(preview_dimensions.items())[:12]
                        },
                    }
                )

    if not row_count:
        raise RuntimeError("Source returned no observations; evidence was not saved.")
    path = _store_artifact(payload)
    references = (
        payload.get("source_references")
        if isinstance(payload.get("source_references"), list)
        else []
    )
    urls = (
        payload.get("api_request_urls") if isinstance(payload.get("api_request_urls"), list) else []
    )
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
        "size_notice": (
            f"Saved JSON is {artifact_size_mib:.2f} MiB ({row_count:,} observations). "
            "This is a large local file; inspect or process it selectively."
            if large_artifact
            else None
        ),
        "record_path": "series[*].points[*]" if is_macro else "series[*].observations[*]",
        "series_count": len(series_items),
        "row_count": row_count,
        "missing_value_count": missing_count,
        "period_start": period_start,
        "period_end": period_end,
        "dimension_ids": sorted(dimensions)
        if not is_macro
        else ["country_code", "series_id", "frequency", "unit"],
        "attribute_ids": sorted(attributes),
        "unit_examples": sorted(units - {""})[:10],
        "frequency_examples": sorted(frequencies - {""})[:10],
        "unit_multiplier_codes": sorted(unit_multipliers)[:10],
        "preview_rows": preview,
        "preview_truncated": row_count > len(preview),
        "summary_truncated": {
            "unit_examples": len(units - {""}) > 10,
            "frequency_examples": len(frequencies - {""}) > 10,
            "unit_multiplier_codes": len(unit_multipliers) > 10,
            "preview_dimension_codes": preview_dimensions_truncated,
        },
        "api_request_urls": urls[:5],
        "api_request_url_count": len(urls),
        "source_references": references[:5],
        "source_reference_count": len(references),
        "source_annotation_keys": sorted((payload.get("source_annotations") or {}).keys()),
        "value_semantics": payload.get("value_semantics"),
        "retrieved_at": payload["retrieved_at"],
        "source_fetched_at": payload.get("source_fetched_at", payload["retrieved_at"]),
        "source_cache_hit": payload.get("source_cache_hit", False),
    }
