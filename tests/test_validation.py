"""Data-integrity failures must be errors, never silently successful slices."""

import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

from ausdata_mcp import artifacts, macro_data, server
from scripts import build_comtrade_metadata


def entry(provider):
    return macro_data.MacroCatalogEntry(
        entry_id=f"{provider}::TEST",
        provider_key=provider,
        provider_name=provider,
        concept_id="TEST",
        concept_label="Test",
        indicator_label="Test",
        unit="USD",
        provider_config={},
    )


def response(payload):
    return httpx.Response(
        200, request=httpx.Request("GET", "https://example.test/data"), json=payload
    )


class MacroValidationTests(unittest.TestCase):
    def test_missing_requested_countries_fail_for_each_macro_provider(self):
        cases = [
            (
                macro_data._fetch_world_bank,
                [
                    {"pages": 1, "total": 1},
                    [{"countryiso3code": "AUS", "date": "2022", "value": 1}],
                ],
                {"series_id": "TEST"},
            ),
            (
                macro_data._fetch_imf,
                {"values": {"TEST": {"AUS": {"2022": 1}}}},
                {"series_id": "TEST"},
            ),
            (
                macro_data._fetch_oecd,
                "REF_AREA,TIME_PERIOD,OBS_VALUE\nAUS,2022,1\n",
                {"agency": "OECD.TEST", "dataflow": "TEST"},
            ),
        ]
        for fetch, payload, config in cases:
            reply = (
                response(payload)
                if not isinstance(payload, str)
                else httpx.Response(
                    200, request=httpx.Request("GET", "https://example.test"), text=payload
                )
            )
            with (
                self.subTest(provider=fetch.__name__),
                patch.object(macro_data.httpx, "get", return_value=reply),
            ):
                with self.assertRaisesRegex(RuntimeError, "NZL.*partial retrieval"):
                    fetch(entry("test"), config, ["AUS", "NZL"], 2022, 2022)

    def test_invalid_scope_fails_before_network(self):
        with patch.object(
            macro_data.httpx, "get", side_effect=AssertionError("No request expected")
        ):
            for fetch in (
                macro_data._fetch_world_bank,
                macro_data._fetch_imf,
                macro_data._fetch_oecd,
            ):
                for countries, all_countries, start, end in [
                    (["AUS", ""], False, 2020, 2022),
                    (["AUS"], True, 2020, 2022),
                    (["AUS"], False, 2023, 2022),
                    (["AUS"], False, 0, 2022),
                ]:
                    with (
                        self.subTest(fetch=fetch.__name__, countries=countries),
                        self.assertRaises(ValueError),
                    ):
                        fetch(entry("test"), {}, countries, start, end, all_countries=all_countries)

    def test_world_bank_rejects_incomplete_pagination(self):
        row = {"countryiso3code": "AUS", "date": "2022", "value": 1}
        for replies in (
            [response([{"pages": 1, "total": 2}, [row]])],
            [response([{"pages": 2}, [row]]), response([{"pages": 2}, []])],
        ):
            with (
                self.subTest(replies=replies),
                patch.object(macro_data.httpx, "get", side_effect=replies),
            ):
                with self.assertRaisesRegex(
                    RuntimeError, "incomplete data|pagination was incomplete"
                ):
                    macro_data._fetch_world_bank(
                        entry("worldbank"), {"series_id": "TEST"}, ["AUS"], 2022, 2022
                    )

    def test_world_bank_rejects_wrong_slice_and_duplicate_points(self):
        row = {"countryiso3code": "AUS", "date": "2022", "value": 1}
        for rows in ([row, row], [row, {**row, "countryiso3code": "NZL"}]):
            with patch.object(macro_data.httpx, "get", return_value=response([{"pages": 1}, rows])):
                with self.assertRaisesRegex(
                    RuntimeError, "duplicate observations|outside the requested"
                ):
                    macro_data._fetch_world_bank(
                        entry("worldbank"), {"series_id": "TEST"}, ["AUS"], 2022, 2022
                    )

    def test_world_bank_retains_null_values_and_source_flags(self):
        rows = [
            {
                "countryiso3code": "AUS",
                "date": "2022",
                "value": None,
                "obs_status": "M",
                "unit": "%",
            }
        ]
        with patch.object(
            macro_data.httpx, "get", return_value=response([{"pages": 1, "total": 1}, rows])
        ):
            result = macro_data._fetch_world_bank(
                entry("worldbank"), {"series_id": "TEST"}, ["aus", "AUS"], 2022, 2022
            )
        self.assertEqual(len(result["series"]), 1)
        self.assertIsNone(result["series"][0]["points"][0]["y"])
        self.assertEqual(result["series"][0]["points"][0]["source_row"]["obs_status"], "M")

    def test_oecd_retains_suppression_and_rejects_ambiguous_null_rows(self):
        for extra in ("", "AUS,2022,1,B,A\n"):
            text = "REF_AREA,TIME_PERIOD,OBS_VALUE,MEASURE,OBS_STATUS\nAUS,2022,..,A,C\n" + extra
            reply = httpx.Response(
                200, request=httpx.Request("GET", "https://example.test"), text=text
            )
            with patch.object(macro_data.httpx, "get", return_value=reply):
                if extra:
                    with self.assertRaisesRegex(RuntimeError, "multiple observations"):
                        macro_data._fetch_oecd(
                            entry("oecd"),
                            {"agency": "OECD.TEST", "dataflow": "TEST"},
                            ["AUS"],
                            2022,
                            2022,
                        )
                else:
                    result = macro_data._fetch_oecd(
                        entry("oecd"),
                        {"agency": "OECD.TEST", "dataflow": "TEST"},
                        ["AUS"],
                        2022,
                        2022,
                    )
                    point = result["series"][0]["points"][0]
                    self.assertIsNone(point["y"])
                    self.assertEqual(point["source_row"]["OBS_STATUS"], "C")

    def test_comtrade_does_not_hide_empty_or_invalid_chunks(self):
        row = {
            "period": 2022,
            "primaryValue": 1,
            "reporterCode": 36,
            "partnerCode": 0,
            "cmdCode": "TOTAL",
            "flowCode": "X",
        }
        for invalid in ({"data": []}, {"error": "failed"}):
            with patch.object(
                macro_data.httpx, "get", side_effect=[response({"data": [row]}), response(invalid)]
            ):
                with self.assertRaisesRegex(RuntimeError, "partial retrieval|incomplete data"):
                    macro_data._fetch_comtrade(
                        entry("comtrade"),
                        {},
                        reporter_codes=["36", "554"],
                        partner_codes=["0"],
                        flow_code="X",
                        frequency_code="A",
                        hs_codes=["TOTAL"],
                        start_year=2022,
                        end_year=2022,
                    )

    def test_comtrade_rejects_wrong_world_partner_instead_of_replacing_zero(self):
        row = {
            "period": 2022,
            "primaryValue": 1,
            "reporterCode": 36,
            "partnerCode": 0,
            "cmdCode": "TOTAL",
            "flowCode": "X",
        }
        with patch.object(macro_data.httpx, "get", return_value=response({"data": [row]})):
            with self.assertRaisesRegex(RuntimeError, "outside the requested codes"):
                macro_data._fetch_comtrade(
                    entry("comtrade"),
                    {},
                    reporter_codes=["36"],
                    partner_codes=["554"],
                    flow_code="X",
                    frequency_code="A",
                    hs_codes=["TOTAL"],
                    start_year=2022,
                    end_year=2022,
                )


