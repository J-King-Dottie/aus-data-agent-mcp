"""Pacific Data Hub SDMX adapter, integrated into the shared MCP workflow.

Adapted from J-King-Dottie/pacific-data-hub-agent-mcp, commit
d18e2a7372198700e759436ea20a2df8403e44c2 (pdh_client.py). The original
catalogue/SDMX approach is retained; transport, validation and artifacts use
this project's contracts. No report renderer or duplicate MCP is required.
"""

from __future__ import annotations

import csv
import io
import math
import xml.etree.ElementTree as ET
from functools import lru_cache
from typing import Any
from urllib.parse import quote

from .data_config import get_data_settings
from .sdmx_structure import NS, SDMXStructureClient, _annotations, _identifier, _name
from .sdmx_structure import parse_dataset_id as _parse_dataset_id
from .selection import coverage_gaps, sdmx_selection

PROVIDER = "Pacific Data Hub"


def parse_dataset_id(dataset_id: str) -> tuple[str, str, str]:
    return _parse_dataset_id(dataset_id, "pdh")


class PacificDataService(SDMXStructureClient):
    metadata_kind = "pacific_metadata"

    def __init__(self):
        settings = get_data_settings()
        super().__init__(settings.pdh_base_url, settings.macro_timeout_seconds, "pdh", PROVIDER)

    def catalogue(self) -> list[dict[str, Any]]:
        # The unified catalogue owns caching and indexing for every source.
        xml = self._get("dataflow/SPC/all/latest", {"detail": "allstubs"}).text
        try:
            root = ET.fromstring(xml)
        except ET.ParseError as exc:
            raise RuntimeError("PDH returned invalid catalogue XML.") from exc
        flows = []
        for element in root.findall(".//structure:Dataflow", NS):
            agency = _identifier(element.get("agencyID", "SPC"))
            flow = _identifier(element.get("id", ""))
            version = _identifier(element.get("version", "latest"))
            annotations = _annotations(element)
            description = _name(element, "Description") or " ".join(
                value for values in annotations.values() for value in values
            )
            flows.append(
                {
                    "route": "pacific",
                    "provider": PROVIDER,
                    "datasetId": f"pdh::{agency}::{flow}::{version}",
                    "title": _name(element) or flow,
                    "description": description,
                    "sourceUrl": f"{self.base_url}/dataflow/{agency}/{flow}/{version}",
                    "searchText": f"{flow} {_name(element)} {description} Pacific Data Hub SPC Pacific islands",
                }
            )
        if not flows:
            raise RuntimeError(
                "PDH returned no catalogue entries; existing catalogue was not replaced."
            )
        return flows

    def retrieve(
        self,
        dataset_id: str,
        filters: dict[str, list[str]] | None = None,
        key: str = "",
        start: str = "",
        end: str = "",
        refresh: bool = False,
    ) -> dict[str, Any]:
        metadata = self.metadata(dataset_id, refresh)
        order = metadata["key_order"]
        resolved_key, selected = sdmx_selection(order, filters, key)
        if start and end and start > end and not start.startswith(end):
            raise ValueError("startPeriod must not be after endPeriod.")
        for dimension, values in selected.items():
            component = next(item for item in metadata["dimensions"] if item["id"] == dimension)
            if component["codelist"]:
                valid = {item["code"] for item in self._codes(component["codelist"], refresh)}
                if set(values) - valid:
                    raise ValueError(
                        f"Invalid {dimension} codes {sorted(set(values) - valid)}. Browse this dimension with get_metadata."
                    )
        agency, flow_id, _ = parse_dataset_id(dataset_id)
        params = {"dimensionAtObservation": "AllDimensions", "format": "csvfile"}
        if start:
            params["startPeriod"] = start
        if end:
            params["endPeriod"] = end
        response = self._get(
            f"data/{agency},{flow_id},{metadata['version']}/{quote(resolved_key, safe='.+_-')}",
            params,
        )
        reader = csv.DictReader(io.StringIO(response.text.lstrip("\ufeff")))
        fields = reader.fieldnames or []
        required = set(order) | {"TIME_PERIOD", "OBS_VALUE"}
        if len(fields) != len(set(fields)):
            raise RuntimeError(
                "PDH returned duplicate CSV columns; ambiguous observations were not accepted."
            )
        if not required.issubset(fields):
            raise RuntimeError(
                f"PDH returned invalid SDMX CSV; missing columns {sorted(required - set(fields))}."
            )
        series: dict[tuple, dict] = {}
        extra_fields = [field for field in fields if field not in required]
        observed = {dimension: set() for dimension in selected}
        seen = set()
        for row in reader:
            if None in row or any(row.get(field) is None for field in fields):
                raise RuntimeError(
                    "PDH CSV contains malformed rows; incomplete observations were not accepted."
                )
            identity = tuple(row[dimension] for dimension in order)
            period = row["TIME_PERIOD"]
            if not period or any(not code for code in identity):
                raise RuntimeError(
                    "PDH returned an observation without complete dimension coordinates."
                )
            if (start and period < start and not start.startswith(period)) or (
                end and period > end and not period.startswith(end)
            ):
                raise RuntimeError("PDH returned observations outside the requested periods.")
            if (identity, period) in seen:
                raise RuntimeError(
                    "PDH returned duplicate observations for the same dimensions and period."
                )
            seen.add((identity, period))
            for dimension in selected:
                observed[dimension].add(row[dimension])
            if any(row[dimension] not in allowed for dimension, allowed in selected.items()):
                raise RuntimeError(
                    "PDH returned observations outside the requested codes; refusing a mismatched slice."
                )
            item = series.setdefault(
                identity,
                {
                    "seriesKey": ".".join(identity),
                    "dimensions": {dimension: {"code": row[dimension]} for dimension in order},
                    "observations": [],
                },
            )
            raw_value = row["OBS_VALUE"].strip()
            try:
                value = float(raw_value)
                if not math.isfinite(value):
                    value = None
            except ValueError:
                value = None
            attributes = {field: row[field] for field in extra_fields}
            attributes["OBS_VALUE_RAW"] = raw_value
            item["observations"].append(
                {
                    "observationKey": row["TIME_PERIOD"],
                    "value": value,
                    "dimensions": {"TIME_PERIOD": {"code": row["TIME_PERIOD"]}},
                    "attributes": attributes,
                }
            )
        if not series:
            raise RuntimeError("PDH returned no observations for the selected filters and periods.")
        return {
            "kind": "pacific_retrieve",
            "coverage_gaps": coverage_gaps(selected, observed),
            "provider": PROVIDER,
            "dataset": {"id": dataset_id, "name": metadata["title"]},
            "series": list(series.values()),
            "api_request_url": str(response.url),
            "source_references": [
                {
                    "provider": PROVIDER,
                    "dataset_id": dataset_id,
                    "source_url": metadata["source_url"],
                    "api_request_url": str(response.url),
                }
            ],
            "retrieval": {
                "key": resolved_key,
                "source_filters": selected,
                "start_period": start,
                "end_period": end,
            },
            "source_annotations": metadata["annotations"],
            "value_semantics": "Values are unscaled. Retain UNIT_MULT and UNIT_MEASURE; suppressed/non-numeric values are null with OBS_VALUE_RAW preserved.",
        }


@lru_cache(maxsize=1)
def get_pacific_service() -> PacificDataService:
    return PacificDataService()
