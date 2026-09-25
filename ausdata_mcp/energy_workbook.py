"""Parse the official source file into metadata and observation records."""

import math
import posixpath
import re
import xml.etree.ElementTree as ET
import zipfile
from collections import defaultdict
from pathlib import Path
from typing import BinaryIO

NS = {
    "a": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
}
STATE_CODES = {"NSW", "VIC", "QLD", "WA", "SA", "TAS", "NT", "AUS"}


def column_letters(cell_ref: str) -> str:
    out = []
    for ch in cell_ref:
        if ch.isalpha():
            out.append(ch)
        else:
            break
    return "".join(out)


def column_number(column_ref: str) -> int:
    value = 0
    for ch in str(column_ref or "").upper():
        if not ("A" <= ch <= "Z"):
            break
        value = value * 26 + (ord(ch) - ord("A") + 1)
    return value


def parse_float(value: str):
    text = str(value or "").strip().replace(",", "")
    if not text:
        return None
    try:
        number = float(text)
        return number if math.isfinite(number) else None
    except ValueError:
        return None


def normalize_code(value: str) -> str:
    code = re.sub(r"[^A-Za-z0-9]+", "_", str(value or "").strip().upper()).strip("_")
    return code or "UNKNOWN"


def load_workbook(xlsx_path: Path | BinaryIO):
    with zipfile.ZipFile(xlsx_path) as workbook:
        shared_strings = []
        if "xl/sharedStrings.xml" in workbook.namelist():
            root = ET.fromstring(workbook.read("xl/sharedStrings.xml"))
            for item in root:
                text = "".join(
                    node.text or ""
                    for node in item.iter(
                        "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}t"
                    )
                )
                shared_strings.append(text)

        rel_root = ET.fromstring(workbook.read("xl/_rels/workbook.xml.rels"))
        relationships = {
            item.attrib["Id"]: item.attrib["Target"] for item in rel_root if item.attrib.get("Id")
        }
        wb_root = ET.fromstring(workbook.read("xl/workbook.xml"))
        sheets = {}
        for sheet in wb_root.find("a:sheets", NS):
            name = sheet.attrib.get("name", "")
            rid = sheet.attrib.get(
                "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"
            )
            target = relationships.get(rid, "")
            sheet_path = posixpath.normpath(
                target.lstrip("/") if target.startswith("/") else f"xl/{target}"
            )
            if not sheet_path.startswith("xl/"):
                raise ValueError("Workbook contains an invalid worksheet relationship.")
            sheet_root = ET.fromstring(workbook.read(sheet_path))
            rows = []
            for row in sheet_root.findall(".//a:sheetData/a:row", NS):
                row_values = {}
                for cell in row.findall("a:c", NS):
                    ref = cell.attrib.get("r", "")
                    col = column_letters(ref)
                    cell_type = cell.attrib.get("t")
                    value_node = cell.find("a:v", NS)
                    value = ""
                    if cell_type == "inlineStr":
                        value = "".join(item.text or "" for item in cell.findall(".//a:t", NS))
                    elif value_node is not None and value_node.text is not None:
                        if cell_type == "s":
                            value = shared_strings[int(value_node.text)]
                        else:
                            value = value_node.text
                    row_values[col] = value
                rows.append(row_values)
            sheets[name] = rows
    return sheets


def period_code(value: str) -> str | None:
    """Recognize source year labels, including explicitly marked estimates."""
    match = re.fullmatch(r"((?:19|20)\d{2}(?:-\d{2})?)(?: \(est\.\))?", value.strip())
    return match.group(1) if match else None


def find_header_row(rows: list[dict]) -> int:
    for idx, row in enumerate(rows):
        if str(row.get("A", "")).strip().lower() in {"fuel type", "industry"}:
            values = [
                str(value).strip()
                for col, value in row.items()
                if col != "A" and str(value).strip()
            ]
            if values and all(value in STATE_CODES or period_code(value) for value in values):
                return idx
    for idx, row in enumerate(rows):
        b_value = str(row.get("B", "")).strip()
        data_cells = [
            str(value).strip()
            for col, value in row.items()
            if column_number(col) >= column_number("C") and str(value).strip()
        ]
        if (
            not b_value
            and len(data_cells) >= 2
            and all(value in STATE_CODES or period_code(value) for value in data_cells)
        ):
            return idx
    raise ValueError("Unable to identify header row")


