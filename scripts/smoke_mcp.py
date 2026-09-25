"""Exercise the actual stdio protocol without app dependencies or model credentials."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import tempfile
import time
from datetime import timedelta
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def seed_offline_catalogue(directory):
    """Small explicit fixture in a resumed session; no shipped catalogue or network."""
    from ausdata_mcp.catalog_refresh import SCHEMA_VERSION
    from ausdata_mcp.catalog_sources import PROVIDERS

    path = Path(directory) / "sessions" / "smoke" / "catalogue" / "catalog.json"
    path.parent.mkdir(parents=True)
    entry = {
        "datasetId": "worldbank::NY.GDP.PCAP.CD",
        "route": "macro",
        "provider": "World Bank",
        "title": "GDP per capita NY.GDP.PCAP.CD",
        "description": "Protocol test fixture",
        "unit": "USD",
        "searchText": "GDP per capita NY.GDP.PCAP.CD",
        "sourceUrl": "https://data.worldbank.org/indicator/NY.GDP.PCAP.CD",
        "requiresMetadataBeforeRetrieval": True,
        "providerKey": "worldbank",
        "conceptId": "NY.GDP.PCAP.CD",
        "conceptLabel": "GDP per capita",
        "indicatorLabel": "GDP per capita",
        "providerConfig": {"series_id": "NY.GDP.PCAP.CD"},
    }
    sources = {
        p: {
            "status": "fresh",
            "entry_count": int(p == "World Bank"),
            "refresh_after": time.time() + 3600,
            "error": None,
        }
        for p in PROVIDERS
    }
    path.write_text(
        json.dumps(
            {
                "schema_version": SCHEMA_VERSION,
                "generation": "fixture",
                "entries": [entry],
                "sources": sources,
                "lastUpdated": "fixture",
            }
        )
    )


async def smoke(
    live: bool = False,
    live_pacific: bool = False,
    live_domestic: bool = False,
    live_abs: bool = False,
    live_macro: bool = False,
) -> dict:
    with tempfile.TemporaryDirectory(prefix="ausdata-smoke-") as directory:
        env = {
            key: value
            for key, value in os.environ.items()
            if not key.startswith(("OPENAI_", "NISABA_", "AUSDATA_"))
        }
        env.update(AUSDATA_RUNTIME_DIR=directory, PYTHON_DOTENV_DISABLED="1")
        if not any((live, live_pacific, live_domestic, live_abs, live_macro)):
            seed_offline_catalogue(directory)
            env["AUSDATA_SESSION_ID"] = "smoke"
        parameters = StdioServerParameters(
            command=sys.executable,
            args=[str(ROOT / "scripts/run_mcp.py")],
            cwd=directory,
            env=env,
        )
        async with stdio_client(parameters) as (reader, writer):
            async with ClientSession(
                reader, writer, read_timeout_seconds=timedelta(seconds=600)
            ) as session:
                initialized = await session.initialize()
                assert initialized.instructions == (ROOT / "AGENT_SYSTEM_PROMPT.md").read_text()
                listing = await session.list_tools()
                names = {tool.name for tool in listing.tools}
                assert names == {"search_catalog", "get_metadata", "retrieve"}, names
                assert all(
                    tool.inputSchema.get("additionalProperties") is False for tool in listing.tools
                )
                invalid = await session.call_tool(
                    "search_catalog", {"query": "test", "provder": "ABS"}
                )
                assert invalid.isError and "provder" in invalid.content[0].text
                resources = await session.list_resources()
                assert "ausdata://guide" in {str(item.uri) for item in resources.resources}
                guide = await session.read_resource("ausdata://guide")
                assert guide.contents[0].text == initialized.instructions
                prompt = await session.get_prompt(
                    "analyse_public_data", {"question": "Compare GDP"}
                )
                assert "Compare GDP" in prompt.messages[0].content.text

                async def call(name, arguments):
                    result = await session.call_tool(name, arguments)
                    if result.isError:
                        raise RuntimeError(f"{name}: {result.content}")
                    payload = result.structuredContent or json.loads(result.content[0].text)
                    return payload.get("result", payload)

                found = await call(
                    "search_catalog",
                    {"query": "NY.GDP.PCAP.CD", "provider": "World Bank", "limit": 5},
                )
                assert found["candidates"]
                assert all("searchText" not in item for item in found["candidates"])
                metadata = await call("get_metadata", {"datasetId": "worldbank::NY.GDP.PCAP.CD"})
                assert metadata["source_identifiers"]["series_id"] == "NY.GDP.PCAP.CD"
                report = {
                    "tools": sorted(names),
                    "guidance": "verified",
                    "search": "passed",
                    "live": "not requested",
                }
                if any((live, live_pacific, live_domestic, live_abs, live_macro)):
                    report["catalogue"] = found["catalogue"]
                    report["catalogue_warnings"] = found["warnings"]
                if live:
                    manifest = await call(
                        "retrieve",
                        {
                            "datasetId": "worldbank::NY.GDP.PCAP.CD",
                            "countries": ["AUS", "NZL"],
                            "startYear": 2020,
                            "endYear": 2022,
                        },
                    )
                    payload = json.loads(
                        Path(manifest["artifact_path"]).read_text(encoding="utf-8")
                    )
                    assert {series["country_code"] for series in payload["series"]} == {
                        "AUS",
                        "NZL",
                    }
                    assert sum(len(series["points"]) for series in payload["series"]) == 6
                    assert manifest["row_count"] == 6 and len(manifest["preview_rows"]) <= 3
                    assert (
                        manifest["artifact_bytes"] == Path(manifest["artifact_path"]).stat().st_size
                    )
                    assert manifest["artifact_size_mib"] == round(
                        manifest["artifact_bytes"] / 1048576, 2
                    )
                    assert manifest["large_artifact"] is False
                    assert manifest["retrieved_at"] and manifest["source_references"]
                    report["live"] = (
                        "World Bank: complete 6-observation artifact and compact manifest verified"
                    )
                if live_pacific:
                    found = await call(
                        "search_catalog", {"query": "energy", "provider": "PDH", "limit": 10}
                    )
                    dataset_id = next(
                        item["datasetId"]
                        for item in found["candidates"]
                        if "::DF_ENERGY::" in item["datasetId"]
                    )
                    metadata = await call("get_metadata", {"datasetId": dataset_id})
                    assert "GEO_PICT" in metadata["key_order"]
                    geography = await call(
                        "get_metadata",
                        {"datasetId": dataset_id, "dimension": "GEO_PICT", "codeSearch": "Fiji"},
                    )
                    assert any(code["code"] == "FJ" for code in geography["codes"])
                    indicators = await call(
                        "get_metadata",
                        {
                            "datasetId": dataset_id,
                            "dimension": "INDICATOR",
                            "codeSearch": "ENERGY_IND_001",
                        },
                    )
                    assert indicators["codes"]
                    manifest = await call(
                        "retrieve",
                        {
                            "datasetId": dataset_id,
                            "sourceFilters": {"GEO_PICT": ["FJ"], "INDICATOR": ["ENERGY_IND_001"]},
                        },
                    )
                    payload = json.loads(
                        Path(manifest["artifact_path"]).read_text(encoding="utf-8")
                    )
                    assert manifest["row_count"] > 0
                    assert all(
                        series["dimensions"]["GEO_PICT"]["code"] == "FJ"
                        for series in payload["series"]
                    )
                    assert (
                        "UNIT_MULT" in manifest["attribute_ids"]
                        and "OBS_STATUS" in manifest["attribute_ids"]
                    )
                    assert payload["source_references"] and payload["retrieved_at"]
                    count = found["catalogue"]["sources"]["Pacific Data Hub"]["entry_count"]
                    report["pacific_live"] = (
                        f"PDH: {count} datasets; metadata, codelists, retrieval and complete artifact verified"
                    )
                if live_abs:
                    found = await call("search_catalog", {"query": "LF_AGES", "provider": "ABS"})
                    identity = next(
                        item["datasetId"]
                        for item in found["candidates"]
                        if ",LF_AGES," in item["datasetId"]
                    )
                    metadata = await call("get_metadata", {"datasetId": identity})
                    anchor = metadata["anchor_candidates"][0]
                    code = anchor["anchor_codes"][0]["code"]
                    manifest = await call(
                        "retrieve",
                        {
                            "datasetId": identity,
                            "anchorType": anchor["anchor_type"],
                            "anchorCode": code,
                            "startPeriod": "2022-08",
                            "endPeriod": "2022-08",
                        },
                    )
                    payload = json.loads(
                        Path(manifest["artifact_path"]).read_text(encoding="utf-8")
                    )
                    assert manifest["row_count"] > 0 and manifest["period_start"] == "2022-08"
                    assert manifest["period_end"] == "2022-08" and payload["source_structure"]
                    assert manifest["unit_multiplier_codes"], manifest
                    report["abs_live"] = (
                        "ABS: live metadata anchor, source coordinates, attributes and complete artifact verified"
                    )
                if live_macro:
                    selections = [
                        (
                            "IMF",
                            "LUR",
                            "imf::LUR",
                            {"countries": ["AUS"], "startYear": 2022, "endYear": 2023},
                        ),
                        (
                            "OECD",
                            "DSD_PRICES_COICOP2018@DF_PRICES_C2018_N_TXCP01_NRG",
                            None,
                            {"countries": ["AUS"], "startYear": 2023, "endYear": 2023},
                        ),
                        (
                            "UN Comtrade",
                            "goods trade",
                            "comtrade::goods_trade",
                            {
                                "reporterCodes": ["36"],
                                "partnerCodes": ["0"],
                                "flowCode": "X",
                                "frequencyCode": "A",
                                "hsCodes": ["TOTAL"],
                                "startYear": 2022,
                                "endYear": 2022,
                            },
                        ),
                    ]
                    for provider, query, identity, filters in selections:
                        found = await call("search_catalog", {"query": query, "provider": provider})
                        identity = identity or next(
                            item["datasetId"]
                            for item in found["candidates"]
                            if query in item["datasetId"]
                        )
                        await call("get_metadata", {"datasetId": identity})
                        manifest = await call("retrieve", {"datasetId": identity, **filters})
                        payload = json.loads(
                            Path(manifest["artifact_path"]).read_text(encoding="utf-8")
                        )
                        assert manifest["row_count"] > 0 and payload["source_references"]
                        report[provider + "_live"] = (
                            "Metadata, retrieval and complete artifact verified"
                        )
                if live_domestic:
                    rba = await call("get_metadata", {"datasetId": "CUSTOM_AUS,RBA_F1,1.0"})
                    assert rba["codelists"]
                    series_id = rba["codelists"][0]["codes"][0]["id"]
                    manifest = await call(
                        "retrieve", {"datasetId": "CUSTOM_AUS,RBA_F1,1.0", "dataKey": series_id}
                    )
                    payload = json.loads(
                        Path(manifest["artifact_path"]).read_text(encoding="utf-8")
                    )
                    periods = [
                        row["observationKey"] for row in payload["series"][0]["observations"]
                    ]
                    assert manifest["row_count"] > 0 and min(periods) == manifest["period_start"]
                    assert all(len(period) == 10 and period[4] == "-" for period in periods)
                    rba_rows = {"F1": manifest["row_count"]}
                    for table in ("A1", "G1", "I1"):
                        dataset_id = f"CUSTOM_AUS,RBA_{table},1.0"
                        await call("get_metadata", {"datasetId": dataset_id})
                        manifest = await call(
                            "retrieve", {"datasetId": dataset_id, "dataKey": "all"}
                        )
                        assert manifest["row_count"] > 0
                        rba_rows[table] = manifest["row_count"]
                    report["rba_table_rows"] = rba_rows
                    identity = "CUSTOM_AUS,AES_TABLE_O,1.0"
                    metadata = await call("get_metadata", {"datasetId": identity})
                    assert metadata["codelists"]
                    manifest = await call(
                        "retrieve", {"datasetId": identity, "dataKey": "national_financial_year"}
                    )
                    payload = json.loads(
                        Path(manifest["artifact_path"]).read_text(encoding="utf-8")
                    )
                    rows = []
                    for series in payload["series"]:
                        for observation in series["observations"]:
                            dimensions = {
                                **(series.get("dimensions") or {}),
                                **(observation.get("dimensions") or {}),
                            }
                            category = dimensions.get("CATEGORY")
                            code = category.get("code") if isinstance(category, dict) else category
                            if code == "BLACK_COAL" and str(
                                observation["observationKey"]
                            ).startswith("1989"):
                                rows.append((series, observation))
                    assert len(rows) == 1, [
                        (series.get("dimensions"), series["observations"][:1])
                        for series in payload["series"][:3]
                    ]
                    assert rows[0][1]["value"] == 87573, rows
                    attributes = {
                        **(rows[0][0].get("attributes") or {}),
                        **(rows[0][1].get("attributes") or {}),
                    }
                    assert attributes["UNIT"] == "GWh", rows
                    assert payload["source_annotations"]["workbook_notes"]
                    groups = next(
                        item["codes"]
                        for item in metadata["codelists"]
                        if item["id"] == "SHEET_GROUPS"
                    )
                    group_counts = {"national_financial_year": manifest["row_count"]}
                    for group in groups:
                        if group["id"] == "national_financial_year":
                            continue
                        manifest = await call(
                            "retrieve", {"datasetId": identity, "dataKey": group["id"]}
                        )
                        payload = json.loads(
                            Path(manifest["artifact_path"]).read_text(encoding="utf-8")
                        )
                        assert manifest["row_count"] > 0
                        for series in payload["series"]:
                            for observation in series["observations"]:
                                period = observation["dimensions"]["TIME_PERIOD"]
                                assert re.fullmatch(r"\d{4}(?:-\d{2})?", period["code"]), period
                        group_counts[group["id"]] = manifest["row_count"]
                    report["domestic_live"] = (
                        "RBA live-link metadata and retrieval; all discovered AES sheet groups, periods, first-year value, units and source notes verified"
                    )
                    report["energy_group_rows"] = group_counts
                return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--live-pacific", action="store_true")
    parser.add_argument("--live-domestic", action="store_true")
    parser.add_argument("--live-abs", action="store_true")
    parser.add_argument("--live-macro", action="store_true")
    args = parser.parse_args()
    print(
        json.dumps(
            asyncio.run(
                smoke(
                    args.live, args.live_pacific, args.live_domestic, args.live_abs, args.live_macro
                )
            ),
            indent=2,
        )
    )
