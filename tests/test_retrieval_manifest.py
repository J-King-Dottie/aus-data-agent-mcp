import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ausdata_mcp import server as api


class RetrievalManifestTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        patcher = patch.object(api, "RUNTIME_DIR", self.root)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_macro_manifest_is_bounded_but_file_is_complete(self):
        points = [{"x": str(year), "y": year} for year in range(2000, 2025)]
        payload = {"provider": "World Bank", "selected_indicator": {"entry_id": "worldbank::TEST"},
                   "series": [{"country_code": "AUS", "series_id": "TEST", "frequency": "annual",
                               "unit": "USD", "points": points}],
                   "source_references": [{"source_url": "https://example.test/data"}]}
        manifest = api._store_retrieval(payload, "worldbank::TEST", "Test")
        saved = json.loads(Path(manifest["artifact_path"]).read_text(encoding="utf-8"))
        self.assertTrue(Path(manifest["artifact_path"]).is_absolute())
        self.assertEqual(manifest["artifact_format"], "json")
        self.assertEqual(manifest["artifact_bytes"], Path(manifest["artifact_path"]).stat().st_size)
        self.assertFalse(manifest["large_artifact"])
        self.assertIsNone(manifest["size_notice"])
        self.assertEqual(manifest["row_count"], 25)
        self.assertEqual(len(manifest["preview_rows"]), 3)
        self.assertTrue(manifest["preview_truncated"])
        self.assertEqual(len(saved["series"][0]["points"]), 25)
        self.assertEqual((manifest["period_start"], manifest["period_end"]), ("2000", "2024"))
        self.assertEqual(manifest["unit_examples"], ["USD"])
        self.assertNotIn("series", manifest)

    def test_large_artifact_manifest_prompts_size_notice(self):
        payload = {"series": [{"seriesKey": "test", "observations": [{"observationKey": "2024", "value": 1}]}]}
        with patch.object(api, "LARGE_ARTIFACT_BYTES", 1):
            manifest = api._store_retrieval(payload, "ABS,TEST,1.0", "Test")
        self.assertTrue(manifest["large_artifact"])
        self.assertIn("large local file", manifest["size_notice"])
        self.assertEqual(manifest["artifact_size_mib"], round(manifest["artifact_bytes"] / 1048576, 2))

    def test_domestic_manifest_preserves_raw_dimensions_and_attributes(self):
        payload = {"kind": "pacific_retrieve", "provider": "Pacific Data Hub",
                   "series": [{"seriesKey": "A.FJ.POP", "dimensions": {"GEO_PICT": {"code": "FJ"}},
                               "observations": [
                                   {"observationKey": "2020", "value": 1.25,
                                    "attributes": {"UNIT_MULT": "3", "OBS_STATUS": "A"}},
                                   {"observationKey": "2021", "value": None,
                                    "attributes": {"UNIT_MULT": "3", "OBS_STATUS": "M"}},
                               ]}], "source_annotations": {"NonProductionDataflow": ["true"]}}
        manifest = api._store_retrieval(payload, "pdh::TEST", "Population")
        saved = json.loads(Path(manifest["artifact_path"]).read_text(encoding="utf-8"))
        self.assertEqual(manifest["row_count"], 2)
        self.assertEqual(manifest["missing_value_count"], 1)
        self.assertIn("GEO_PICT", manifest["dimension_ids"])
        self.assertIn("OBS_STATUS", manifest["attribute_ids"])
        self.assertEqual(manifest["unit_multiplier_codes"], ["3"])
        self.assertEqual(saved["series"][0]["observations"][1]["attributes"]["OBS_STATUS"], "M")
        self.assertEqual(saved["source_annotations"]["NonProductionDataflow"], ["true"])

    def test_abs_manifest_uses_time_dimension_instead_of_observation_index(self):
        payload = {"series": [{"seriesKey": "test", "dimensions": {"SEX": {"code": "1"}},
                               "observations": [
                                   {"observationKey": "0", "value": 11.2,
                                    "dimensions": {"TIME_PERIOD": {"code": "2025-12"}}},
                                   {"observationKey": "1", "value": 10.8,
                                    "dimensions": {"TIME_PERIOD": {"code": "2026-01"}}},
                               ]}]}
        manifest = api._store_retrieval(payload, "ABS,LF_AGES,1.0.0", "Labour Force")
        self.assertEqual((manifest["period_start"], manifest["period_end"]), ("2025-12", "2026-01"))
        self.assertEqual(manifest["preview_rows"][0]["period"], "2025-12")

    def test_domestic_retrieval_adds_citable_source_provenance(self):
        entry = {"provider": "ABS", "sourceUrl": "https://example.test/dataset"}
        with patch.object(api, "_route_entry", return_value=entry), patch.object(api, "get_domestic_service") as service:
            service.return_value.resolve_dataset.return_value = {
                "dataset": {"id": "test"}, "series": [],
                "api_request_url": "https://example.test/data", "source_references": [],
            }
            manifest = api.retrieve("CUSTOM_AUS,TEST,1.0")
        self.assertEqual(manifest["source_references"][0]["source_url"], entry["sourceUrl"])
        self.assertEqual(manifest["source_references"][0]["api_request_url"], "https://example.test/data")
        self.assertTrue(manifest["retrieved_at"])
        self.assertTrue(Path(manifest["artifact_path"]).is_file())
