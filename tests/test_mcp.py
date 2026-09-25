from __future__ import annotations

import asyncio
import json
import tempfile
import time
import unittest
from pathlib import Path
from threading import Barrier
from unittest.mock import patch

import httpx

from ausdata_mcp import macro_data
from ausdata_mcp import server as api
from ausdata_mcp import unified_catalog as catalog
from scripts.smoke_mcp import smoke


class CatalogueTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        self.snapshot = root / "catalog.json"
        self.index = root / "search.sqlite3"
        entries = [
            {
                "datasetId": f"test::{i}",
                "provider": provider,
                "title": title,
                "description": "",
                "searchText": title,
                "sourceUrl": "https://example.test",
                "requiresMetadataBeforeRetrieval": False,
            }
            for i, (provider, title) in enumerate(
                [
                    ("ABS", "Zebra labour wages"),
                    ("ABS", "Alpha labour"),
                    ("World Bank", "labour wages"),
                ]
            )
        ]
        self.snapshot.write_text(
            json.dumps(
                {
                    "schema_version": catalog.SCHEMA_VERSION,
                    "generation": "fixture",
                    "entries": entries,
                    "lastUpdated": "fixture",
                    "sources": {
                        provider: {
                            "status": "fresh",
                            "error": None,
                            "refresh_after": time.time() + 3600,
                        }
                        for provider in catalog.PROVIDERS
                    },
                }
            )
        )
        for name, value in (("CATALOG_PATH", self.snapshot), ("FTS_DB_PATH", self.index)):
            patcher = patch.object(catalog, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        catalog._invalidate_caches()
        self.addCleanup(catalog._invalidate_caches)

    def test_first_search_builds_index_and_orders_text_matches_without_scores(self):
        with patch.object(catalog, "refresh", side_effect=AssertionError("No network rebuild")):
            result = catalog.search_unified_catalog("labour wages", limit=2, provider="ABS")
        self.assertTrue(self.index.exists())
        self.assertEqual(result["candidates"][0]["title"], "Zebra labour wages")
        self.assertEqual(len(result["candidates"]), 2)
        self.assertEqual(result["total"], 2)
        self.assertIsNone(result["next_offset"])
        self.assertNotIn("score", result["candidates"][0])
        self.assertNotIn("rank", result["candidates"][0])
        self.assertNotIn("searchText", result["candidates"][0])

    def test_candidate_pool_paginates_in_fts_order(self):
        first = catalog.search_unified_catalog("labour wages", limit=1, provider="ABS")
        second = catalog.search_unified_catalog(
            "labour wages", limit=1, offset=first["next_offset"], provider="ABS"
        )
        self.assertEqual(
            [first["candidates"][0]["title"], second["candidates"][0]["title"]],
            ["Zebra labour wages", "Alpha labour"],
        )
        self.assertEqual(first["next_offset"], 1)
        self.assertIsNone(second["next_offset"])

    def test_query_punctuation_is_not_sql_or_fts(self):
        result = catalog.search_unified_catalog('labour " OR 1=1 --', provider="World Bank")
        self.assertTrue(all(item["provider"] == "World Bank" for item in result["candidates"]))


class ProviderTests(unittest.TestCase):
    def test_comtrade_requires_explicit_scope_before_network_request(self):
        entry = macro_data.MacroCatalogEntry(
            entry_id="comtrade::goods_trade",
            provider_key="comtrade",
            provider_name="UN Comtrade",
            concept_id="goods_trade",
            concept_label="Goods trade",
            indicator_label="Goods trade",
            unit="USD",
            provider_config={},
        )
        args = dict(
            reporter_codes=["36"],
            partner_codes=["0"],
            flow_code="",
            frequency_code="A",
            hs_codes=["TOTAL"],
            start_year=2020,
            end_year=2021,
        )
        with patch.object(
            macro_data.httpx, "get", side_effect=AssertionError("No provider request expected")
        ) as get:
            with self.assertRaisesRegex(ValueError, "flowCode"):
                macro_data._fetch_comtrade(entry, {}, **args)
            args["flow_code"] = "X"
            args["hs_codes"] = []
            with self.assertRaisesRegex(ValueError, "hsCodes"):
                macro_data._fetch_comtrade(entry, {}, **args)
            args["hs_codes"] = ["TOTAL"]
            args["end_year"] = None
            with self.assertRaisesRegex(ValueError, "startYear and endYear"):
                macro_data._fetch_comtrade(entry, {}, **args)
            get.assert_not_called()

    def test_comtrade_rejects_mixed_valid_and_invalid_codes(self):
        entry = macro_data.MacroCatalogEntry(
            entry_id="comtrade::goods_trade",
            provider_key="comtrade",
            provider_name="UN Comtrade",
            concept_id="goods_trade",
            concept_label="Goods trade",
            indicator_label="Goods trade",
            unit="USD",
            provider_config={},
        )
        args = dict(
            reporter_codes=["36"],
            partner_codes=["0"],
            flow_code="X",
            frequency_code="A",
            hs_codes=["TOTAL"],
            start_year=2022,
            end_year=2022,
        )
        with patch.object(
            macro_data.httpx, "get", side_effect=AssertionError("No provider request expected")
        ) as get:
            for field, invalid in (
                ("reporter_codes", "999999"),
                ("partner_codes", "999999"),
                ("hs_codes", "999999"),
            ):
                bad = {**args, field: [*args[field], invalid]}
                with (
                    self.subTest(field=field),
                    self.assertRaisesRegex(ValueError, "Choose codes from get_metadata"),
                ):
                    macro_data._fetch_comtrade(entry, {}, **bad)
            get.assert_not_called()

    def test_comtrade_metadata_covers_frequency_and_retrieval_keeps_scope(self):
        entry = macro_data.MacroCatalogEntry(
            entry_id="comtrade::goods_trade",
            provider_key="comtrade",
            provider_name="UN Comtrade",
            concept_id="goods_trade",
            concept_label="Goods trade",
            indicator_label="Goods trade",
            unit="USD",
            provider_config={},
        )
        metadata = macro_data._build_comtrade_metadata_payload("Australian exports", entry)
        frequency = next(item for item in metadata["dimensions"] if item["id"] == "FREQUENCY")
        self.assertEqual([option["code"] for option in frequency["options"]], ["A", "M"])
        row = {
            "period": 2022,
            "primaryValue": 100,
            "reporterCode": 36,
            "partnerCode": 0,
            "cmdCode": "TOTAL",
            "reporterDesc": "Australia",
            "partnerDesc": "World",
        }
        response = httpx.Response(
            200,
            request=httpx.Request("GET", "https://example.test/trade"),
            json={"data": [row, row]},
        )
        with patch.object(macro_data.httpx, "get", return_value=response) as get:
            result = macro_data._fetch_comtrade(
                entry,
                {},
                reporter_codes=["36"],
                partner_codes=["0"],
                flow_code="X",
                frequency_code="A",
                hs_codes=["TOTAL"],
                start_year=2022,
                end_year=2022,
            )
        self.assertEqual(
            [{"x": point["x"], "y": point["y"]} for point in result["series"][0]["points"]],
            [{"x": "2022", "y": 100.0}],
        )
        self.assertEqual(result["query_parameters"]["partnerCodes"], ["0"])
        self.assertNotIn("typeCode", get.call_args.kwargs["params"])

        with patch.object(macro_data.settings, "comtrade_api_key", "dummy-test-key"):
            with patch.object(macro_data.httpx, "get", return_value=response) as authenticated_get:
                authenticated = macro_data._fetch_comtrade(
                    entry,
                    {},
                    reporter_codes=["36"],
                    partner_codes=["0"],
                    flow_code="X",
                    frequency_code="A",
                    hs_codes=["TOTAL"],
                    start_year=2022,
                    end_year=2022,
                )
        self.assertEqual(
            authenticated_get.call_args.kwargs["headers"]["Ocp-Apim-Subscription-Key"],
            "dummy-test-key",
        )
        self.assertNotIn("subscription-key", authenticated_get.call_args.kwargs["params"])
        self.assertNotIn("dummy-test-key", authenticated["api_request_url"])

        conflicting = httpx.Response(
            200,
            request=httpx.Request("GET", "https://example.test/trade"),
            json={"data": [row, {**row, "primaryValue": 101}]},
        )
        with patch.object(macro_data.httpx, "get", return_value=conflicting):
            with self.assertRaisesRegex(RuntimeError, "conflicting values"):
                macro_data._fetch_comtrade(
                    entry,
                    {},
                    reporter_codes=["36"],
                    partner_codes=["0"],
                    flow_code="X",
                    frequency_code="A",
                    hs_codes=["TOTAL"],
                    start_year=2022,
                    end_year=2022,
                )

    def test_oecd_rejects_ambiguous_dimensions(self):
        entry = macro_data.MacroCatalogEntry(
            entry_id="oecd::TEST",
            provider_key="oecd",
            provider_name="OECD",
            concept_id="TEST",
            concept_label="Test",
            indicator_label="Test",
            unit="",
            provider_config={},
        )
        csv_text = "REF_AREA,TIME_PERIOD,OBS_VALUE,MEASURE\nAUS,2022,1,A\nAUS,2022,2,B\n"
        response = httpx.Response(
            200, request=httpx.Request("GET", "https://example.test/oecd"), text=csv_text
        )
        config = {"agency": "OECD.TEST", "dataflow": "TEST", "version": "1.0"}
        with patch.object(macro_data.httpx, "get", return_value=response):
            with self.assertRaisesRegex(
                RuntimeError, "multiple observations for the same country and period"
            ):
                macro_data._fetch_oecd(entry, config, ["AUS"], 2022, 2022)

    def test_oecd_reads_unambiguous_series(self):
        entry = macro_data.MacroCatalogEntry(
            entry_id="oecd::TEST",
            provider_key="oecd",
            provider_name="OECD",
            concept_id="TEST",
            concept_label="Test",
            indicator_label="Test",
            unit="Index",
            provider_config={},
        )
        csv_text = "REF_AREA,TIME_PERIOD,OBS_VALUE,MEASURE,FREQ,Unit of measure,UNIT_MULT\nAUS,2021,1,A,A,Litres per person,0\nAUS,2022,2,A,A,Litres per person,0\n"
        response = httpx.Response(
            200, request=httpx.Request("GET", "https://example.test/oecd"), text=csv_text
        )
        config = {"agency": "OECD.TEST", "dataflow": "TEST", "version": "1.0"}
        with patch.object(macro_data.httpx, "get", return_value=response):
            result = macro_data._fetch_oecd(entry, config, ["AUS"], 2021, 2022)
        self.assertEqual(
            [{"x": point["x"], "y": point["y"]} for point in result["series"][0]["points"]],
            [{"x": "2021", "y": 1.0}, {"x": "2022", "y": 2.0}],
        )
        self.assertEqual(result["series"][0]["unit"], "Litres per person")
        self.assertEqual(result["series"][0]["frequency"], "annual")

    def test_world_bank_requests_countries_and_all_pages(self):
        entry = macro_data.MacroCatalogEntry(
            entry_id="test",
            provider_key="worldbank",
            provider_name="World Bank",
            concept_id="test",
            concept_label="Test",
            indicator_label="Test",
            unit="USD",
            provider_config={},
        )

        def response(year, page):
            return httpx.Response(
                200,
                request=httpx.Request("GET", f"https://example.test?page={page}"),
                json=[{"pages": 2}, [{"countryiso3code": "AUS", "date": year, "value": 2}]],
            )

        with patch.object(
            macro_data.httpx, "get", side_effect=[response("2021", 1), response("2020", 2)]
        ) as get:
            result = macro_data._fetch_world_bank(
                entry, {"series_id": "TEST", "source_id": "2"}, ["AUS"], 2020, 2021
            )
        self.assertIn("/country/AUS/", get.call_args_list[0].args[0])
        self.assertEqual(len(result["series"][0]["points"]), 2)
        self.assertEqual(len(result["api_request_urls"]), 2)
        self.assertEqual(get.call_args_list[1].kwargs["params"]["page"], 2)
        self.assertEqual(get.call_args_list[1].kwargs["params"]["source"], "2")


class ProtocolTests(unittest.TestCase):
    def test_registered_tools_do_not_block_independent_requests(self):
        barrier = Barrier(2)

        def search(*args, **kwargs):
            barrier.wait(timeout=3)
            return {"candidates": [], "total": 0}

        async def concurrent():
            return await asyncio.wait_for(
                asyncio.gather(
                    api.server.call_tool("search_catalog", {"query": "one"}),
                    api.server.call_tool("search_catalog", {"query": "two"}),
                ),
                timeout=5,
            )

        with patch.object(api, "search_unified_catalog", side_effect=search):
            self.assertEqual(len(asyncio.run(concurrent())), 2)

    def test_real_stdio_without_model_credentials(self):
        result = asyncio.run(smoke())
        self.assertEqual(result["search"], "passed")


if __name__ == "__main__":
    unittest.main()
