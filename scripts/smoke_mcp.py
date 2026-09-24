"""Exercise the actual stdio protocol without app dependencies or model credentials."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
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
    from ausdata_mcp.catalog_sources import PROVIDERS
    from ausdata_mcp.catalog_refresh import SCHEMA_VERSION
    path = Path(directory) / "sessions" / "smoke" / "catalogue" / "catalog.json"
    path.parent.mkdir(parents=True)
    entry = {"datasetId": "worldbank::NY.GDP.PCAP.CD", "route": "macro", "provider": "World Bank",
             "title": "GDP per capita NY.GDP.PCAP.CD", "description": "Protocol test fixture", "unit": "USD",
             "searchText": "GDP per capita NY.GDP.PCAP.CD", "sourceUrl": "https://data.worldbank.org/indicator/NY.GDP.PCAP.CD",
             "requiresMetadataBeforeRetrieval": True, "providerKey": "worldbank", "conceptId": "NY.GDP.PCAP.CD",
             "conceptLabel": "GDP per capita", "indicatorLabel": "GDP per capita", "providerConfig": {"series_id": "NY.GDP.PCAP.CD"}}
    sources = {p: {"status": "fresh", "entry_count": int(p == "World Bank"), "refresh_after": time.time() + 3600, "error": None} for p in PROVIDERS}
    path.write_text(json.dumps({"schema_version": SCHEMA_VERSION, "generation": "fixture", "entries": [entry],
                               "sources": sources, "lastUpdated": "fixture"}))


async def smoke(live: bool = False, live_pacific: bool = False, live_domestic: bool = False) -> dict:
    with tempfile.TemporaryDirectory(prefix="ausdata-smoke-") as directory:
        env = {key: value for key, value in os.environ.items()
               if not key.startswith(("OPENAI_", "NISABA_", "AUSDATA_"))}
        env.update(AUSDATA_RUNTIME_DIR=directory, PYTHON_DOTENV_DISABLED="1")
        if not live and not live_pacific and not live_domestic:
            seed_offline_catalogue(directory)
            env["AUSDATA_SESSION_ID"] = "smoke"
        parameters = StdioServerParameters(
            command=sys.executable, args=[str(ROOT / "scripts/run_mcp.py")],
            cwd=directory, env=env,
        )
        async with stdio_client(parameters) as (reader, writer):
            async with ClientSession(reader, writer, read_timeout_seconds=timedelta(seconds=600)) as session:
                initialized = await session.initialize()
                assert initialized.instructions == (ROOT / "AGENT_SYSTEM_PROMPT.md").read_text()
                listing = await session.list_tools()
                names = {tool.name for tool in listing.tools}
                assert names == {"search_catalog", "get_metadata", "retrieve"}, names
                resources = await session.list_resources()
                assert "ausdata://guide" in {str(item.uri) for item in resources.resources}
                guide = await session.read_resource("ausdata://guide")
                assert guide.contents[0].text == initialized.instructions
                prompt = await session.get_prompt("analyse_public_data", {"question": "Compare GDP"})
                assert "Compare GDP" in prompt.messages[0].content.text

                async def call(name, arguments):
                    result = await session.call_tool(name, arguments)
                    if result.isError:
                        raise RuntimeError(f"{name}: {result.content}")
                    payload = result.structuredContent or json.loads(result.content[0].text)
                    return payload.get("result", payload)

                found = await call("search_catalog", {"query": "NY.GDP.PCAP.CD", "provider": "World Bank", "limit": 5})
                assert found["candidates"]
                assert all("searchText" not in item for item in found["candidates"])
                metadata = await call("get_metadata", {"datasetId": "worldbank::NY.GDP.PCAP.CD"})
                assert metadata["source_identifiers"]["series_id"] == "NY.GDP.PCAP.CD"
                report = {"tools": sorted(names), "guidance": "verified", "search": "passed", "live": "not requested"}
                if live or live_pacific or live_domestic:
                    report["catalogue"] = found["catalogue"]
                    report["catalogue_warnings"] = found["warnings"]
                if live:
                    manifest = await call("retrieve", {"datasetId": "worldbank::NY.GDP.PCAP.CD", "countries": ["AUS", "NZL"], "startYear": 2020, "endYear": 2022})
                    payload = json.loads(Path(manifest["artifact_path"]).read_text(encoding="utf-8"))
                    assert {series["country_code"] for series in payload["series"]} == {"AUS", "NZL"}
                    assert sum(len(series["points"]) for series in payload["series"]) == 6
                    assert manifest["row_count"] == 6 and len(manifest["preview_rows"]) <= 3
                    assert manifest["artifact_bytes"] == Path(manifest["artifact_path"]).stat().st_size
                    assert manifest["artifact_size_mib"] == round(manifest["artifact_bytes"] / 1048576, 2)
                    assert manifest["large_artifact"] is False
                    assert manifest["retrieved_at"] and manifest["source_references"]
                    report["live"] = "World Bank: complete 6-observation artifact and compact manifest verified"
                if live_pacific:
                    found = await call("search_catalog", {"query": "energy", "provider": "PDH", "limit": 10})
                    dataset_id = next(item["datasetId"] for item in found["candidates"] if "::DF_ENERGY::" in item["datasetId"])
                    metadata = await call("get_metadata", {"datasetId": dataset_id})
                    assert "GEO_PICT" in metadata["key_order"]
                    geography = await call("get_metadata", {"datasetId": dataset_id, "dimension": "GEO_PICT", "codeSearch": "Fiji"})
                    assert any(code["code"] == "FJ" for code in geography["codes"])
                    indicators = await call("get_metadata", {"datasetId": dataset_id, "dimension": "INDICATOR", "codeSearch": "ENERGY_IND_001"})
                    assert indicators["codes"]
                    manifest = await call("retrieve", {"datasetId": dataset_id, "sourceFilters": {"GEO_PICT": ["FJ"], "INDICATOR": ["ENERGY_IND_001"]}})
                    payload = json.loads(Path(manifest["artifact_path"]).read_text(encoding="utf-8"))
                    assert manifest["row_count"] > 0
                    assert all(series["dimensions"]["GEO_PICT"]["code"] == "FJ" for series in payload["series"])
                    assert "UNIT_MULT" in manifest["attribute_ids"] and "OBS_STATUS" in manifest["attribute_ids"]
                    assert payload["source_references"] and payload["retrieved_at"]
                    count = found['catalogue']['sources']['Pacific Data Hub']['entry_count']
                    report["pacific_live"] = f"PDH: {count} datasets; metadata, codelists, retrieval and complete artifact verified"
                if live_domestic:
                    rba = await call("get_metadata", {"datasetId": "CUSTOM_AUS,RBA_F1,1.0"})
                    assert rba["codelists"]
                    identity = "CUSTOM_AUS,AES_TABLE_O,1.0"
                    metadata = await call("get_metadata", {"datasetId": identity})
                    assert metadata["codelists"]
                    manifest = await call("retrieve", {"datasetId": identity, "dataKey": "national_financial_year"})
                    payload = json.loads(Path(manifest["artifact_path"]).read_text(encoding="utf-8"))
                    rows = []
                    for series in payload["series"]:
                        for observation in series["observations"]:
                            dimensions = {**(series.get("dimensions") or {}), **(observation.get("dimensions") or {})}
                            category = dimensions.get("CATEGORY")
                            code = category.get("code") if isinstance(category, dict) else category
                            if code == "BLACK_COAL" and str(observation["observationKey"]).startswith("1989"):
                                rows.append((series, observation))
                    assert len(rows) == 1, [(series.get("dimensions"), series["observations"][:1]) for series in payload["series"][:3]]
                    assert rows[0][1]["value"] == 87573, rows
                    attributes = {**(rows[0][0].get("attributes") or {}), **(rows[0][1].get("attributes") or {})}
                    assert attributes["UNIT"] == "GWh", rows
                    assert payload["source_annotations"]["workbook_notes"]
                    report["domestic_live"] = "RBA live-link metadata; latest AES workbook metadata, retrieval, first-year value, units and source notes verified"
                return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--live-pacific", action="store_true")
    parser.add_argument("--live-domestic", action="store_true")
    args = parser.parse_args()
    print(json.dumps(asyncio.run(smoke(args.live, args.live_pacific, args.live_domestic)), indent=2))