def detect_column_dimension(header_values: list[str]) -> str:
    cleaned = [value.strip() for value in header_values if value.strip()]
    if cleaned and all(period_code(value) for value in cleaned):
        return "TIME_PERIOD"
    if cleaned and all(value in STATE_CODES for value in cleaned):
        return "REGION"
    raise ValueError(
        "AES sheet mixes or omits time and region headers; unknown layout was not accepted."
    )


def infer_sheet_region(sheet_name: str):
    prefix = str(sheet_name or "").split(" ")[0].strip().upper()
    if prefix in STATE_CODES:
        return "Australia" if prefix == "AUS" else prefix
    return None


def infer_period_basis(sheet_name: str):
    upper = str(sheet_name or "").upper()
    if upper.endswith("FY"):
        return "financial_year"
    if upper.endswith("CY"):
        return "calendar_year"
    return None


def sheet_title(rows: list[dict]) -> str:
    if rows and str(rows[0].get("A", "")).startswith("Table O"):
        return str(rows[0]["A"])
    for index in (1, 0):
        if index < len(rows):
            value = str(rows[index].get("B", "")).strip()
            if value:
                return value
    return ""


def extract_sheet_records(sheet_name: str, rows: list[dict], sheet_group_id: str):
    header_idx = find_header_row(rows)
    header_row = rows[header_idx]
    unit_row = rows[header_idx + 1] if header_idx + 1 < len(rows) else {}
    modern = str(header_row.get("A", "")).strip().lower() in {"fuel type", "industry"}
    label_column = "A" if modern else "B"
    first_column = "B" if modern else "C"
    header_unit = (
        "GWh"
        if modern and any("Gigawatt hours" in str(row.get("A", "")) for row in rows[:header_idx])
        else None
    )
    if modern and header_unit is None:
        raise ValueError(f"Unrecognized units in AES sheet {sheet_name}; refusing to infer units.")
    columns = sorted(
        (
            col
            for col in header_row.keys()
            if column_number(col) >= column_number(first_column) and str(header_row[col]).strip()
        ),
        key=column_number,
    )
    header_values = [str(header_row.get(col, "")).strip() for col in columns]
    column_dimension = detect_column_dimension(header_values)
    if len(set(header_values)) != len(header_values):
        raise ValueError(f"Duplicate observation columns in AES sheet {sheet_name}.")
    title = sheet_title(rows)
    summary_period = None
    if column_dimension == "REGION":
        periods = re.findall(r"(?:19|20)\d{2}(?:-\d{2})?", title)
        if len(periods) != 1:
            raise ValueError(f"AES sheet {sheet_name} does not identify one observation period.")
        summary_period = periods[0]
    section = None
    records = []
    coordinates = set()

    for row in rows[header_idx + (1 if modern else 2) :]:
        label = str(row.get(label_column, "")).strip()
        row_values = [str(row.get(col, "")).strip() for col in columns]

        if label.startswith(("Notes:", "[a]", "[b]", "Source:")):
            break
        if not label and not any(row_values):
            continue
        if label and not any(row_values) and not modern:
            section = label
            continue
        if not label:
            continue
        if parse_float(label) is not None:
            raise ValueError(
                f"Numeric category in AES sheet {sheet_name}; workbook layout may have changed."
            )

        for col, column_value in zip(columns, header_values, strict=True):
            value = parse_float(row.get(col, ""))
            column_code = (
                period_code(column_value) if column_dimension == "TIME_PERIOD" else column_value
            )
            coordinate = (section, normalize_code(label), column_code)
            if coordinate in coordinates:
                raise ValueError(f"Duplicate observation coordinates in AES sheet {sheet_name}.")
            coordinates.add(coordinate)
            record = {
                "sheet_group": sheet_group_id,
                "sheet": sheet_name,
                "sheet_title": title,
                "section": section,
                "category": label,
                "column_dimension": column_dimension,
                "column_value": column_code,
                "column_label": column_value,
                "unit": ("percent" if "per cent" in label.lower() else header_unit)
                if modern
                else (str(unit_row.get(col, "")).strip() or None),
                "value": value,
                "raw_value": str(row.get(col, "")),
            }
            region = infer_sheet_region(sheet_name)
            if region and column_dimension != "REGION":
                record["region"] = region
            period_basis = infer_period_basis(sheet_name)
            if period_basis:
                record["period_basis"] = period_basis
            if summary_period:
                record["time_period"] = summary_period
            records.append(record)
    return records