class ComtradeReferenceTests(unittest.TestCase):
    def test_world_total_is_a_partner_not_a_reporter(self):
        metadata = macro_data._build_comtrade_metadata_payload("trade", entry("comtrade"))
        dimensions = {dimension["id"]: dimension for dimension in metadata["dimensions"]}
        self.assertNotIn("0", {option["code"] for option in dimensions["REPORTER"]["options"]})
        self.assertIn("0", {option["code"] for option in dimensions["PARTNER"]["options"]})
        self.assertEqual(metadata["fixed_dimensions"]["motCode"], "0")
        for dimension in ("HS_2DIGIT", "HS_4DIGIT"):
            self.assertIn("matchesTruncated", dimensions[dimension])

    def test_failed_reference_refresh_preserves_existing_file(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "reference.json"
            target.write_text("original")
            with (
                patch.object(build_comtrade_metadata, "OUTPUT_PATH", target),
                patch.object(build_comtrade_metadata, "_fetch_json", return_value={"results": []}),
            ):
                with self.assertRaisesRegex(ValueError, "import and export"):
                    build_comtrade_metadata.main()
            self.assertEqual(target.read_text(), "original")

    def test_comtrade_requests_only_supported_total_breakdowns(self):
        row = {
            "period": 2022,
            "primaryValue": 1,
            "reporterCode": 36,
            "partnerCode": 0,
            "cmdCode": "TOTAL",
            "flowCode": "X",
            "partner2Code": 0,
            "motCode": 0,
            "customsCode": "C00",
        }
        arguments = dict(
            reporter_codes=["36"],
            partner_codes=["0"],
            flow_code="X",
            frequency_code="A",
            hs_codes=["TOTAL"],
            start_year=2022,
            end_year=2022,
        )
        with patch.object(macro_data.httpx, "get", return_value=response({"data": [row]})) as get:
            macro_data._fetch_comtrade(entry("comtrade"), {}, **arguments)
        self.assertEqual(get.call_args.kwargs["params"]["motCode"], "0")
        self.assertEqual(get.call_args.kwargs["params"]["customsCode"], "C00")
        with patch.object(
            macro_data.httpx, "get", return_value=response({"data": [{**row, "motCode": 9900}]})
        ):
            with self.assertRaisesRegex(RuntimeError, "non-total motCode"):
                macro_data._fetch_comtrade(entry("comtrade"), {}, **arguments)


class ToolBoundaryTests(unittest.TestCase):
    def test_unused_parameters_are_rejected_before_retrieval(self):
        cases = [
            ("CUSTOM_AUS,RBA_F1,1.0", {"startPeriod": "2020"}),
            ("worldbank::TEST", {"startPeriod": "2020"}),
            ("ABS,TEST,1.0", {"countries": ["NZL"]}),
            ("comtrade::goods_trade", {"countries": ["AUS"]}),
            ("pdh::SPC::TEST::1.0", {"startYear": 2020}),
        ]
        with patch.object(server, "_route_entry", return_value={}):
            for dataset_id, arguments in cases:
                with (
                    self.subTest(dataset=dataset_id),
                    self.assertRaisesRegex(ValueError, "do not apply"),
                ):
                    server.retrieve(dataset_id, **arguments)

    def test_empty_comtrade_codes_are_not_removed_before_validation(self):
        record = {"providerKey": "comtrade", "datasetId": "comtrade::goods_trade"}
        with patch.object(server, "_route_entry", return_value=record):
            with self.assertRaisesRegex(ValueError, "Invalid reporterCodes"):
                server.retrieve(
                    "comtrade::goods_trade",
                    reporterCodes=["36", ""],
                    partnerCodes=["0"],
                    flowCode="X",
                    frequencyCode="A",
                    hsCodes=["TOTAL"],
                    startYear=2022,
                    endYear=2022,
                )

    def test_empty_evidence_and_nonfinite_json_leave_no_artifact(self):
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.object(artifacts, "RUNTIME_DIR", Path(directory)),
        ):
            for payload in (
                {"series": []},
                {"series": [{"observations": []}]},
                {"series": [{"observations": [{"observationKey": "2020", "value": float("nan")}]}]},
            ):
                with self.subTest(payload=payload), self.assertRaises((RuntimeError, ValueError)):
                    artifacts.store_retrieval(payload, "TEST", "Test")
            self.assertEqual(list(Path(directory).rglob("*.json")), [])
            self.assertEqual(list(Path(directory).rglob("*.tmp")), [])

    def test_zero_multiplier_and_domestic_frequency_are_in_manifest(self):
        payload = {
            "series": [
                {
                    "dimensions": {"FREQ": {"code": "M"}},
                    "attributes": {"UNIT_MULT": {"code": 0}},
                    "observations": [{"observationKey": "2024-01", "value": 1}],
                }
            ]
        }
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.object(artifacts, "RUNTIME_DIR", Path(directory)),
        ):
            manifest = artifacts.store_retrieval(payload, "TEST", "Test")
            self.assertEqual(manifest["unit_multiplier_codes"], ["0"])
            self.assertEqual(manifest["frequency_examples"], ["M"])


class MCPArgumentTests(unittest.TestCase):
    def test_unknown_arguments_fail_before_source_access(self):
        async def check():
            with patch.object(server, "search_unified_catalog") as search:
                with self.assertRaisesRegex(Exception, "Unknown arguments.*provder"):
                    await server.server.call_tool(
                        "search_catalog", {"query": "test", "provder": "ABS"}
                    )
                search.assert_not_called()
            tools = await server.server.list_tools()
            self.assertEqual(len(tools), 3)
            for tool in tools:
                self.assertFalse(tool.inputSchema["additionalProperties"])

        asyncio.run(check())
