"""Live discovery adapters for the supported source catalogues and publications."""
from __future__ import annotations

import html
import re
from pathlib import Path
from typing import Any, Dict, List

import httpx
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse, unquote


from .data_config import get_data_settings
from .domestic_data import get_domestic_service

WORLD_BANK_PROVIDER = "World Bank"
IMF_PROVIDER = "IMF"
OECD_PROVIDER = "OECD"
COMTRADE_PROVIDER = "UN Comtrade"

RBA_PAGE = "https://www.rba.gov.au/statistics/tables/"
ENERGY_PAGE = "https://www.energy.gov.au/energy-data/australian-energy-statistics"
COMTRADE_FLOWS = "https://comtradeapi.un.org/files/v1/app/reference/tradeRegimes.json"
OECD_SEARCH = "https://dotstat-search.oecd.org/api/search"
PROVIDERS = ("ABS", "World Bank", "IMF", "OECD", "Pacific Data Hub",
             "Reserve Bank of Australia", "Department of Climate Change, Energy, the Environment and Water", "UN Comtrade")


class Links(HTMLParser):
    """Read publication links and their labels without downloading data files."""
    def __init__(self, html):
        super().__init__(convert_charrefs=True)
        self.links = []
        self.current = None
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self.current = [dict(attrs).get("href", ""), ""]

    def handle_data(self, text):
        if self.current is not None:
            self.current[1] += text

    def handle_endtag(self, tag):
        if tag == "a" and self.current is not None:
            self.links.append((self.current[0], _clean_text(self.current[1])))
            self.current = None


def _page(client, url):
    response = client.get(url, timeout=60)
    response.raise_for_status()
    return response.text


def _custom_entry(flow):
    return {"route": "domestic", "provider": flow["sourceOrganization"],
            "datasetId": f"CUSTOM_AUS,{flow['id']},1.0", "title": flow["name"],
            "description": flow["description"], "searchText": _join_search_text([
                flow["id"], flow["name"], flow["description"],
                flow["sourceOrganization"], str((flow.get("curation") or {}).get("tableCode") or ""),
            ]),
            "sourceUrl": flow["sourcePageUrl"], "requiresMetadataBeforeRetrieval": True,
            "providerConfig": {}, "sourceRecord": flow}


def fetch_rba_catalog(client):
    entries = []
    title = ""
    for href, label in Links(_page(client, RBA_PAGE)).links:
        url = urljoin(RBA_PAGE, href)
        if urlparse(url).netloc != "www.rba.gov.au":
            continue
        if "/tables/xls/" in url:
            title = label
        filename = Path(urlparse(url).path).name
        # The parser supports RBA series-matrix CSVs. Transaction/event CSVs have
        # different schemas and are deliberately outside this adapter's coverage.
        match = re.fullmatch(r"([a-z][0-9]+(?:\.[0-9]+)?)-data(?:-([a-z0-9-]+))?\.csv", filename)
        if "/tables/csv/" not in url or not match:
            continue
        code = match[1].upper()
        suffix = "_" + match[2].replace("-", "_").upper() if match[2] else ""
        name = title or label or code
        if match[2]:
            name += " — " + label
        flow = {"id": "RBA_" + code.replace(".", "_") + suffix, "agencyID": "CUSTOM_AUS", "version": "1.0",
                "name": name, "description": f"Reserve Bank of Australia statistical table {code}: {name}. Live time-series CSV; inspect metadata for individual series.",
                "flowType": "rba_tables_csv", "sourceType": "csv", "sourceOrganization": PROVIDERS[5],
                "sourcePageUrl": RBA_PAGE, "sourceUrl": url, "curation": {"tableCode": code}}
        entries.append(_custom_entry(flow))
    return entries