def build_metadata(flow: dict, curation: dict, workbook_sheets: dict) -> dict:
    sheet_groups = curation.get("sheetGroups") or []
    ignored_sheets = curation.get("ignoredSheets") or []
    group_codes = [
        {
            "id": item["id"],
            "name": item["id"],
            "description": item.get("description", ""),
        }
        for item in sheet_groups
    ]
    sheet_codes = []
    for item in sheet_groups:
        for sheet_name in item.get("sheets", []):
            if sheet_name in workbook_sheets:
                sheet_codes.append(
                    {
                        "id": normalize_code(sheet_name),
                        "name": sheet_name,
                        "description": sheet_title(workbook_sheets[sheet_name]),
                    }
                )
    concepts = [
        {
            "id": "DATA_KEY",
            "name": "Custom retrieval key",
            "description": "Use dataKey equal to one of the discovered SHEET_GROUP ids to retrieve a grouped workbook slice.",
        },
        {
            "id": "SOURCE_URL",
            "name": "Source workbook URL",
            "description": flow["description"],
        },
    ]
    if ignored_sheets:
        concepts.append(
            {
                "id": "IGNORED_SHEETS",
                "name": "Ignored sheets",
                "description": ", ".join(ignored_sheets),
            }
        )

    return {
        "dataStructure": {
            "id": flow["id"],
            "agencyID": flow["agencyID"],
            "version": flow["version"],
            "name": flow["name"],
            "description": (
                f"{flow['description']} Retrieve using dataKey equal to one of the discovered "
                "sheet group ids such as national_financial_year, state_financial_year, "
                "bioenergy, or national_calendar_year. Use the returned codelists for valid groups."
            ),
        },
        "dimensions": [
            {
                "id": "SHEET_GROUP",
                "position": 1,
                "conceptId": "SHEET_GROUP",
                "codelist": {"id": "SHEET_GROUPS"},
            },
            {
                "id": "SHEET",
                "position": 2,
                "conceptId": "SHEET",
                "codelist": {"id": "SHEETS"},
            },
        ],
        "attributes": [
            {
                "id": "UNIT",
                "attachmentLevel": "Observation",
                "conceptId": "UNIT",
            }
        ],
        "codelists": [
            {
                "id": "SHEET_GROUPS",
                "name": "Discovered sheet groups",
                "codes": group_codes,
            },
            {
                "id": "SHEETS",
                "name": "Workbook sheets",
                "codes": sheet_codes,
            },
        ],
        "concepts": concepts,
    }


def select_group(data_key: str, curation: dict) -> tuple[str, list[str]]:
    sheet_groups = curation.get("sheetGroups") or []
    if not data_key or data_key == "all":
        raise ValueError(
            "DCCEEW requires an explicit sheet group or sheet dataKey from get_metadata."
        )
    for item in sheet_groups:
        if item.get("id") == data_key:
            return item["id"], item.get("sheets", [])
        for name in item.get("sheets", []):
            if data_key in (name, normalize_code(name)):
                return item["id"], [name]
    raise ValueError(f"Unknown custom dataKey '{data_key}'")


