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
import re
import time
import xml.etree.ElementTree as ET
from functools import lru_cache
from threading import RLock
from typing import Any
from urllib.parse import quote

import httpx

from .data_config import get_data_settings

PROVIDER = "Pacific Data Hub"
NS = {
    "structure": "http://www.sdmx.org/resources/sdmxml/schemas/v2_1/structure",
    "common": "http://www.sdmx.org/resources/sdmxml/schemas/v2_1/common",
}
METADATA_TTL = 3600


def _text(element: ET.Element | None) -> str:
    return "".join(element.itertext()).strip() if element is not None else ""


def _name(element: ET.Element, field: str = "Name") -> str:
    names = element.findall(f"common:{field}", NS)
    return _text(next((item for item in names if item.get("{http://www.w3.org/XML/1998/namespace}lang") == "en"), names[0] if names else None))


def _annotations(element: ET.Element) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    for annotation in element.findall(".//common:Annotation", NS):
        kind = _text(annotation.find("common:AnnotationType", NS)) or "annotation"
        values = [_text(annotation.find(f"common:{field}", NS)) for field in ("AnnotationTitle", "AnnotationText")]
        result.setdefault(kind, []).extend(value for value in values if value)
    return result


def _identifier(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_@-][A-Za-z0-9_.@-]*", value):
        raise ValueError("Invalid SDMX identifier. Use IDs returned by the catalogue or metadata.")
    return value


def parse_dataset_id(dataset_id: str) -> tuple[str, str, str]:
    parts = dataset_id.split("::")
    if len(parts) != 4 or parts[0] != "pdh":
        raise ValueError("Expected a pdh::agency::dataflow::version datasetId from search_catalog.")
    return tuple(_identifier(part) for part in parts[1:])


