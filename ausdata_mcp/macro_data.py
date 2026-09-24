from __future__ import annotations

import csv
import io
import json
import logging
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import httpx

from .data_config import get_data_settings


settings = get_data_settings()
COMTRADE_METADATA_PATH = Path(__file__).resolve().parents[1] / "COMTRADE_METADATA.json"
logger = logging.getLogger("ausdata.macro")


COUNTRY_ALIASES: Dict[str, str] = {
    "australia": "AUS",
    "australian": "AUS",
    "aus": "AUS",
    "japan": "JPN",
    "jpn": "JPN",
    "united states": "USA",
    "us": "USA",
    "usa": "USA",
    "america": "USA",
    "united kingdom": "GBR",
    "uk": "GBR",
    "britain": "GBR",
    "england": "GBR",
    "germany": "DEU",
    "deu": "DEU",
    "france": "FRA",
    "fra": "FRA",
    "canada": "CAN",
    "can": "CAN",
    "china": "CHN",
    "chn": "CHN",
    "india": "IND",
    "ind": "IND",
    "italy": "ITA",
    "ita": "ITA",
    "spain": "ESP",
    "esp": "ESP",
    "korea": "KOR",
    "south korea": "KOR",
    "kor": "KOR",
    "new zealand": "NZL",
    "nzl": "NZL",
    "brazil": "BRA",
    "bra": "BRA",
    "mexico": "MEX",
    "mex": "MEX",
    "indonesia": "IDN",
    "idn": "IDN",
    "singapore": "SGP",
    "sgp": "SGP",
    "euro area": "EA19",
    "eurozone": "EA19",
}


WORLD_BANK_PROVIDER = "World Bank"
IMF_PROVIDER = "IMF"
OECD_PROVIDER = "OECD"
COMTRADE_PROVIDER = "UN Comtrade"


def _truncate_log(text: Any, length: int = 400) -> str:
    value = str(text or "").replace("\n", " ").strip()
    return value if len(value) <= length else value[: length - 1] + "…"


def _request_url(url: str, params: Optional[Dict[str, Any]] = None) -> str:
    if not params:
        return url
    filtered = {key: value for key, value in params.items() if value is not None}
    return f"{url}?{httpx.QueryParams(filtered)}"


CATALOG_STOPWORDS = {
    "the", "a", "an", "of", "for", "in", "to", "and", "or", "show", "get", "find",
    "latest", "available", "historical", "comparison", "including", "countries",
    "country", "annual", "quarterly", "monthly", "data", "series",
    "vs", "versus", "compare", "compared", "between", "across", "over", "year", "years",
}


@dataclass
class MacroCatalogEntry:
    entry_id: str
    provider_key: str
    provider_name: str
    concept_id: str
    concept_label: str
    indicator_label: str
    unit: str
    description: str
    search_text: str
    provider_config: Dict[str, Any]


def _normalize_text(value: str) -> str:
    return re.sub(r"[^a-z0-9\s]+", " ", str(value or "").lower()).strip()


def _contains_token(text: str, token: str) -> bool:
    clean_text = f" {str(text or '').strip()} "
    clean_token = str(token or "").strip()
    if not clean_token:
        return False
    return f" {clean_token} " in clean_text