def build_resolved_dataset(
    flow: dict, curation: dict, workbook_sheets: dict, data_key: str = "all"
) -> dict:
    group_id, target_sheets = select_group(data_key or "all", curation)
    records = []
    for sheet_name in target_sheets:
        rows = workbook_sheets.get(sheet_name)
        if not rows:
            raise ValueError(f"Requested AES sheet {sheet_name!r} is absent or empty.")
        sheet_records = extract_sheet_records(sheet_name, rows, group_id)
        if not sheet_records:
            raise ValueError(f"Requested AES sheet {sheet_name!r} contains no observations.")
        records.extend(sheet_records)
    if not records:
        raise ValueError(f"No records extracted for dataKey '{data_key or 'all'}'")

    dimensions_lookup = defaultdict(dict)
    series_map = {}

    for record in records:
        dimensions_lookup["SHEET_GROUP"][record["sheet_group"]] = record["sheet_group"]
        dimensions_lookup["SHEET"][record["sheet"]] = record["sheet"]
        if record.get("section"):
            dimensions_lookup["SECTION"][normalize_code(record["section"])] = record["section"]
        dimensions_lookup["CATEGORY"][normalize_code(record["category"])] = record["category"]
        if record.get("region"):
            dimensions_lookup["REGION"][normalize_code(record["region"])] = record["region"]
        if record.get("period_basis"):
            dimensions_lookup["PERIOD_BASIS"][normalize_code(record["period_basis"])] = record[
                "period_basis"
            ]

        series_dims = {
            "SHEET_GROUP": {"code": record["sheet_group"], "label": record["sheet_group"]},
            "SHEET": {"code": record["sheet"], "label": record["sheet"]},
            "CATEGORY": {"code": normalize_code(record["category"]), "label": record["category"]},
        }
        if record.get("section"):
            series_dims["SECTION"] = {
                "code": normalize_code(record["section"]),
                "label": record["section"],
            }
        if record.get("region"):
            series_dims["REGION"] = {
                "code": normalize_code(record["region"]),
                "label": record["region"],
            }
        if record.get("period_basis"):
            series_dims["PERIOD_BASIS"] = {
                "code": normalize_code(record["period_basis"]),
                "label": record["period_basis"],
            }

        series_key_parts = [
            record["sheet_group"],
            record["sheet"],
            record.get("region") or "",
            record.get("section") or "",
            record["category"],
        ]
        series_key = "|".join(series_key_parts)
        series = series_map.setdefault(
            series_key,
            {
                "seriesKey": series_key,
                "dimensions": series_dims,
                "observations": [],
            },
        )

        obs_dims = {
            record["column_dimension"]: {
                "code": record["column_value"]
                if record["column_dimension"] == "TIME_PERIOD"
                else normalize_code(record["column_value"]),
                "label": record["column_label"],
            }
        }
        dimensions_lookup[record["column_dimension"]][record["column_value"]] = record[
            "column_label"
        ]
        if record.get("time_period"):
            obs_dims["TIME_PERIOD"] = {
                "code": record["time_period"],
                "label": record["time_period"],
            }
            dimensions_lookup["TIME_PERIOD"][record["time_period"]] = record["time_period"]

        observation = {
            "observationKey": record["column_value"],
            "value": record["value"],
            "dimensions": obs_dims,
            "attributes": {"OBS_VALUE_RAW": record["raw_value"]},
        }
        if record.get("unit"):
            observation["attributes"]["UNIT"] = record["unit"]
            dimensions_lookup["UNIT"][record["unit"]] = record["unit"]
        series["observations"].append(observation)

    return {
        "dataset": {
            "id": flow["id"],
            "agencyID": flow["agencyID"],
            "version": flow["version"],
            "name": flow["name"],
            "description": flow["description"],
        },
        "query": {
            "dataKey": data_key or "all",
            "detail": "full",
        },
        "source_annotations": {
            "workbook_notes": list(
                dict.fromkeys(
                    str(row.get("A") or row.get("B") or "").strip()
                    for name in target_sheets
                    for row in workbook_sheets.get(name, [])
                    if str(row.get("A") or row.get("B") or "")
                    .strip()
                    .startswith(("Blank cells", "[", "Notes:", "Source:"))
                )
            )
        },
        "dimensions": dict(dimensions_lookup),
        "observationCount": sum(len(item["observations"]) for item in series_map.values()),
        "series": list(series_map.values()),
    }


def discover_curation(workbook_sheets: dict) -> dict:
    """Resolve supported Table O sheet groups from the actual workbook, not its year."""
    groups = {
        "national_financial_year": [],
        "state_financial_year": [],
        "national_calendar_year": [],
        "state_calendar_year": [],
        "state_summary": [],
        "bioenergy": [],
        "industry": [],
    }
    ignored = []
    for name in workbook_sheets:
        compact = re.sub(r"\s+", "", name).upper()
        if compact == "AUSFY":
            group = "national_financial_year"
        elif compact == "AUSCY":
            group = "national_calendar_year"
        elif compact.endswith("FY") and compact[:-2] in STATE_CODES:
            group = "state_financial_year"
        elif compact.endswith("CY") and compact[:-2] in STATE_CODES:
            group = "state_calendar_year"
        elif compact.startswith("STATESUMMARY"):
            group = "state_summary"
        elif "BIOENERGY" in compact:
            group = "bioenergy"
        elif "INDUSTRY" in compact:
            group = "industry"
        else:
            ignored.append(name)
            continue
        groups[group].append(name)
    if not groups["national_financial_year"]:
        raise ValueError("AES Table O workbook schema changed: missing AUS FY sheet.")
    return {
        "sheetGroups": [
            {"id": key, "description": key.replace("_", " "), "sheets": names}
            for key, names in groups.items()
            if names
        ],
        "ignoredSheets": ignored,
    }