class PacificDataService:
    def __init__(self):
        settings = get_data_settings()
        self.base_url = settings.pdh_base_url.rstrip("/")
        self.timeout = settings.macro_timeout_seconds
        self._lock = RLock()
        self._xml_cache: dict[str, tuple[float, str]] = {}

    def _get(self, path: str, params: dict | None = None) -> httpx.Response:
        response = httpx.get(f"{self.base_url}/{path}", params=params, timeout=self.timeout,
                             follow_redirects=True, headers={"User-Agent": "AusData-MCP/0.1"})
        response.raise_for_status()
        return response

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
            description = _name(element, "Description") or " ".join(value for values in annotations.values() for value in values)
            flows.append({
                "route": "pacific", "provider": PROVIDER,
                "datasetId": f"pdh::{agency}::{flow}::{version}",
                "title": _name(element) or flow, "description": description,
                "sourceUrl": f"{self.base_url}/dataflow/{agency}/{flow}/{version}",
                "requiresMetadataBeforeRetrieval": True,
                "searchText": f"{flow} {_name(element)} {description} Pacific Data Hub SPC Pacific islands",
            })
        if not flows:
            raise RuntimeError("PDH returned no catalogue entries; existing catalogue was not replaced.")
        return flows

    def _xml(self, path: str, refresh: bool = False) -> ET.Element:
        with self._lock:
            cached = self._xml_cache.get(path)
            if cached is None or refresh or time.time() - cached[0] > METADATA_TTL:
                xml = self._get(path, {"references": "children", "detail": "full"}).text
                root = ET.fromstring(xml)
                self._xml_cache[path] = (time.time(), xml)
                return root
            return ET.fromstring(cached[1])

    def metadata(self, dataset_id: str, refresh: bool = False) -> dict[str, Any]:
        agency, flow_id, version = parse_dataset_id(dataset_id)
        path = f"dataflow/{agency}/{flow_id}/{version}"
        root = self._xml(path, refresh)
        flow = next((item for item in root.findall(".//structure:Dataflow", NS) if item.get("id") == flow_id), None)
        if flow is None:
            raise RuntimeError(f"PDH metadata did not contain {flow_id}.")
        structure_ref = flow.find("structure:Structure/Ref", NS)
        structures = root.findall(".//structure:DataStructure", NS)
        dsd = next((item for item in structures if structure_ref is not None and item.get("id") == structure_ref.get("id")), None)
        if dsd is None and len(structures) == 1:
            dsd = structures[0]
        if dsd is None:
            raise RuntimeError("PDH did not return an unambiguous data structure; refusing to guess the key order.")
        dimensions, attributes = [], []
        for group, target, tags in (("DimensionList", dimensions, {"Dimension", "TimeDimension"}), ("AttributeList", attributes, {"Attribute"})):
            for element in dsd.findall(f".//structure:{group}/*", NS):
                tag = element.tag.rsplit("}", 1)[-1]
                if tag not in tags:
                    continue
                reference = next((item for item in element.iter() if item.tag.rsplit("}", 1)[-1] == "Ref" and item.get("class") == "Codelist"), None)
                target.append({"id": element.get("id"), "position": int(element.get("position", "999")),
                               "is_time": tag == "TimeDimension", "codelist": {
                                   "id": reference.get("id"), "agency": reference.get("agencyID", agency),
                                   "version": reference.get("version", "latest")
                               } if reference is not None else None})
        dimensions.sort(key=lambda item: item["position"])
        if not dimensions:
            raise RuntimeError("PDH returned no dimensions; refusing an unbounded data request.")
        return {"kind": "pacific_metadata", "dataset_id": dataset_id, "provider": PROVIDER,
                "title": _name(flow), "version": flow.get("version", version),
                "dimensions": dimensions, "attributes": attributes, "annotations": _annotations(flow),
                "key_order": [item["id"] for item in dimensions if not item["is_time"]],
                "source_url": f"{self.base_url}/{path}"}

    def _codes(self, reference: dict, refresh: bool = False) -> list[dict[str, Any]]:
        agency, code_id, version = (_identifier(reference[key]) for key in ("agency", "id", "version"))
        root = self._xml(f"codelist/{agency}/{code_id}/{version}", refresh)
        codelist = next((item for item in root.findall(".//structure:Codelist", NS) if item.get("id") == code_id), None)
        if codelist is None:
            raise RuntimeError(f"PDH did not return codelist {code_id}.")
        return [{"code": item.get("id"), "label": _name(item), "description": _name(item, "Description")}
                for item in codelist.findall("structure:Code", NS)]

    def codes(self, dataset_id: str, dimension: str, search: str = "", offset: int = 0, limit: int = 50, refresh: bool = False) -> dict[str, Any]:
        if offset < 0 or not 1 <= limit <= 200:
            raise ValueError("codeOffset must be non-negative and codeLimit must be 1-200.")
        metadata = self.metadata(dataset_id, refresh)
        component = next((item for item in metadata["dimensions"] + metadata["attributes"] if item["id"] == dimension), None)
        if component is None or not component["codelist"]:
            raise ValueError("Choose a dimension or attribute with a codelist from get_metadata.")
        codes = self._codes(component["codelist"], refresh)
        matches = [code for code in codes if not search or search.casefold() in f"{code['code']} {code['label']} {code['description']}".casefold()]
        page = matches[offset:offset + limit]
        return {"dataset_id": dataset_id, "dimension": dimension, "codelist": component["codelist"],
                "codes": page, "total_codes": len(codes), "matching_codes": len(matches),
                "next_offset": offset + len(page) if offset + len(page) < len(matches) else None}

    def retrieve(self, dataset_id: str, filters: dict[str, list[str]] | None = None, key: str = "", start: str = "", end: str = "", refresh: bool = False) -> dict[str, Any]:
        metadata = self.metadata(dataset_id, refresh)
        order = metadata["key_order"]
        if key and filters:
            raise ValueError("PDH accepts sourceFilters or dataKey, not both.")
        selected = dict(filters or {})
        if key:
            parts = key.split(".")
            if len(parts) != len(order):
                raise ValueError(f"PDH dataKey needs {len(order)} positions: {order}.")
            selected = {dimension: part.split("+") for dimension, part in zip(order, parts) if part}
        unknown = set(selected) - set(order)
        if unknown:
            raise ValueError(f"Unknown PDH dimensions {sorted(unknown)}. Use metadata key_order; use startPeriod/endPeriod for time.")
        if start and end and start > end and not start.startswith(end):
            raise ValueError("startPeriod must not be after endPeriod.")
        for dimension, values in selected.items():
            if not isinstance(values, list) or not values or any(not isinstance(value, str) or not value for value in values):
                raise ValueError("sourceFilters values must be non-empty lists of exact source codes.")
            component = next(item for item in metadata["dimensions"] if item["id"] == dimension)
            if component["codelist"]:
                valid = {item["code"] for item in self._codes(component["codelist"])}
                if set(values) - valid:
                    raise ValueError(f"Invalid {dimension} codes {sorted(set(values) - valid)}. Browse this dimension with get_metadata.")
            if any(re.search(r"[.+/\\?#\s]", value) for value in values):
                raise ValueError("Dimension codes contain SDMX key separators; use codes returned by metadata.")
        if not selected:
            raise ValueError("Select at least one PDH dimension using sourceFilters before retrieval.")
        resolved_key = ".".join("+".join(selected.get(dimension, [])) for dimension in order)
        agency, flow_id, _ = parse_dataset_id(dataset_id)
        params = {"dimensionAtObservation": "AllDimensions", "format": "csvfile"}
        if start:
            params["startPeriod"] = start
        if end:
            params["endPeriod"] = end
        response = self._get(f"data/{agency},{flow_id},{metadata['version']}/{quote(resolved_key, safe='.+_-')}", params)
        reader = csv.DictReader(io.StringIO(response.text.lstrip("\ufeff")))
        fields = reader.fieldnames or []
        required = set(order) | {"TIME_PERIOD", "OBS_VALUE"}
        if not required.issubset(fields):
            raise RuntimeError(f"PDH returned invalid SDMX CSV; missing columns {sorted(required - set(fields))}.")
        series: dict[tuple, dict] = {}
        extra_fields = [field for field in fields if field not in required]
        for row in reader:
            if None in row or any(row.get(field) is None for field in fields):
                raise RuntimeError("PDH CSV contains malformed rows; incomplete observations were not accepted.")
            identity = tuple(row[dimension] for dimension in order)
            if any(row[dimension] not in allowed for dimension, allowed in selected.items()):
                raise RuntimeError("PDH returned observations outside the requested codes; refusing a mismatched slice.")
            item = series.setdefault(identity, {"seriesKey": ".".join(identity), "dimensions": {dimension: {"code": row[dimension]} for dimension in order}, "observations": []})
            raw_value = row["OBS_VALUE"].strip()
            try:
                value = float(raw_value)
                if not math.isfinite(value):
                    value = None
            except ValueError:
                value = None
            attributes = {field: row[field] for field in extra_fields}
            attributes["OBS_VALUE_RAW"] = raw_value
            item["observations"].append({"observationKey": row["TIME_PERIOD"], "value": value,
                                         "dimensions": {"TIME_PERIOD": {"code": row["TIME_PERIOD"]}}, "attributes": attributes})
        if not series:
            raise RuntimeError("PDH returned no observations for the selected filters and periods.")
        return {"kind": "pacific_retrieve", "provider": PROVIDER,
                "dataset": {"id": dataset_id, "name": metadata["title"]},
                "series": list(series.values()), "api_request_url": str(response.url),
                "source_references": [{"provider": PROVIDER, "dataset_id": dataset_id, "source_url": metadata["source_url"], "api_request_url": str(response.url)}],
                "retrieval": {"key": resolved_key, "source_filters": selected, "start_period": start, "end_period": end},
                "source_annotations": metadata["annotations"],
                "value_semantics": "Values are unscaled. Retain UNIT_MULT and UNIT_MEASURE; suppressed/non-numeric values are null with OBS_VALUE_RAW preserved."}


@lru_cache(maxsize=1)
def get_pacific_service() -> PacificDataService:
    return PacificDataService()