def fetch_energy_catalog(client):
    publications = []
    for href, label in Links(_page(client, ENERGY_PAGE)).links:
        url = urljoin(ENERGY_PAGE, href)
        if urlparse(url).netloc == "www.energy.gov.au" and "statistics-table-o-electricity-generation" in url:
            years = re.findall(r"20\d{2}", url)
            if years:
                publications.append((max(map(int, years)), url, label))
    if not publications:
        raise RuntimeError("DCCEEW no longer lists an AES Table O publication at its statistics index.")
    _, publication, label = max(publications)
    downloads = []
    for href, text in Links(_page(client, publication)).links:
        url = urljoin(publication, href)
        decoded = unquote(url).lower()
        if urlparse(url).netloc == "www.energy.gov.au" and urlparse(url).path.lower().endswith(".xlsx") and re.search(r"table[ _-]*o", decoded + " " + text.lower()):
            downloads.append(url)
    downloads = list(dict.fromkeys(downloads))
    if len(downloads) != 1:
        raise RuntimeError(f"Expected one current AES Table O workbook, found {len(downloads)}; publication layout may have changed.")
    flow = {"id": "AES_TABLE_O", "agencyID": "CUSTOM_AUS", "version": "1.0", "name": label,
            "description": label + ". Electricity generation by fuel, state, financial year and calendar year. Inspect workbook metadata for available periods and sheets.",
            "flowType": "dcceew_aes_xlsx", "sourceType": "xlsx", "sourceOrganization": PROVIDERS[6],
            "sourcePageUrl": publication, "sourceUrl": downloads[0], "curation": {"discoverSheets": True}}
    return [_custom_entry(flow)]


def _normalize_macro(items):
    entries = []
    for item in dedupe_entries(items):
        config = item["provider_config"]
        entries.append({"route": "macro", "provider": item["provider_name"], "datasetId": item["entry_id"],
                        "title": item["indicator_label"], "description": item["description"],
                        "searchText": item["search_text"], "sourceUrl": config.get("source_url_template", ""),
                        "requiresMetadataBeforeRetrieval": True,
                        **{camel: item[snake] for camel, snake in (("providerKey", "provider_key"), ("providerName", "provider_name"),
                            ("conceptId", "concept_id"), ("conceptLabel", "concept_label"), ("indicatorLabel", "indicator_label"),
                            ("unit", "unit"), ("providerConfig", "provider_config"))}})
    return entries


def fetch_source(provider):
    """Fetch only discovery information; detailed metadata and observations are deferred."""
    if provider == "ABS":
        entries = _build_abs_entries()
    elif provider == "Pacific Data Hub":
        from .pacific_data import get_pacific_service
        entries = get_pacific_service().catalogue()
    else:
        with httpx.Client(follow_redirects=True, headers={"User-Agent": "AusData-MCP/0.1"}) as client:
            if provider in {"World Bank", "IMF", "OECD"}:
                fetch = {"World Bank": fetch_world_bank_catalog, "IMF": fetch_imf_catalog, "OECD": fetch_oecd_catalog}[provider]
                entries = _normalize_macro(fetch(client))
            elif provider == PROVIDERS[5]:
                entries = fetch_rba_catalog(client)
            elif provider == PROVIDERS[6]:
                entries = fetch_energy_catalog(client)
            elif provider == "UN Comtrade":
                # Comtrade is one queryable trade cube, not thousands of datasets.
                # Validate the supported import/export operations from its live list.
                response = client.get(COMTRADE_FLOWS, timeout=60)
                response.raise_for_status()
                flows = response.json()["results"]
                if not {"M", "X"}.issubset({str(row["id"]) for row in flows}):
                    raise RuntimeError("Comtrade no longer lists supported import/export flows.")
                entries = _normalize_macro(build_comtrade_catalog())
                entries[0]["catalogueSourceUrl"] = COMTRADE_FLOWS
                entries[0]["discoveryScope"] = "One supported goods-trade cube; live import/export capability check. Commodity and country metadata is separate."
            else:
                raise ValueError(f"Unsupported catalogue provider: {provider}")
    if not entries:
        raise RuntimeError(f"{provider} returned no supported catalogue entries.")
    return list({entry["datasetId"]: entry for entry in entries}.values())


