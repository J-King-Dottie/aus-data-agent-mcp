"""Cached SDMX structure and codelist browsing shared by OECD and Pacific."""

from __future__ import annotations

import re
import time
import xml.etree.ElementTree as ET
from collections import OrderedDict
from threading import RLock
from typing import Any

import httpx

from .selection import code_page

NS = {
    "structure": "http://www.sdmx.org/resources/sdmxml/schemas/v2_1/structure",
    "common": "http://www.sdmx.org/resources/sdmxml/schemas/v2_1/common",
}
METADATA_TTL = 3600


def _text(element: ET.Element | None) -> str:
    return "".join(element.itertext()).strip() if element is not None else ""


def _name(element: ET.Element, field: str = "Name") -> str:
    names = element.findall(f"common:{field}", NS)
    return _text(
        next(
            (
                item
                for item in names
                if item.get("{http://www.w3.org/XML/1998/namespace}lang") == "en"
            ),
            names[0] if names else None,
        )
    )


def _annotations(element: ET.Element) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    for annotation in element.findall(".//common:Annotation", NS):
        kind = _text(annotation.find("common:AnnotationType", NS)) or "annotation"
        values = [
            _text(annotation.find(f"common:{field}", NS))
            for field in ("AnnotationTitle", "AnnotationText")
        ]
        result.setdefault(kind, []).extend(value for value in values if value)
    return result


def _identifier(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_@-][A-Za-z0-9_.@-]*", value):
        raise ValueError("Invalid SDMX identifier. Use IDs returned by the catalogue or metadata.")
    return value


def parse_dataset_id(dataset_id: str, prefix: str) -> tuple[str, str, str]:
    parts = dataset_id.split("::")
    if len(parts) != 4 or parts[0] != prefix:
        raise ValueError(
            f"Expected a {prefix}::agency::dataflow::version datasetId from search_catalog."
        )
    return tuple(_identifier(part) for part in parts[1:])


class SDMXStructureClient:
    metadata_kind = "sdmx_metadata"

    def __init__(self, base_url: str, timeout: float, prefix: str, provider: str):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.prefix = prefix
        self.provider = provider
        self._lock = RLock()
        self._xml_cache: OrderedDict[str, tuple[float, ET.Element]] = OrderedDict()

    def _get(self, path: str, params: dict | None = None) -> httpx.Response:
        response = httpx.get(
            f"{self.base_url}/{path}",
            params=params,
            timeout=self.timeout,
            follow_redirects=True,
            headers={"User-Agent": "AusData-MCP/0.1"},
        )
        response.raise_for_status()
        return response

    def _xml(self, path: str, refresh: bool = False) -> ET.Element:
        with self._lock:
            cached = self._xml_cache.get(path)
            if cached is not None and not refresh and time.monotonic() - cached[0] < METADATA_TTL:
                self._xml_cache.move_to_end(path)
                return cached[1]
        try:
            root = ET.fromstring(self._get(path, {"references": "children", "detail": "full"}).text)
        except ET.ParseError as exc:
            raise RuntimeError(
                f"{self.provider} returned invalid structure XML; retry metadata later."
            ) from exc
        with self._lock:
            self._xml_cache[path] = (time.monotonic(), root)
            self._xml_cache.move_to_end(path)
            while len(self._xml_cache) > 64:
                self._xml_cache.popitem(last=False)
        return root

    def metadata(self, dataset_id: str, refresh: bool = False) -> dict[str, Any]:
        agency, flow_id, version = parse_dataset_id(dataset_id, self.prefix)
        path = f"dataflow/{agency}/{flow_id}/{version}"
        root = self._xml(path, refresh)
        flow = next(
            (
                item
                for item in root.findall(".//structure:Dataflow", NS)
                if item.get("id") == flow_id
            ),
            None,
        )
        if flow is None:
            raise RuntimeError(f"{self.provider} metadata did not contain {flow_id}.")
        structure_ref = flow.find("structure:Structure/Ref", NS)
        structures = root.findall(".//structure:DataStructure", NS)
        dsd = next(
            (
                item
                for item in structures
                if structure_ref is not None and item.get("id") == structure_ref.get("id")
            ),
            None,
        )
        if dsd is None and len(structures) == 1:
            dsd = structures[0]
        if dsd is None:
            raise RuntimeError(
                f"{self.provider} did not return an unambiguous data structure; refusing to guess the key order."
            )
        dimensions, attributes = [], []
        for group, target, tags in (
            ("DimensionList", dimensions, {"Dimension", "MeasureDimension", "TimeDimension"}),
            ("AttributeList", attributes, {"Attribute"}),
        ):
            for element in dsd.findall(f".//structure:{group}/*", NS):
                tag = element.tag.rsplit("}", 1)[-1]
                if tag not in tags:
                    continue
                reference = next(
                    (
                        item
                        for item in element.iter()
                        if item.tag.rsplit("}", 1)[-1] == "Ref" and item.get("class") == "Codelist"
                    ),
                    None,
                )
                target.append(
                    {
                        "id": element.get("id"),
                        "position": int(element.get("position", "999")),
                        "is_time": tag == "TimeDimension",
                        "codelist": {
                            "id": reference.get("id"),
                            "agency": reference.get("agencyID", agency),
                            "version": reference.get("version", "latest"),
                        }
                        if reference is not None
                        else None,
                    }
                )
        dimensions.sort(key=lambda item: item["position"])
        if not dimensions:
            raise RuntimeError(
                f"{self.provider} returned no dimensions; refusing an unbounded data request."
            )
        return {
            "kind": self.metadata_kind,
            "dataset_id": dataset_id,
            "provider": self.provider,
            "title": _name(flow),
            "description": _name(flow, "Description"),
            "version": flow.get("version", version),
            "dimensions": dimensions,
            "attributes": attributes,
            "annotations": _annotations(flow),
            "key_order": [item["id"] for item in dimensions if not item["is_time"]],
            "source_url": f"{self.base_url}/{path}",
        }

    def _codes(self, reference: dict, refresh: bool = False) -> list[dict[str, Any]]:
        agency, code_id, version = (
            _identifier(reference[key]) for key in ("agency", "id", "version")
        )
        root = self._xml(f"codelist/{agency}/{code_id}/{version}", refresh)
        codelist = next(
            (
                item
                for item in root.findall(".//structure:Codelist", NS)
                if item.get("id") == code_id
            ),
            None,
        )
        if codelist is None:
            raise RuntimeError(f"{self.provider} did not return codelist {code_id}.")
        return [
            {
                "code": item.get("id"),
                "label": _name(item),
                "description": _name(item, "Description"),
            }
            for item in codelist.findall("structure:Code", NS)
        ]

    def codes(
        self,
        dataset_id: str,
        dimension: str,
        search: str = "",
        offset: int = 0,
        limit: int = 50,
        refresh: bool = False,
    ) -> dict[str, Any]:
        if offset < 0 or not 1 <= limit <= 200:
            raise ValueError("codeOffset must be non-negative and codeLimit must be 1-200.")
        metadata = self.metadata(dataset_id, refresh)
        component = next(
            (
                item
                for item in metadata["dimensions"] + metadata["attributes"]
                if item["id"] == dimension
            ),
            None,
        )
        if component is None or not component["codelist"]:
            raise ValueError("Choose a dimension or attribute with a codelist from get_metadata.")
        codes = self._codes(component["codelist"], refresh)
        return code_page(
            dataset_id, dimension, codes, search, offset, limit, codelist=component["codelist"]
        )