@lru_cache(maxsize=1)
def _load_comtrade_metadata() -> Dict[str, Any]:
    if not COMTRADE_METADATA_PATH.exists():
        raise RuntimeError(f"Comtrade metadata file not found: {COMTRADE_METADATA_PATH}")
    try:
        payload = json.loads(COMTRADE_METADATA_PATH.read_text(encoding="utf-8"))
    except Exception as exc:
        raise RuntimeError(f"Failed to read Comtrade metadata file {COMTRADE_METADATA_PATH}: {exc}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"Comtrade metadata file {COMTRADE_METADATA_PATH} must contain a top-level object.")
    return payload


def _comtrade_dimension(name: str) -> List[Dict[str, Any]]:
    payload = _load_comtrade_metadata()
    values = payload.get(name)
    return [item for item in values if isinstance(item, dict)]


def _score_comtrade_option(query: str, option: Dict[str, Any]) -> int:
    normalized_query = f" {_normalize_text(query)} "
    score = 0
    code = str(option.get("code") or "").strip().lower()
    label = _normalize_text(str(option.get("label") or ""))

    if code and _contains_token(normalized_query, code):
        score += 40
    if label and label in normalized_query:
        score += 30

    for text in [label]:
        if not text:
            continue
        tokens = [token for token in text.split() if len(token) >= 3 and token not in CATALOG_STOPWORDS]
        token_hits = sum(1 for token in tokens if _contains_token(normalized_query, token))
        score += token_hits * 3

    return score


def _comtrade_matches(query: str, options: List[Dict[str, Any]], *, limit: int = 12) -> List[Dict[str, Any]]:
    ranked: List[tuple[int, Dict[str, Any]]] = []
    for option in options:
        score = _score_comtrade_option(query, option)
        if score <= 0:
            continue
        ranked.append((score, option))
    ranked.sort(key=lambda item: (-item[0], str(item[1].get("label") or ""), str(item[1].get("code") or "")))
    return [item for _, item in ranked[:limit]]


def _inject_comtrade_option(
    options: List[Dict[str, Any]],
    injected: Dict[str, Any],
) -> List[Dict[str, Any]]:
    injected_code = str(injected.get("code") or "").strip()
    if not injected_code:
        return list(options)
    deduped = [item for item in options if str(item.get("code") or "").strip() != injected_code]
    return [dict(injected), *deduped]


def _comtrade_period_values(start_year: int, end_year: int, frequency_code: str) -> List[str]:
    if frequency_code == "M":
        values: List[str] = []
        for year in range(start_year, end_year + 1):
            for month in range(1, 13):
                values.append(f"{year}{month:02d}")
        return values
    return [str(year) for year in range(start_year, end_year + 1)]


def _chunk_period_values(period_values: List[str], max_periods: int = 12) -> List[str]:
    if not period_values:
        return []
    size = max(1, int(max_periods))
    return [
        ",".join(period_values[index : index + size])
        for index in range(0, len(period_values), size)
    ]


def _resolve_comtrade_area_codes(raw_values: List[str], options: List[Dict[str, Any]]) -> List[str]:
    by_key: Dict[str, str] = {}
    for option in options:
        code = str(option.get("code") or "").strip()
        if not code:
            continue
        by_key[code] = code
        label = _normalize_text(str(option.get("label") or ""))
        if label:
            by_key[label] = code

    resolved: List[str] = []
    for raw in raw_values:
        clean = str(raw or "").strip()
        if not clean:
            continue
        mapped = by_key.get(clean)
        if mapped is None:
            mapped = by_key.get(clean.upper())
        if mapped is None:
            mapped = by_key.get(_normalize_text(clean))
        if mapped and mapped not in resolved:
            resolved.append(mapped)
    return resolved


def _resolve_comtrade_hs_codes(raw_values: List[str], options: List[Dict[str, Any]]) -> List[str]:
    by_key: Dict[str, str] = {}
    for option in options:
        code = str(option.get("code") or "").strip()
        label = _normalize_text(str(option.get("label") or ""))
        if code:
            by_key[code] = code
        if label:
            by_key[label] = code

    resolved: List[str] = []
    for raw in raw_values:
        clean = str(raw or "").strip()
        if not clean:
            continue
        mapped = by_key.get(clean) or by_key.get(_normalize_text(clean))
        if mapped and mapped not in resolved:
            resolved.append(mapped)
    return resolved


def _validated_comtrade_codes(
    values: List[str], options: List[Dict[str, Any]],
    resolver: Callable[[List[str], List[Dict[str, Any]]], List[str]], parameter: str,
) -> List[str]:
    invalid = [str(value) for value in values if not resolver([value], options)]
    if invalid:
        raise ValueError(f"Invalid {parameter} {invalid}. Choose codes from get_metadata.")
    return resolver(values, options)


def _build_comtrade_metadata_payload(query: str, selected_entry: MacroCatalogEntry) -> Dict[str, Any]:
    flows = _comtrade_dimension("flows")
    countries = _comtrade_dimension("countries")
    hs_2digit = _comtrade_dimension("hs_2digit")
    hs_4digit = _comtrade_dimension("hs_4digit")

    partner_options = _inject_comtrade_option(
        countries,
        {"code": "0", "label": "All partners (World total)"},
    )
    matched_hs_2digit = _inject_comtrade_option(
        _comtrade_matches(query, hs_2digit, limit=25),
        {"code": "TOTAL", "label": "TOTAL - All products"},
    )
    matched_hs_4digit = _inject_comtrade_option(
        _comtrade_matches(query, hs_4digit, limit=100),
        {"code": "TOTAL", "label": "TOTAL - All products"},
    )

    return {
        "provider": COMTRADE_PROVIDER,
        "candidate_id": selected_entry.entry_id,
        "concept_id": selected_entry.concept_id,
        "concept_label": selected_entry.concept_label,
        "indicator_label": selected_entry.indicator_label,
        "requires_metadata_before_retrieval": True,
        "dimensions": [
            {
                "id": "FLOW",
                "label": "Trade flow",
                "required": True,
                "options": flows,
            },
            {
                "id": "FREQUENCY",
                "label": "Annual or monthly observations",
                "required": True,
                "options": [{"code": "A", "label": "Annual"}, {"code": "M", "label": "Monthly"}],
            },
            {
                "id": "REPORTER",
                "label": "Reporter country",
                "required": True,
                "optionCount": len(countries),
                "options": countries,
            },
            {
                "id": "PARTNER",
                "label": "Partner country",
                "required": True,
                "optionCount": len(partner_options),
                "options": partner_options,
            },
            {
                "id": "HS_2DIGIT",
                "label": "HS 2-digit chapter",
                "required": True,
                "optionCount": len(hs_2digit),
                "matchedOptions": matched_hs_2digit,
            },
            {
                "id": "HS_4DIGIT",
                "label": "HS 4-digit heading",
                "required": True,
                "optionCount": len(hs_4digit),
                "matchedOptions": matched_hs_4digit,
            },
        ],
        "fullDimensionCounts": {
            "flows": len(flows),
            "reporters": len(countries),
            "partners": len(partner_options),
            "hs_2digit": len(hs_2digit),
            "hs_4digit": len(hs_4digit),
        },
    }


def _get_country_name_from_iso3(code: str) -> str:
    for alias, iso3 in COUNTRY_ALIASES.items():
        if iso3 == code and len(alias) > 3:
            return alias.title()
    return code


def _source_reference(provider: str, *, indicator: str, series_id: str, country: str = "", source_url: str = "") -> Dict[str, Any]:
    ref: Dict[str, Any] = {
        "provider": provider,
        "indicator": indicator,
        "series_id": series_id,
    }
    if country:
        ref["country"] = country
    if source_url:
        ref["source_url"] = source_url
    return ref


def _parse_numeric(value: Any) -> Optional[float]:
    if value in {None, ""}:
        return None
    try:
        return float(value)
    except Exception:
        return None


def _parse_world_bank_error(payload: Any) -> Optional[str]:
    if not isinstance(payload, list) or not payload:
        return None
    first = payload[0]
    if not isinstance(first, dict):
        return None
    messages = first.get("message")
    if not isinstance(messages, list):
        return None
    parts: List[str] = []
    for item in messages:
        if not isinstance(item, dict):
            continue
        value = str(item.get("value") or "").strip()
        key = str(item.get("key") or "").strip()
        text = value or key
        if text:
            parts.append(text)
    return "; ".join(parts) if parts else None


def _looks_like_html_error(text: str) -> bool:
    normalized = str(text or "").lstrip().lower()
    return normalized.startswith("<!doctype html") or normalized.startswith("<html") or normalized.startswith("<?xml")


def _fetch_world_bank(query: str, entry: MacroCatalogEntry, provider_config: Dict[str, Any], countries: List[str], start_year: Optional[int], end_year: Optional[int], *, all_countries: bool = False) -> Dict[str, Any]:
    if not countries and not all_countries:
        countries = ["AUS"]
    series_id = str(provider_config.get("series_id") or "").strip()
    label = str(provider_config.get("label") or entry.concept_label or series_id).strip()
    requested_countries = list(countries)
    country_path = "all" if all_countries else ";".join(countries)
    url = f"{settings.worldbank_base_url.rstrip('/')}/country/{country_path}/indicator/{series_id}"
    params: Dict[str, Any] = {"format": "json", "per_page": 20000}
    if provider_config.get("source_id"):
        params["source"] = provider_config["source_id"]
    if start_year and end_year:
        params["date"] = f"{start_year}:{end_year}"
    request_url = _request_url(url, params)
    final_request_url = request_url
    logger.info(
        'Macro retrieval request provider=worldbank indicator=%s countries="%s" all_countries=%s url="%s"',
        series_id,
        ",".join(countries),
        all_countries,
        _truncate_log(request_url, 700),
    )
    try:
        response = httpx.get(url, params=params, timeout=settings.macro_timeout_seconds)
        response.raise_for_status()
        if _looks_like_html_error(response.text):
            raise RuntimeError("World Bank returned an HTML error page.")
        final_request_url = str(response.request.url)
        payload = response.json()
    except Exception as exc:
        response = exc.response if isinstance(exc, httpx.HTTPStatusError) else None
        if response is not None and getattr(response, "request", None) is not None:
            final_request_url = str(response.request.url)
        body_preview = _truncate_log(response.text, 500) if response is not None else ""
        logger.error(
            'Macro retrieval error provider=worldbank indicator=%s url="%s" status=%s error="%s" body="%s"',
            series_id,
            _truncate_log(request_url, 700),
            getattr(response, "status_code", ""),
            _truncate_log(exc, 500),
            body_preview,
        )
        raise
    error_message = _parse_world_bank_error(payload)
    if error_message:
        logger.error(
            'Macro retrieval error provider=worldbank indicator=%s url="%s" api_error="%s"',
            series_id,
            _truncate_log(request_url, 700),
            _truncate_log(error_message, 500),
        )
        raise RuntimeError(f"World Bank error for {series_id}: {error_message}")
    if not isinstance(payload, list) or len(payload) < 2 or not isinstance(payload[1], list):
        raise RuntimeError("World Bank returned an unexpected response shape.")

    rows = list(payload[1])
    request_urls = [final_request_url]
    pages = int(payload[0].get("pages", 1)) if isinstance(payload[0], dict) else 1
    for page in range(2, pages + 1):
        page_params = {**params, "page": page}
        page_response = httpx.get(url, params=page_params, timeout=settings.macro_timeout_seconds)
        page_response.raise_for_status()
        page_payload = page_response.json()
        if not isinstance(page_payload, list) or len(page_payload) < 2 or not isinstance(page_payload[1], list):
            raise RuntimeError(f"World Bank returned an invalid page {page} of {pages}; incomplete data was not accepted.")
        rows.extend(page_payload[1])
        request_urls.append(str(page_response.request.url))
    by_country: Dict[str, List[Dict[str, Any]]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        value = _parse_numeric(row.get("value"))
        year = str(row.get("date") or "").strip()
        iso3 = str(row.get("countryiso3code") or "").strip().upper()
        if value is None or not year or not iso3:
            continue
        if start_year and year < str(start_year):
            continue
        if end_year and year > str(end_year):
            continue
        by_country.setdefault(iso3, []).append({"x": year, "y": value})

    series: List[Dict[str, Any]] = []
    source_refs: List[Dict[str, Any]] = []
    country_codes = sorted(by_country.keys()) if all_countries else requested_countries
    for country_code in country_codes:
        points = sorted(by_country.get(country_code) or [], key=lambda item: item["x"])
        if not points:
            continue
        country_name = _get_country_name_from_iso3(country_code)
        source_url = f"{provider_config.get('source_url_template') or ''}?locations={country_code}"
        series.append(
            {
                "provider": WORLD_BANK_PROVIDER,
                "country": country_name,
                "country_code": country_code,
                "indicator": label,
                "series_id": series_id,
                "unit": entry.unit,
                "frequency": "annual",
                "points": points,
                "source_url": source_url,
            }
        )
        source_refs.append(
            _source_reference(
                WORLD_BANK_PROVIDER,
                indicator=label,
                series_id=series_id,
                country=country_name,
                source_url=source_url,
            )
        )

    if not series:
        logger.error(
            'Macro retrieval error provider=worldbank indicator=%s url="%s" error="%s"',
            series_id,
            _truncate_log(request_url, 700),
            "World Bank returned no usable data.",
        )
        raise RuntimeError(f"World Bank returned no usable data for {series_id}.")
    logger.info(
        "Macro retrieval success provider=worldbank indicator=%s series=%s url=\"%s\"",
        series_id,
        len(series),
        _truncate_log(final_request_url, 700),
    )

    return {
        "provider": WORLD_BANK_PROVIDER,
        "concept_id": entry.concept_id,
        "concept_label": entry.concept_label,
        "api_request_url": final_request_url,
        "api_request_urls": request_urls,
        "series": series,
        "source_references": source_refs,
    }


def _fetch_imf(query: str, entry: MacroCatalogEntry, provider_config: Dict[str, Any], countries: List[str], start_year: Optional[int], end_year: Optional[int], *, all_countries: bool = False) -> Dict[str, Any]:
    series_id = str(provider_config.get("series_id") or "").strip()
    label = str(provider_config.get("label") or entry.concept_label or series_id).strip()
    url = f"{settings.imf_base_url.rstrip('/')}/{series_id}"
    logger.info(
        'Macro retrieval request provider=imf indicator=%s countries="%s" all_countries=%s url="%s"',
        series_id,
        ",".join(countries),
        all_countries,
        _truncate_log(url, 700),
    )
    try:
        response = httpx.get(url, timeout=settings.macro_timeout_seconds)
        response.raise_for_status()
        payload = response.json()
    except Exception as exc:
        response = exc.response if isinstance(exc, httpx.HTTPStatusError) else None
        body_preview = _truncate_log(response.text, 500) if response is not None else ""
        logger.error(
            'Macro retrieval error provider=imf indicator=%s url="%s" status=%s error="%s" body="%s"',
            series_id,
            _truncate_log(url, 700),
            getattr(response, "status_code", ""),
            _truncate_log(exc, 500),
            body_preview,
        )
        raise
    values = payload.get("values") if isinstance(payload.get("values"), dict) else {}
    series_values = values.get(series_id) if isinstance(values.get(series_id), dict) else {}
    if not isinstance(series_values, dict):
        raise RuntimeError("IMF returned an unexpected response shape.")
    if not countries:
        if all_countries:
            countries = sorted(str(code or "").strip().upper() for code in series_values.keys() if str(code or "").strip())
        else:
            countries = ["AUS"]

    series: List[Dict[str, Any]] = []
    source_refs: List[Dict[str, Any]] = []
    for country_code in countries:
        country_values = series_values.get(country_code) if isinstance(series_values.get(country_code), dict) else {}
        points = []
        for year, raw_value in country_values.items():
            value = _parse_numeric(raw_value)
            year_text = str(year or "").strip()
            if value is None or not year_text:
                continue
            if start_year and year_text.isdigit() and int(year_text) < start_year:
                continue
            if end_year and year_text.isdigit() and int(year_text) > end_year:
                continue
            points.append({"x": year_text, "y": value})
        points.sort(key=lambda item: item["x"])
        if not points:
            continue
        country_name = _get_country_name_from_iso3(country_code)
        source_url = f"{provider_config.get('source_url_template') or ''}/{country_code}"
        series.append(
            {
                "provider": IMF_PROVIDER,
                "country": country_name,
                "country_code": country_code,
                "indicator": label,
                "series_id": series_id,
                "unit": entry.unit,
                "frequency": "annual",
                "points": points,
                "source_url": source_url,
            }
        )
        source_refs.append(
            _source_reference(
                IMF_PROVIDER,
                indicator=label,
                series_id=series_id,
                country=country_name,
                source_url=source_url,
            )
        )

    if not series:
        logger.error(
            'Macro retrieval error provider=imf indicator=%s url="%s" error="%s"',
            series_id,
            _truncate_log(str(response.request.url), 700),
            "IMF returned no usable data.",
        )
        raise RuntimeError(f"IMF returned no usable data for {series_id}.")
    logger.info(
        "Macro retrieval success provider=imf indicator=%s series=%s url=\"%s\"",
        series_id,
        len(series),
        _truncate_log(str(response.request.url), 700),
    )

    return {
        "provider": IMF_PROVIDER,
        "concept_id": entry.concept_id,
        "concept_label": entry.concept_label,
        "api_request_url": str(response.request.url),
        "series": series,
        "source_references": source_refs,
    }


def _choose_oecd_rows(rows: List[Dict[str, str]], provider_config: Dict[str, Any], countries: List[str]) -> List[Dict[str, str]]:
    base_filters = provider_config.get("row_filters") if isinstance(provider_config.get("row_filters"), dict) else {}
    preferred_transformations = [
        str(item or "").strip()
        for item in (provider_config.get("preferred_transformations") or [])
        if str(item or "").strip()
    ]
    preferred_totals = provider_config.get("preferred_totals") if isinstance(provider_config.get("preferred_totals"), dict) else {}

    filtered = []
    for row in rows:
        if str(row.get("REF_AREA") or "").strip().upper() not in countries:
            continue
        keep = True
        for key, expected in base_filters.items():
            if str(row.get(key) or "").strip() != str(expected):
                keep = False
                break
        if keep:
            filtered.append(row)

    if not filtered and preferred_transformations:
        relaxed = []
        for row in rows:
            if str(row.get("REF_AREA") or "").strip().upper() not in countries:
                continue
            keep = True
            for key, expected in base_filters.items():
                if key == "TRANSFORMATION":
                    continue
                if str(row.get(key) or "").strip() != str(expected):
                    keep = False
                    break
            if keep:
                relaxed.append(row)
        filtered = relaxed

    if preferred_transformations and filtered:
        best = []
        for candidate in preferred_transformations:
            subset = [row for row in filtered if str(row.get("TRANSFORMATION") or "").strip() == candidate]
            if subset:
                best = subset
                break
        if best:
            filtered = best

    if preferred_totals and filtered:
        for key, preferred_values in preferred_totals.items():
            subset = [
                row
                for row in filtered
                if str(row.get(key) or "").strip() in {str(item) for item in preferred_values}
            ]
            if subset:
                filtered = subset

    return filtered


def _fetch_oecd(query: str, entry: MacroCatalogEntry, provider_config: Dict[str, Any], countries: List[str], start_year: Optional[int], end_year: Optional[int], *, all_countries: bool = False) -> Dict[str, Any]:
    agency = str(provider_config.get("agency") or "").strip()
    dataflow = str(provider_config.get("dataflow") or "").strip()
    version = str(provider_config.get("version") or "1.0").strip()
    if not agency or not dataflow:
        raise RuntimeError("OECD provider configuration is incomplete.")

    url = f"{settings.oecd_base_url.rstrip('/')}/data/{agency},{dataflow},{version}"
    params: Dict[str, Any] = {
        "dimensionAtObservation": "AllDimensions",
        "format": "csvfilewithlabels",
    }
    if start_year:
        params["startPeriod"] = str(start_year)
    if end_year:
        params["endPeriod"] = str(end_year)

    request_url = _request_url(url, params)
    logger.info(
        'Macro retrieval request provider=oecd indicator=%s countries="%s" all_countries=%s url="%s"',
        dataflow,
        ",".join(countries),
        all_countries,
        _truncate_log(request_url, 700),
    )
    try:
        response = httpx.get(url, params=params, timeout=max(settings.macro_timeout_seconds, 60))
        response.raise_for_status()
        text = response.text
    except Exception as exc:
        response = exc.response if isinstance(exc, httpx.HTTPStatusError) else None
        body_preview = _truncate_log(response.text, 500) if response is not None else ""
        logger.error(
            'Macro retrieval error provider=oecd indicator=%s url="%s" status=%s error="%s" body="%s"',
            dataflow,
            _truncate_log(request_url, 700),
            getattr(response, "status_code", ""),
            _truncate_log(exc, 500),
            body_preview,
        )
        raise
    if "Could not find Dataflow" in text:
        raise RuntimeError(f"OECD dataflow {agency},{dataflow},{version} was not found.")

    rows = list(csv.DictReader(io.StringIO(text)))
    if not countries:
        if all_countries:
            countries = sorted(
                {
                    str(row.get("REF_AREA") or "").strip().upper()
                    for row in rows
                    if isinstance(row, dict) and str(row.get("REF_AREA") or "").strip()
                }
            )
        else:
            countries = ["AUS"]
    selected_rows = _choose_oecd_rows(rows, provider_config, countries)
    if not selected_rows:
        logger.error(
            'Macro retrieval error provider=oecd indicator=%s url="%s" error="%s"',
            dataflow,
            _truncate_log(str(response.request.url), 700),
            "OECD returned no usable rows.",
        )
        raise RuntimeError(f"OECD returned no usable rows for {dataflow}.")

    label = str(provider_config.get("label") or entry.concept_label or dataflow).strip()
    series_id = dataflow
    grouped: Dict[str, List[Dict[str, Any]]] = {}
    units: Dict[str, set[str]] = {}
    frequencies: Dict[str, set[str]] = {}
    seen_periods: set[tuple[str, str]] = set()
    for row in selected_rows:
        country_code = str(row.get("REF_AREA") or "").strip().upper()
        period = str(row.get("TIME_PERIOD") or "").strip()
        value = _parse_numeric(row.get("OBS_VALUE"))
        if not country_code or not period or value is None:
            continue
        identity = (country_code, period)
        if identity in seen_periods:
            raise RuntimeError(
                "OECD dataflow contains multiple observations for the same country and period. "
                "Its other dimensions cannot be safely combined into one series; choose a more specific dataset or another source."
            )
        seen_periods.add(identity)
        grouped.setdefault(country_code, []).append({"x": period, "y": value})
        unit = str(row.get("Unit of measure") or row.get("UNIT_MEASURE") or entry.unit or "").strip()
        multiplier = str(row.get("UNIT_MULT") or "").strip()
        if multiplier and multiplier != "0":
            unit = f"{unit} (10^{multiplier})" if unit else f"10^{multiplier}"
        units.setdefault(country_code, set()).add(unit)
        frequencies.setdefault(country_code, set()).add(str(row.get("FREQ") or "").strip())

    series: List[Dict[str, Any]] = []
    source_refs: List[Dict[str, Any]] = []
    for country_code in countries:
        points = sorted(grouped.get(country_code) or [], key=lambda item: item["x"])
        if not points:
            continue
        if len(units[country_code]) > 1:
            raise RuntimeError("OECD returned changing units within one series; select a more specific slice.")
        country_name = _get_country_name_from_iso3(country_code)
        source_url = str(provider_config.get("source_url_template") or "").strip()
        frequency_codes = frequencies[country_code] - {""}
        frequency = ({"A": "annual", "Q": "quarterly", "M": "monthly", "W": "weekly", "D": "daily"}.get(next(iter(frequency_codes)), "")
                     if len(frequency_codes) == 1 else "")
        series.append(
            {
                "provider": OECD_PROVIDER,
                "country": country_name,
                "country_code": country_code,
                "indicator": label,
                "series_id": series_id,
                "unit": next(iter(units[country_code])),
                "frequency": frequency or ("annual" if all("-" not in item["x"] for item in points) else "mixed"),
                "points": points,
                "source_url": source_url,
            }
        )
        source_refs.append(
            _source_reference(
                OECD_PROVIDER,
                indicator=label,
                series_id=series_id,
                country=country_name,
                source_url=source_url,
            )
        )

    if not series:
        logger.error(
            'Macro retrieval error provider=oecd indicator=%s url="%s" error="%s"',
            dataflow,
            _truncate_log(str(response.request.url), 700),
            "OECD returned no usable points.",
        )
        raise RuntimeError(f"OECD returned no usable points for {dataflow}.")
    logger.info(
        "Macro retrieval success provider=oecd indicator=%s series=%s url=\"%s\"",
        dataflow,
        len(series),
        _truncate_log(str(response.request.url), 700),
    )

    return {
        "provider": OECD_PROVIDER,
        "concept_id": entry.concept_id,
        "concept_label": entry.concept_label,
        "api_request_url": str(response.request.url),
        "series": series,
        "source_references": source_refs,
    }


def _fetch_comtrade(
    query: str,
    entry: MacroCatalogEntry,
    provider_config: Dict[str, Any],
    *,
    reporter_codes: List[str],
    partner_codes: List[str],
    flow_code: str,
    frequency_code: str,
    hs_codes: List[str],
    start_year: Optional[int],
    end_year: Optional[int],
) -> Dict[str, Any]:
    reporters_lookup = {str(item.get("code") or "").strip(): item for item in _comtrade_dimension("countries")}
    hs_lookup = {
        str(item.get("code") or "").strip(): item
        for item in (_comtrade_dimension("hs_2digit") + _comtrade_dimension("hs_4digit"))
    }
    flow_lookup = {str(item.get("code") or "").strip(): item for item in _comtrade_dimension("flows")}
    frequency_lookup = {
        "A": {"code": "A", "label": "Annual"},
        "M": {"code": "M", "label": "Monthly"},
    }

    clean_reporters = _validated_comtrade_codes(reporter_codes, list(reporters_lookup.values()),
                                                _resolve_comtrade_area_codes, "reporterCodes")
    partner_options = [{"code": "0", "label": "All partners (World total)"}, *reporters_lookup.values()]
    clean_partners = _validated_comtrade_codes(partner_codes, partner_options,
                                               _resolve_comtrade_area_codes, "partnerCodes")
    clean_hs_codes = _validated_comtrade_codes(hs_codes, list(hs_lookup.values()),
                                               _resolve_comtrade_hs_codes, "hsCodes")
    if not clean_reporters:
        raise RuntimeError("Comtrade retrieval requires at least one valid reporterCode.")
    if not clean_partners:
        raise ValueError("Comtrade retrieval requires partnerCodes from metadata; use ['0'] for the World total.")
    if not clean_hs_codes:
        raise ValueError("Comtrade retrieval requires hsCodes from metadata; use ['TOTAL'] for all products.")
    if flow_code not in flow_lookup:
        raise ValueError("Comtrade retrieval requires a valid flowCode from metadata.")
    if frequency_code not in frequency_lookup:
        raise ValueError("Comtrade retrieval requires frequencyCode A or M.")
    if not isinstance(start_year, int) or not isinstance(end_year, int) or start_year > end_year:
        raise ValueError("Comtrade retrieval requires startYear and endYear in ascending order.")

    clean_flow_code = flow_code
    clean_frequency_code = frequency_code
    resolved_start_year, resolved_end_year = start_year, end_year

    period_values = _comtrade_period_values(resolved_start_year, resolved_end_year, clean_frequency_code)
    period_chunks = _chunk_period_values(period_values, max_periods=12)
    if not period_chunks:
        raise RuntimeError("Comtrade retrieval produced an empty period selection.")

    request_count = len(clean_reporters) * len(clean_partners) * len(clean_hs_codes) * len(period_chunks)
    point_budget = len(clean_reporters) * len(clean_partners) * len(clean_hs_codes) * len(period_values)
    if request_count > 36 or point_budget > 1200:
        raise RuntimeError(
            "Requested UN Comtrade retrieval is too broad. Narrow one of: countries, partners, HS codes, frequency, or time range."
        )

    if settings.comtrade_api_key:
        base_url = f"{settings.comtrade_base_url.rstrip('/')}/C/{clean_frequency_code}/HS"
    else:
        base_url = f"https://comtradeapi.un.org/public/v1/preview/C/{clean_frequency_code}/HS"
    source_url = "https://comtradeplus.un.org/TradeFlow"
    flow_label = str((flow_lookup.get(clean_flow_code) or {}).get("label") or clean_flow_code)
    frequency_label = str((frequency_lookup.get(clean_frequency_code) or {}).get("label") or clean_frequency_code)

    all_series: Dict[tuple[str, str, str, str], Dict[str, Any]] = {}
    source_refs: List[Dict[str, Any]] = []
    last_request_url = ""
    request_urls: List[str] = []

    for reporter_code in clean_reporters:
        for partner_code in clean_partners:
            for hs_code in clean_hs_codes:
                for period_chunk in period_chunks:
                    params: Dict[str, Any] = {
                        "reporterCode": reporter_code,
                        "period": period_chunk,
                        "partnerCode": partner_code,
                        "cmdCode": hs_code,
                        "flowCode": clean_flow_code,
                    }
                    headers = {"User-Agent": "AusData-MCP/1.0"}
                    if settings.comtrade_api_key:
                        headers["Ocp-Apim-Subscription-Key"] = settings.comtrade_api_key

                    request_url = _request_url(base_url, params)
                    last_request_url = request_url
                    request_urls.append(request_url)
                    logger.info(
                        'Macro retrieval request provider=comtrade reporters="%s" partners="%s" hs="%s" flow=%s frequency=%s url="%s"',
                        ",".join(clean_reporters),
                        ",".join(clean_partners),
                        ",".join(clean_hs_codes),
                        clean_flow_code,
                        clean_frequency_code,
                        _truncate_log(request_url, 700),
                    )
                    try:
                        response = httpx.get(base_url, params=params, timeout=max(settings.macro_timeout_seconds, 60),
                                             headers=headers)
                        response.raise_for_status()
                        payload = response.json()
                    except Exception as exc:
                        response = exc.response if isinstance(exc, httpx.HTTPStatusError) else None
                        body_preview = _truncate_log(response.text, 500) if response is not None else ""
                        logger.error(
                            'Macro retrieval error provider=comtrade url="%s" status=%s error="%s" body="%s"',
                            _truncate_log(request_url, 700),
                            getattr(response, "status_code", ""),
                            _truncate_log(exc, 500),
                            body_preview,
                        )
                        raise

                    rows = payload.get("data") if isinstance(payload, dict) else None
                    if not isinstance(rows, list):
                        continue

                    for row in rows:
                        if not isinstance(row, dict):
                            continue
                        period = str(row.get("period") or "").strip()
                        value = _parse_numeric(row.get("primaryValue"))
                        if not period or value is None:
                            continue
                        row_reporter_code = str(row.get("reporterCode") or reporter_code).strip()
                        row_partner_code = str(row.get("partnerCode") or partner_code).strip()
                        row_cmd_code = str(row.get("cmdCode") or hs_code).strip() or hs_code
                        row_flow_code = str(row.get("flowCode") or clean_flow_code).strip()
                        if (row_reporter_code, row_partner_code, row_cmd_code, row_flow_code) != (
                            reporter_code, partner_code, hs_code, clean_flow_code
                        ):
                            raise RuntimeError("UN Comtrade returned observations outside the requested codes.")
                        key = (row_reporter_code, row_partner_code, row_cmd_code, clean_flow_code)
                        series_entry = all_series.setdefault(
                            key,
                            {
                                "provider": COMTRADE_PROVIDER,
                                "country": str(row.get("reporterDesc") or (reporters_lookup.get(row_reporter_code) or {}).get("label") or row_reporter_code),
                                "country_code": row_reporter_code,
                                "partner": str(row.get("partnerDesc") or ("World" if row_partner_code == "0" else row_partner_code)),
                                "partner_code": row_partner_code,
                                "indicator": f"{flow_label} - {str((hs_lookup.get(row_cmd_code) or {}).get('label') or row.get('cmdDesc') or row_cmd_code)}",
                                "series_id": provider_config.get("series_id") or entry.concept_id or "UN_COMTRADE_GOODS_TRADE",
                                "unit": entry.unit or "US Dollars",
                                "frequency": "monthly" if clean_frequency_code == "M" else "annual",
                                "flow_code": clean_flow_code,
                                "flow_label": flow_label,
                                "hs_code": row_cmd_code,
                                "hs_label": str((hs_lookup.get(row_cmd_code) or {}).get("label") or row.get("cmdDesc") or row_cmd_code),
                                "source_url": source_url,
                                "points": [],
                            },
                        )
                        x_value = f"{period[:4]}-{period[4:6]}" if clean_frequency_code == "M" and len(period) == 6 else period
                        series_entry["points"].append({"x": x_value, "y": value})

    series: List[Dict[str, Any]] = []
    seen_refs: set[str] = set()
    for item in all_series.values():
        points_by_x: Dict[str, float] = {}
        for point in item.get("points") or []:
            x_value = str(point.get("x") or "").strip()
            y_value = point.get("y")
            if not x_value or not isinstance(y_value, (int, float)):
                continue
            if x_value in points_by_x and points_by_x[x_value] != float(y_value):
                raise RuntimeError("UN Comtrade returned conflicting values for the same series and period.")
            points_by_x[x_value] = float(y_value)
        points = [{"x": key, "y": points_by_x[key]} for key in sorted(points_by_x.keys())]
        if not points:
            continue
        item["points"] = points
        series.append(item)
        ref_key = "|".join(
            [
                str(item.get("country") or ""),
                str(item.get("partner") or ""),
                str(item.get("hs_code") or ""),
                str(item.get("flow_code") or ""),
            ]
        )
        if ref_key in seen_refs:
            continue
        seen_refs.add(ref_key)
        source_refs.append(
            _source_reference(
                COMTRADE_PROVIDER,
                indicator=str(item.get("indicator") or ""),
                series_id=str(item.get("hs_code") or ""),
                country=str(item.get("country") or ""),
                source_url=source_url,
            )
        )

    if not series:
        raise RuntimeError("UN Comtrade returned no usable data for the selected request shape.")

    logger.info(
        'Macro retrieval success provider=comtrade series=%s request="%s"',
        len(series),
        _truncate_log(last_request_url, 700),
    )

    return {
        "provider": COMTRADE_PROVIDER,
        "concept_id": entry.concept_id,
        "concept_label": entry.concept_label,
        "api_request_url": last_request_url,
        "api_request_urls": request_urls,
        "query_parameters": {
            "reporterCodes": clean_reporters,
            "partnerCodes": clean_partners,
            "flowCode": clean_flow_code,
            "frequencyCode": clean_frequency_code,
            "frequencyLabel": frequency_label,
            "hsCodes": clean_hs_codes,
            "startYear": resolved_start_year,
            "endYear": resolved_end_year,
        },
        "series": series,
        "source_references": source_refs,
    }
