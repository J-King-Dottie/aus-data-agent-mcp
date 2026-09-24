#!/usr/bin/env python3

import argparse
import csv
import json
import re
from pathlib import Path


DATE_VALUE_RE = re.compile(r"^\d{1,2}[-/][A-Za-z0-9]{1,3}[-/]\d{2,4}$")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="RBA statistical tables CSV parser")
    parser.add_argument("command", choices=["metadata", "resolve"])
    parser.add_argument("--csv", required=True)
    parser.add_argument("--dataset-id", required=True)
    parser.add_argument("--agency-id", required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--name", required=True)
    parser.add_argument("--description", required=True)
    parser.add_argument("--curation-json", required=True)
    parser.add_argument("--data-key")
    parser.add_argument("--detail", default="full")
    return parser.parse_args()


def normalize_code(value: str) -> str:
    code = re.sub(r"[^A-Za-z0-9]+", "_", str(value or "").strip().upper()).strip("_")
    return code or "UNKNOWN"


def parse_float(value: str):
    text = str(value or "").strip().replace(",", "")
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def clean_text(value: str) -> str:
    text = str(value or "").replace("\ufeff", "").strip()
    text = text.replace("�", "-")
    text = re.sub(r"\s+", " ", text)
    return text.strip(" ,")


def is_date_like(value: str) -> bool:
    return bool(DATE_VALUE_RE.match(str(value or "").strip()))


def load_rows(csv_path: Path) -> list[list[str]]:
    with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
        return [list(row) for row in csv.reader(handle)]


def parse_table(rows: list[list[str]]) -> dict:
    if not rows:
        raise ValueError("RBA CSV file was empty")

    table_title = clean_text(rows[0][0] if rows[0] else "")
    metadata_rows: dict[str, list[str]] = {}
    source_row_seen = False
    data_start_idx = None

    for idx in range(1, len(rows)):
        row = rows[idx]
        first_cell = clean_text(row[0] if row else "")
        value_cells = [clean_text(cell) for cell in row[1:]]
        has_values = any(value_cells)

        if first_cell == "Series ID":
            metadata_rows["Series ID"] = value_cells
            data_start_idx = idx + 1
            break
        if first_cell in {"Title", "Description", "Frequency", "Type", "Units", "Source", "Publication date"}:
            metadata_rows[first_cell] = value_cells
            if first_cell == "Source":
                source_row_seen = True
            continue
        if not first_cell and source_row_seen and has_values and "Publication date" not in metadata_rows:
            non_blank = [item for item in value_cells if item]
            if non_blank and all(is_date_like(item) for item in non_blank):
                metadata_rows["Publication date"] = value_cells
                continue
        if first_cell and has_values and is_date_like(first_cell):
            data_start_idx = idx
            break

    if "Series ID" not in metadata_rows:
        raise ValueError("RBA CSV did not expose a Series ID row")

    series_ids = [clean_text(item) for item in metadata_rows.get("Series ID", [])]
    series_count = max(
        len(metadata_rows.get(key, []))
        for key in metadata_rows.keys()
    ) if metadata_rows else len(series_ids)

    series_metadata = []
    for idx in range(series_count):
        series_id = clean_text(series_ids[idx] if idx < len(series_ids) else "")
        title = clean_text(metadata_rows.get("Title", [])[idx] if idx < len(metadata_rows.get("Title", [])) else "")
        description = clean_text(metadata_rows.get("Description", [])[idx] if idx < len(metadata_rows.get("Description", [])) else "")
        frequency = clean_text(metadata_rows.get("Frequency", [])[idx] if idx < len(metadata_rows.get("Frequency", [])) else "")
        series_type = clean_text(metadata_rows.get("Type", [])[idx] if idx < len(metadata_rows.get("Type", [])) else "")
        unit = clean_text(metadata_rows.get("Units", [])[idx] if idx < len(metadata_rows.get("Units", [])) else "")
        source = clean_text(metadata_rows.get("Source", [])[idx] if idx < len(metadata_rows.get("Source", [])) else "")
        publication_date = clean_text(
            metadata_rows.get("Publication date", [])[idx]
            if idx < len(metadata_rows.get("Publication date", []))
            else ""
        )
        if not any([series_id, title, description, frequency, series_type, unit, source, publication_date]):
            continue
        series_metadata.append(
            {
                "column_index": idx + 1,
                "series_id": series_id or f"SERIES_{idx + 1}",
                "title": title or series_id or f"Series {idx + 1}",
                "description": description,
                "frequency": frequency,
                "type": series_type,
                "unit": unit,
                "source": source,
                "publication_date": publication_date,
            }
        )

    if not series_metadata:
        raise ValueError("RBA CSV did not expose any usable series metadata")

    data_rows = rows[data_start_idx:] if data_start_idx is not None else []
    return {
        "table_title": table_title,
        "series_metadata": series_metadata,
        "data_rows": data_rows,
    }


def build_metadata(args: argparse.Namespace, parsed: dict, curation: dict) -> dict:
    series_codes = []
    for item in parsed["series_metadata"]:
        series_codes.append(
            {
                "id": item["series_id"],
                "name": item["title"],
                "description": item["description"] or item["title"],
            }
        )

    table_code = clean_text((curation or {}).get("tableCode") or "")
    concepts = [
        {
            "id": "DATA_KEY",
            "name": "Custom retrieval key",
            "description": "Use dataKey equal to a Series ID to retrieve one series, or use all to retrieve the full RBA table.",
        },
        {
            "id": "SOURCE_URL",
            "name": "Source CSV URL",
            "description": args.description,
        },
    ]
    if table_code:
        concepts.append(
            {
                "id": "TABLE_CODE",
                "name": "RBA table code",
                "description": table_code,
            }
        )

    return {
        "dataStructure": {
            "id": args.dataset_id,
            "agencyID": args.agency_id,
            "version": args.version,
            "name": args.name,
            "description": (
                f"{args.description} Retrieve the full table with dataKey=all, or use a specific "
                "Series ID from the SERIES_IDS codelist to narrow to one series."
            ),
        },
        "dimensions": [
            {
                "id": "SERIES_ID",
                "position": 1,
                "conceptId": "SERIES_ID",
                "codelist": {"id": "SERIES_IDS"},
            }
        ],
        "attributes": [
            {"id": "UNIT", "attachmentLevel": "Series", "conceptId": "UNIT"},
            {"id": "FREQUENCY", "attachmentLevel": "Series", "conceptId": "FREQUENCY"},
            {"id": "TYPE", "attachmentLevel": "Series", "conceptId": "TYPE"},
            {"id": "SOURCE", "attachmentLevel": "Series", "conceptId": "SOURCE"},
            {"id": "PUBLICATION_DATE", "attachmentLevel": "Series", "conceptId": "PUBLICATION_DATE"},
        ],
        "codelists": [
            {
                "id": "SERIES_IDS",
                "name": "RBA table series ids",
                "codes": series_codes,
            }
        ],
        "concepts": concepts,
    }


def select_series(data_key: str, parsed: dict) -> list[dict]:
    series_items = list(parsed["series_metadata"])
    selected_key = clean_text(data_key or "all")
    if not selected_key or selected_key.lower() == "all":
        return series_items
    selected = [
        item
        for item in series_items
        if clean_text(item["series_id"]).upper() == selected_key.upper()
    ]
    if not selected:
        raise ValueError(f"Unknown RBA series id '{data_key}'")
    return selected


def build_resolved_dataset(args: argparse.Namespace, parsed: dict, curation: dict) -> dict:
    selected_series = select_series(args.data_key or "all", parsed)
    selected_by_column = {item["column_index"]: item for item in selected_series}

    dimensions_lookup = {
        "SERIES_ID": {},
        "TIME_PERIOD": {},
    }
    series_list = []
    observation_count = 0

    for item in selected_series:
        dimensions_lookup["SERIES_ID"][item["series_id"]] = item["title"]
        attributes = {
            "TITLE": item["title"],
            "DESCRIPTION": item["description"] or item["title"],
        }
        if item.get("frequency"):
            attributes["FREQUENCY"] = item["frequency"]
        if item.get("type"):
            attributes["TYPE"] = item["type"]
        if item.get("unit"):
            attributes["UNIT"] = item["unit"]
        if item.get("source"):
            attributes["SOURCE"] = item["source"]
        if item.get("publication_date"):
            attributes["PUBLICATION_DATE"] = item["publication_date"]

        series_list.append(
            {
                "seriesKey": item["series_id"],
                "dimensions": {
                    "SERIES_ID": {
                        "code": item["series_id"],
                        "label": item["title"],
                    }
                },
                "attributes": attributes,
                "observations": [],
            }
        )

    series_lookup = {item["seriesKey"]: item for item in series_list}

    for row in parsed["data_rows"]:
        if not row:
            continue
        period_label = clean_text(row[0] if len(row) > 0 else "")
        if not period_label or not is_date_like(period_label):
            continue
        dimensions_lookup["TIME_PERIOD"][period_label] = period_label
        for column_index, item in selected_by_column.items():
            raw_value = row[column_index] if column_index < len(row) else ""
            value = parse_float(raw_value)
            if value is None:
                continue
            series_lookup[item["series_id"]]["observations"].append(
                {
                    "observationKey": period_label,
                    "value": value,
                    "dimensions": {
                        "TIME_PERIOD": {
                            "code": normalize_code(period_label),
                            "label": period_label,
                        }
                    },
                }
            )
            observation_count += 1

    if observation_count == 0:
        raise ValueError(f"No records extracted for dataKey '{args.data_key or 'all'}'")

    table_code = clean_text((curation or {}).get("tableCode") or "")
    query = {
        "dataKey": args.data_key or "all",
        "detail": args.detail,
    }
    if table_code:
        query["tableCode"] = table_code

    return {
        "dataset": {
            "id": args.dataset_id,
            "agencyID": args.agency_id,
            "version": args.version,
            "name": args.name,
            "description": args.description,
        },
        "query": query,
        "dimensions": dimensions_lookup,
        "observationCount": observation_count,
        "series": series_list,
    }


def main() -> None:
    args = parse_args()
    curation = json.loads(args.curation_json)
    rows = load_rows(Path(args.csv))
    parsed = parse_table(rows)

    if args.command == "metadata":
        print(json.dumps(build_metadata(args, parsed, curation)))
        return

    print(json.dumps(build_resolved_dataset(args, parsed, curation)))


if __name__ == "__main__":
    main()