def _clean_text(value: Any) -> str:
    text = str(value or "")
    text = re.sub(r"\s+", " ", text.replace("\u0000", " ")).strip()
    return text


def _join_search_text(parts: List[str]) -> str:
    deduped: List[str] = []
    for part in parts:
        clean = _clean_text(part)
        if clean and clean not in deduped:
            deduped.append(clean)
    return " ".join(deduped)


def fetch_world_bank_catalog(client: httpx.Client) -> List[Dict[str, Any]]:
    first = client.get(
        get_data_settings().worldbank_base_url.rstrip("/") + "/indicator",
        params={"format": "json", "per_page": 20000, "page": 1},
        timeout=120,
    )
    first.raise_for_status()
    payload = first.json()
    meta = payload[0] if isinstance(payload, list) and payload else {}
    pages = int(meta.get("pages") or 1)
    rows = payload[1] if isinstance(payload, list) and len(payload) > 1 else []
    if not isinstance(rows, list) or not rows or not meta.get("pages"):
        raise RuntimeError("World Bank returned an invalid indicator catalogue.")

    all_rows = list(rows) if isinstance(rows, list) else []
    for page in range(2, pages + 1):
        response = client.get(
            get_data_settings().worldbank_base_url.rstrip("/") + "/indicator",
            params={"format": "json", "per_page": 20000, "page": page},
            timeout=120,
        )
        response.raise_for_status()
        page_payload = response.json()
        page_rows = page_payload[1] if isinstance(page_payload, list) and len(page_payload) > 1 else []
        if not isinstance(page_rows, list) or not page_rows:
            raise RuntimeError(f"World Bank indicator page {page} was empty or invalid.")
        all_rows.extend(page_rows)

    if meta.get("total") is not None and len(all_rows) != int(meta["total"]):
        raise RuntimeError("World Bank catalogue pagination was incomplete.")
    entries: List[Dict[str, Any]] = []
    for row in all_rows:
        if not isinstance(row, dict):
            continue
        indicator_id = _clean_text(row.get("id"))
        label = _clean_text(row.get("name"))
        if not indicator_id or not label:
            continue
        source = row.get("source") if isinstance(row.get("source"), dict) else {}
        source_label = _clean_text(source.get("value"))
        source_id = _clean_text(source.get("id"))
        source_note = _clean_text(row.get("sourceNote"))
        source_org = _clean_text(row.get("sourceOrganization"))
        topics = []
        for topic in row.get("topics") or []:
            if isinstance(topic, dict):
                topic_label = _clean_text(topic.get("value"))
                if topic_label:
                    topics.append(topic_label)
        description = _clean_text(" ".join(part for part in [label, source_note, source_org] if part))
        search_text = _join_search_text(
            [
                indicator_id,
                label,
                source_label,
                source_note,
                source_org,
                *topics,
                "world bank",
                "worldbank",
            ]
        )
        source_url = f"https://data.worldbank.org/indicator/{indicator_id}"
        entries.append(
            {
                "entry_id": f"worldbank::{indicator_id}" if source_id == "2" else f"worldbank::{source_id}::{indicator_id}",
                "provider_key": "worldbank",
                "provider_name": WORLD_BANK_PROVIDER,
                "concept_id": indicator_id,
                "concept_label": label,
                "indicator_label": label,
                "unit": "",
                "description": description or label,
                "search_text": search_text,
                "provider_config": {
                    "series_id": indicator_id,
                    "source_id": source_id,
                    "source_name": source_label,
                    "label": label,
                    "source_url_template": source_url,
                },
            }
        )
    return entries


