"""Parse the official source file into metadata and observation records."""

import csv
import io
import math
import re
from datetime import datetime

DATE_VALUE_RE = re.compile(r"^\d{1,2}[-/][A-Za-z0-9]{1,3}[-/]\d{2,4}$")


def parse_period(value: str) -> str:
    for pattern in (
        "%d-%b-%Y",
        "%d/%b/%Y",
        "%d/%m/%Y",
        "%d-%m-%Y",
        "%d-%b-%y",
        "%d/%b/%y",
        "%d/%m/%y",
        "%d-%m-%y",
    ):
        try:
            return datetime.strptime(value, pattern).date().isoformat()
        except ValueError:
            continue
    raise ValueError(f"Unrecognized RBA observation date: {value!r}.")


def parse_float(value: str):
    text = str(value or "").strip().replace(",", "")
    if not text:
        return None
    try:
        number = float(text)
        return number if math.isfinite(number) else None
    except ValueError:
        return None


def clean_text(value: str) -> str:
    text = str(value or "").replace("\ufeff", "").strip()
    text = text.replace("�", "-")
    text = re.sub(r"\s+", " ", text)
    return text.strip(" ,")


def is_date_like(value: str) -> bool:
    return bool(DATE_VALUE_RE.match(str(value or "").strip()))


def load_rows(content: bytes) -> list[list[str]]:
    return list(csv.reader(io.StringIO(content.decode("utf-8-sig"))))


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
        if first_cell in {
            "Title",
            "Description",
            "Frequency",
            "Type",
            "Units",
            "Source",
            "Publication date",
        }:
            metadata_rows[first_cell] = value_cells
            if first_cell == "Source":
                source_row_seen = True
            continue
        if (
            not first_cell
            and source_row_seen
            and has_values
            and "Publication date" not in metadata_rows
        ):
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
    series_count = (
        max(len(metadata_rows.get(key, [])) for key in metadata_rows.keys())
        if metadata_rows
        else len(series_ids)
    )

    series_metadata = []
    for idx in range(series_count):
        series_id = clean_text(series_ids[idx] if idx < len(series_ids) else "")
        title = clean_text(
            metadata_rows.get("Title", [])[idx] if idx < len(metadata_rows.get("Title", [])) else ""
        )
        description = clean_text(
            metadata_rows.get("Description", [])[idx]
            if idx < len(metadata_rows.get("Description", []))
            else ""
        )
        frequency = clean_text(
            metadata_rows.get("Frequency", [])[idx]
            if idx < len(metadata_rows.get("Frequency", []))
            else ""
        )
        series_type = clean_text(
            metadata_rows.get("Type", [])[idx] if idx < len(metadata_rows.get("Type", [])) else ""
        )
        unit = clean_text(
            metadata_rows.get("Units", [])[idx] if idx < len(metadata_rows.get("Units", [])) else ""
        )
        source = clean_text(
            metadata_rows.get("Source", [])[idx]
            if idx < len(metadata_rows.get("Source", []))
            else ""
        )
        publication_date = clean_text(
            metadata_rows.get("Publication date", [])[idx]
            if idx < len(metadata_rows.get("Publication date", []))
            else ""
        )
        if not any(
            [series_id, title, description, frequency, series_type, unit, source, publication_date]
        ):
            continue
        if not series_id:
            raise ValueError(f"RBA CSV column {idx + 1} has metadata but no Series ID.")
        if any(item["series_id"] == series_id for item in series_metadata):
            raise ValueError(f"RBA CSV contains duplicate Series ID {series_id}.")
        series_metadata.append(
            {
                "column_index": idx + 1,
                "series_id": series_id,
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


def build_metadata(flow: dict, parsed: dict, curation: dict) -> dict:
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
            "description": flow["description"],
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
            "id": flow["id"],
            "agencyID": flow["agencyID"],
            "version": flow["version"],
            "name": flow["name"],
            "description": (
                f"{flow['description']} Retrieve the full table with dataKey=all, or use a specific "
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
            {
                "id": "PUBLICATION_DATE",
                "attachmentLevel": "Series",
                "conceptId": "PUBLICATION_DATE",
            },
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


def build_resolved_dataset(flow: dict, parsed: dict, curation: dict, data_key: str = "all") -> dict:
    selected_series = select_series(data_key or "all", parsed)
    selected_by_column = {item["column_index"]: item for item in selected_series}
    source_column_count = max(item["column_index"] for item in parsed["series_metadata"])

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
        if not period_label and not any(clean_text(cell) for cell in row):
            continue
        period = parse_period(period_label)
        if period in dimensions_lookup["TIME_PERIOD"]:
            raise ValueError(f"RBA CSV contains duplicate observation date {period}.")
        dimensions_lookup["TIME_PERIOD"][period] = period_label
        if any(clean_text(cell) for cell in row[source_column_count + 1 :]):
            raise ValueError("RBA CSV contains observation columns without Series ID metadata.")
        for column_index, item in selected_by_column.items():
            raw_value = row[column_index] if column_index < len(row) else ""
            attributes = {"OBS_VALUE_RAW": raw_value}
            # Official tables omit trailing empty fields, including date-only
            # rows for unpublished periods. Preserve their missingness explicitly.
            if column_index >= len(row):
                attributes["SOURCE_CELL_OMITTED"] = True
            value = parse_float(raw_value)
            series_lookup[item["series_id"]]["observations"].append(
                {
                    "observationKey": period,
                    "value": value,
                    "attributes": attributes,
                    "dimensions": {
                        "TIME_PERIOD": {
                            "code": period,
                            "label": period_label,
                        }
                    },
                }
            )
            observation_count += 1

    if observation_count == 0:
        raise ValueError(f"No records extracted for dataKey '{data_key or 'all'}'")

    table_code = clean_text((curation or {}).get("tableCode") or "")
    query = {
        "dataKey": data_key or "all",
        "detail": "full",
    }
    if table_code:
        query["tableCode"] = table_code

    return {
        "dataset": {
            "id": flow["id"],
            "agencyID": flow["agencyID"],
            "version": flow["version"],
            "name": flow["name"],
            "description": flow["description"],
        },
        "query": query,
        "dimensions": dimensions_lookup,
        "observationCount": observation_count,
        "series": series_list,
    }