def fetch_imf_catalog(client: httpx.Client) -> List[Dict[str, Any]]:
    response = client.get(get_data_settings().imf_base_url.rstrip("/") + "/indicators", timeout=120)
    response.raise_for_status()
    payload = response.json()
    indicators = payload.get("indicators") if isinstance(payload, dict) else {}
    if not isinstance(indicators, dict):
        return []

    entries: List[Dict[str, Any]] = []
    for series_id, item in indicators.items():
        if not isinstance(item, dict):
            continue
        clean_series_id = _clean_text(series_id)
        label = _clean_text(item.get("label"))
        if not clean_series_id or not label:
            continue
        description = _clean_text(item.get("description"))
        dataset = _clean_text(item.get("dataset"))
        source = _clean_text(item.get("source"))
        unit = _clean_text(item.get("unit"))
        source_url = (
            f"https://www.imf.org/external/datamapper/{clean_series_id}@{dataset}"
            if dataset
            else f"https://www.imf.org/external/datamapper/{clean_series_id}"
        )
        entries.append(
            {
                "entry_id": f"imf::{clean_series_id}",
                "provider_key": "imf",
                "provider_name": IMF_PROVIDER,
                "concept_id": clean_series_id,
                "concept_label": label,
                "indicator_label": label,
                "unit": unit,
                "description": _clean_text(" ".join(part for part in [label, description, source, unit] if part)) or label,
                "search_text": _join_search_text(
                    [
                        clean_series_id,
                        label,
                        description,
                        dataset,
                        source,
                        unit,
                        "imf",
                        "international monetary fund",
                    ]
                ),
                "provider_config": {
                    "series_id": clean_series_id,
                    "label": label,
                    "dataset": dataset,
                    "source_url_template": source_url,
                },
            }
        )
    return entries


def fetch_oecd_catalog(client: httpx.Client) -> List[Dict[str, Any]]:
    # OECD Data Explorer uses this public search index for dataset discovery.
    # Its SDMX dataflow endpoint can challenge server-side requests, while the
    # detailed structures and observations remain separate SDMX operations.
    flows = []
    total = None
    while total is None or len(flows) < total:
        response = client.get(OECD_SEARCH, params={"tenant": "oecd", "rows": 1500, "start": len(flows)}, timeout=120)
        response.raise_for_status()
        payload = response.json()
        page = payload.get("dataflows")
        found = payload.get("numFound")
        if not isinstance(page, list) or not isinstance(found, int) or found < 1 or payload.get("start") != len(flows):
            raise RuntimeError("OECD search returned an invalid catalogue page.")
        if total is not None and found != total:
            raise RuntimeError("OECD catalogue changed during pagination; retry the refresh.")
        total = found
        if not page or len(flows) + len(page) > total:
            raise RuntimeError("OECD search returned an incomplete catalogue page.")
        flows.extend(page)

    entries: List[Dict[str, Any]] = []
    for flow in flows:
        if flow.get("datasourceId") != "dsDisseminateFinalDMZ":
            continue
        agency_id = _clean_text(flow.get("agencyId"))
        dataflow_id = _clean_text(flow.get("dataflowId"))
        version = _clean_text(flow.get("version"))
        if not (agency_id.startswith("OECD") and dataflow_id and version):
            raise RuntimeError("OECD search returned a public dataflow without its SDMX identity.")
        label = _clean_text(html.unescape(re.sub(r"<[^>]*>", " ", str(flow.get("name") or dataflow_id))))
        description = _clean_text(html.unescape(re.sub(r"<[^>]*>", " ", str(flow.get("description") or label))))[:4000]
        source_url = f"{get_data_settings().oecd_base_url.rstrip('/')}/data/{agency_id},{dataflow_id},{version}"
        entries.append(
            {
                "entry_id": f"oecd::{agency_id}::{dataflow_id}::{version}",
                "provider_key": "oecd",
                "provider_name": OECD_PROVIDER,
                "concept_id": dataflow_id,
                "concept_label": label,
                "indicator_label": label,
                "unit": "",
                "description": description or label,
                "search_text": _join_search_text(
                    [
                        dataflow_id,
                        label,
                        description,
                        agency_id,
                        "oecd",
                    ]
                ),
                "provider_config": {
                    "agency": agency_id,
                    "dataflow": dataflow_id,
                    "version": version,
                    "label": label,
                    "source_url_template": source_url,
                },
            }
        )
    return entries


def build_comtrade_catalog() -> List[Dict[str, Any]]:
    label = "UN Comtrade goods trade (imports and exports by partner and HS code)"
    description = (
        "UN Comtrade goods trade retrieval for imports and exports, bilateral trade, world totals, "
        "and HS product codes down to 4-digit headings. Metadata exposes reporter countries, "
        "partner areas, annual or monthly frequency, and HS code descriptions."
    )
    return [
        {
            "entry_id": "comtrade::goods_trade",
            "provider_key": "comtrade",
            "provider_name": COMTRADE_PROVIDER,
            "concept_id": "goods_trade",
            "concept_label": "Goods trade",
            "indicator_label": label,
            "unit": "US Dollars",
            "description": description,
            "search_text": _join_search_text(
                [
                    "goods trade",
                    "imports",
                    "exports",
                    "import",
                    "export",
                    "bilateral trade",
                    "partner",
                    "hs code",
                    "hs4",
                    "hs 4 digit heading",
                    "commodity",
                    "merchandise trade",
                    "un comtrade",
                    "united nations comtrade",
                    "comtrade",
                ]
            ),
            "provider_config": {
                "series_id": "UN_COMTRADE_GOODS_TRADE",
                "label": "UN Comtrade goods trade",
                "requires_metadata_before_retrieval": True,
                "metadata_source": "COMTRADE_METADATA.json",
                "source_url_template": "https://comtradeplus.un.org/TradeFlow",
            },
        }
    ]


def dedupe_entries(entries: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    deduped: Dict[str, Dict[str, Any]] = {}
    for entry in entries:
        entry_id = _clean_text(entry.get("entry_id"))
        if not entry_id:
            continue
        existing = deduped.get(entry_id)
        if existing is None:
            deduped[entry_id] = entry
            continue
        existing["search_text"] = _join_search_text(
            [existing.get("search_text", ""), entry.get("search_text", "")]
        )
        if len(_clean_text(entry.get("description"))) > len(_clean_text(existing.get("description"))):
            existing["description"] = entry["description"]
        if not _clean_text(existing.get("unit")) and _clean_text(entry.get("unit")):
            existing["unit"] = entry["unit"]
    return list(deduped.values())




def _abs_source_url(agency_id: str, flow_id: str, version: str) -> str:
    clean_agency = _clean_text(agency_id)
    clean_flow = _clean_text(flow_id)
    clean_version = _clean_text(version)
    if not clean_agency or not clean_flow or not clean_version:
        return ""
    return f"https://data.api.abs.gov.au/rest/dataflow/{clean_agency}/{clean_flow}/{clean_version}"


def _build_abs_entries() -> List[Dict[str, Any]]:
    entries: List[Dict[str, Any]] = []
    for flow in get_domestic_service().get_abs_data_flows(force_refresh=True):
        flow_id = _clean_text(flow.get("id"))
        agency_id = _clean_text(flow.get("agencyID")) or "ABS"
        version = _clean_text(flow.get("version"))
        if not flow_id or not version:
            continue
        dataset_id = f"{agency_id},{flow_id},{version}"
        entries.append(
            {
                "route": "domestic",
                "provider": "ABS",
                "datasetId": dataset_id,
                "title": _clean_text(flow.get("name")) or flow_id,
                "description": _clean_text(flow.get("description")),
                "searchText": _join_search_text(
                    [
                        flow_id,
                        dataset_id,
                        agency_id,
                        _clean_text(flow.get("name")),
                        _clean_text(flow.get("description")),
                    ]
                ),
                "sourceUrl": _abs_source_url(agency_id, flow_id, version),
                "requiresMetadataBeforeRetrieval": True,
                "providerKey": "",
                "providerName": "ABS",
                "conceptId": "",
                "conceptLabel": "",
                "indicatorLabel": "",
                "unit": "",
                "providerConfig": {},
                "sourceRecord": flow,
            }
        )
    return entries
