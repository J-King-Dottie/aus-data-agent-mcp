from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from typing import Any, Dict, List, Optional

from .model_builder import fetch_model_builder_state, save_node_data_state


SAVED_SERIES_COLOR = "#234233"
CALCULATED_SERIES_COLOR = "#b45f3a"


def _as_text(value: Any) -> str:
    return str(value or "").strip()


def _as_number(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except Exception:
        return None
    if number != number:
        return None
    return number


def _record(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _series_label_from_key(key: str) -> str:
    words = [
        word
        for word in _as_text(key).replace("-", "_").split("_")
        if word and word.lower() not in {"value", "values", "usd", "aud", "billion", "million", "number", "count"}
    ]
    return " ".join(words).capitalize() if words else _as_text(key)


def _fingerprint(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _records_from_contents(contents: Dict[str, Any]) -> List[Dict[str, Any]]:
    rows = contents.get("rows")
    if isinstance(rows, list):
        return [row for row in rows if isinstance(row, dict)]

    columns = [_as_text(item) for item in contents.get("columns", []) if _as_text(item)] if isinstance(contents.get("columns"), list) else []
    records = contents.get("records") if isinstance(contents.get("records"), list) else []
    dimensions = contents.get("dimensions") if isinstance(contents.get("dimensions"), dict) else {}
    if not columns or not records:
        return []

    parsed: List[Dict[str, Any]] = []
    for record in records:
        if not isinstance(record, list):
            continue
        row = dict(dimensions)
        for index, column in enumerate(columns):
            row[column] = record[index] if index < len(record) else None
        parsed.append(row)
    return parsed


def _points_from_variable(variable: Dict[str, Any]) -> List[Dict[str, Any]]:
    contents = variable.get("contents") if isinstance(variable.get("contents"), dict) else {}
    rows = _records_from_contents(contents)
    if not rows:
        return []

    columns = [_as_text(item) for item in contents.get("columns", []) if _as_text(item)] if isinstance(contents.get("columns"), list) else list(rows[0].keys())
    period_key = _as_text(contents.get("period_key")) or next((column for column in columns if any(token in column.lower() for token in ("time", "period", "date", "quarter", "year"))), "")
    value_key = _as_text(contents.get("value_key")) or next((column for column in columns if column.lower() in {"value", "y", "obs_value"}), "")
    if not value_key:
        value_key = next((column for column in columns if any(_as_number(row.get(column)) is not None for row in rows)), "")
    x_key = period_key or next((column for column in columns if column != value_key), "")
    if not x_key or not value_key:
        return []

    points: List[Dict[str, Any]] = []
    for row in rows:
        x_value = _as_text(row.get(x_key))
        y_value = _as_number(row.get(value_key))
        if x_value and y_value is not None:
            points.append({"x": x_value, "y": y_value})
    return points


def _series_from_variable(variable: Dict[str, Any]) -> List[Dict[str, Any]]:
    contents = variable.get("contents") if isinstance(variable.get("contents"), dict) else {}
    rows = _records_from_contents(contents)
    if not rows:
        return []
    columns = [_as_text(item) for item in contents.get("columns", []) if _as_text(item)] if isinstance(contents.get("columns"), list) else list(rows[0].keys())
    period_key = _as_text(contents.get("period_key")) or next((column for column in columns if any(token in column.lower() for token in ("time", "period", "date", "quarter", "year"))), "")
    value_key = _as_text(contents.get("value_key")) or next((column for column in columns if column.lower() in {"value", "y", "obs_value"}), "")
    numeric_keys = [
        column
        for column in columns
        if column != period_key and any(_as_number(row.get(column)) is not None for row in rows)
    ]
    category_keys = [
        column
        for column in columns
        if column not in {period_key, value_key}
        and not any(_as_number(row.get(column)) is not None for row in rows)
        and len({_as_text(row.get(column)) for row in rows if _as_text(row.get(column))}) > 1
    ]
    series: List[Dict[str, Any]] = []
    if period_key and value_key and category_keys:
        grouped: Dict[str, List[Dict[str, Any]]] = {}
        for row in rows:
            label = " / ".join(_as_text(row.get(key)) for key in category_keys if _as_text(row.get(key)))
            x_value = _as_text(row.get(period_key))
            y_value = _as_number(row.get(value_key))
            if label and x_value and y_value is not None:
                grouped.setdefault(label, []).append({"x": x_value, "y": y_value})
        series = [{"name": label, "points": points} for label, points in grouped.items() if points]
    elif period_key and len(numeric_keys) > 1 and _as_text(contents.get("value_key")).lower() not in {"value", "y", "obs_value"}:
        for key in numeric_keys:
            points = []
            for row in rows:
                x_value = _as_text(row.get(period_key))
                y_value = _as_number(row.get(key))
                if x_value and y_value is not None:
                    points.append({"x": x_value, "y": y_value})
            if points:
                series.append({"name": _series_label_from_key(key), "points": points})
    return series


def _searchable_variable_text(variable: Dict[str, Any]) -> str:
    text = " ".join(
        _as_text(variable.get(key))
        for key in ("id", "name", "label", "metric", "sourceName", "geography", "frequency", "seasonalTreatment")
    )
    return "".join(ch.lower() if ch.isalnum() else " " for ch in text)


def _meaningful_tokens(value: str) -> List[str]:
    stopwords = {
        "abs",
        "australia",
        "australian",
        "quarterly",
        "annual",
        "history",
        "path",
        "project",
        "projection",
        "the",
        "and",
        "total",
        "from",
    }
    clean = value.lower().replace("other-res", "other residential")
    clean = "".join(ch if ch.isalnum() else " " for ch in clean)
    return [token for token in clean.split() if len(token) > 2 and token not in stopwords]


def _variable_for_node(node: Dict[str, Any], variables: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    node_variable_id = _as_text(node.get("variableId"))
    node_id = _as_text(node.get("id"))
    for variable in variables:
        if _as_text(variable.get("id")) in {node_variable_id, node_id}:
            return variable

    tokens = _meaningful_tokens(" ".join(_as_text(node.get(key)) for key in ("node_title", "node_description", "tooltip")))
    if not tokens:
        return None
    scored = []
    for variable in variables:
        text = _searchable_variable_text(variable)
        score = sum(1 for token in tokens if token in text)
        if score:
            scored.append((score, variable))
    scored.sort(key=lambda item: item[0], reverse=True)
    if not scored or (len(scored) > 1 and scored[0][0] == scored[1][0]):
        return None
    return scored[0][1]


def _input_node_ids(node: Dict[str, Any], edges: List[Dict[str, Any]]) -> List[str]:
    direct = [_as_text(item) for item in node.get("inputs", []) if _as_text(item)] if isinstance(node.get("inputs"), list) else []
    if direct:
        return direct
    target_id = _as_text(node.get("id"))
    return [
        _as_text(edge.get("sourceNodeId"))
        for edge in edges
        if _as_text(edge.get("targetNodeId")) == target_id and _as_text(edge.get("sourceNodeId"))
    ]


def _operation_symbol(node: Dict[str, Any]) -> str:
    value = f"{_as_text(node.get('expression'))} {_as_text(node.get('node_title'))}".lower()
    if "+" in value or " add" in value or "sum" in value:
        return "+"
    if "÷" in value or "/" in value or "divide" in value or "ratio" in value:
        return "/"
    if "×" in value or "*" in value or "multiply" in value or "product" in value:
        return "*"
    if "−" in value or "-" in value or "subtract" in value or "minus" in value:
        return "-"
    return ""


def _is_comparison_node(node: Dict[str, Any]) -> bool:
    value = " ".join(
        _as_text(node.get(key)).lower()
        for key in ("expression", "method", "node_title", "output")
    )
    return any(token in value for token in ("comparison", "compare", "multi series", "multi-series", "bundle"))


def _is_plain_result_alias(node: Dict[str, Any], edges: List[Dict[str, Any]]) -> bool:
    if _as_text(node.get("nodeType")) != "result":
        return False
    if _as_text(node.get("expression")) or _as_text(node.get("method")):
        return False
    if isinstance(node.get("calculationLogic"), dict) and node.get("calculationLogic"):
        return False
    if isinstance(node.get("calculationSpec"), dict) and node.get("calculationSpec"):
        return False
    return len(_input_node_ids(node, edges)) == 1


def _align_and_calculate(inputs: List[Dict[str, Any]], operation: str) -> List[Dict[str, Any]]:
    if not inputs:
        return []
    input_maps = [{_as_text(point.get("x")): _as_number(point.get("y")) for point in item.get("points", [])} for item in inputs]
    common_x = sorted(set(input_maps[0]).intersection(*(set(item) for item in input_maps[1:]))) if len(input_maps) > 1 else sorted(set(input_maps[0]))
    points: List[Dict[str, Any]] = []
    for x_value in common_x:
        values = [item.get(x_value) for item in input_maps]
        if any(value is None for value in values):
            continue
        result = float(values[0])
        for value in values[1:]:
            number = float(value)
            if operation == "+":
                result += number
            elif operation == "-":
                result -= number
            elif operation == "*":
                result *= number
            elif operation == "/":
                if number == 0:
                    result = float("nan")
                    break
                result /= number
        if result == result:
            points.append({"x": x_value, "y": result})
    return points


def _safe_exec_calculation(code: str, inputs: Dict[str, Dict[str, Any]], parameters: Dict[str, Any]) -> Dict[str, Any]:
    builtins = {
        "abs": abs,
        "bool": bool,
        "dict": dict,
        "enumerate": enumerate,
        "Exception": Exception,
        "float": float,
        "int": int,
        "iter": iter,
        "len": len,
        "list": list,
        "max": max,
        "min": min,
        "next": next,
        "range": range,
        "round": round,
        "set": set,
        "sorted": sorted,
        "str": str,
        "sum": sum,
        "zip": zip,
    }
    namespace: Dict[str, Any] = {}
    exec(code, {"__builtins__": builtins}, namespace)
    calculate = namespace.get("calculate") or namespace.get("run")
    if not callable(calculate):
        raise ValueError("Calculation code must define calculate(inputs, parameters).")
    result = calculate(inputs, parameters)
    if isinstance(result, list):
        return {"points": result}
    if isinstance(result, dict):
        return result
    raise ValueError("Calculation code must return a dict or list of points.")


def _points_from_result(value: Dict[str, Any]) -> List[Dict[str, Any]]:
    points = value.get("points")
    if isinstance(points, list):
        parsed = []
        for point in points:
            if not isinstance(point, dict):
                continue
            x_value = _as_text(point.get("x"))
            y_value = _as_number(point.get("y"))
            if x_value and y_value is not None:
                parsed.append({"x": x_value, "y": y_value})
        return parsed
    rows = _records_from_contents(value)
    if rows:
        fake_variable = {"contents": value}
        return _points_from_variable(fake_variable)
    return []


def _series_from_result(value: Dict[str, Any]) -> List[Dict[str, Any]]:
    series = value.get("series")
    if not isinstance(series, list):
        return []
    parsed: List[Dict[str, Any]] = []
    for index, item in enumerate(series):
        if not isinstance(item, dict):
            continue
        points = _points_from_result({"points": item.get("points")})
        if points:
            parsed.append(
                {
                    "name": _as_text(item.get("name") or item.get("label")) or f"Series {index + 1}",
                    "points": points,
                }
            )
    return parsed


def _points_from_calculated_cache(entry: Dict[str, Any]) -> List[Dict[str, Any]]:
    columns = [_as_text(item) for item in entry.get("columns", []) if _as_text(item)] if isinstance(entry.get("columns"), list) else []
    records = entry.get("records") if isinstance(entry.get("records"), list) else []
    if not columns or not records:
        return []
    try:
        x_index = columns.index("period")
    except ValueError:
        x_index = 0
    try:
        y_index = columns.index("value")
    except ValueError:
        y_index = 1 if len(columns) > 1 else 0
    points: List[Dict[str, Any]] = []
    for record in records:
        if not isinstance(record, list):
            continue
        x_value = _as_text(record[x_index] if x_index < len(record) else "")
        y_value = _as_number(record[y_index] if y_index < len(record) else None)
        if x_value and y_value is not None:
            points.append({"x": x_value, "y": y_value})
    return points


def _series_from_calculated_cache(entry: Dict[str, Any]) -> List[Dict[str, Any]]:
    series = entry.get("series")
    if not isinstance(series, list):
        return []
    parsed_series: List[Dict[str, Any]] = []
    for item in series:
        if not isinstance(item, dict):
            continue
        raw_points = item.get("points")
        points: List[Dict[str, Any]] = []
        if isinstance(raw_points, list):
            for point in raw_points:
                if isinstance(point, dict):
                    x_value = _as_text(point.get("x"))
                    y_value = _as_number(point.get("y"))
                elif isinstance(point, list):
                    x_value = _as_text(point[0] if len(point) > 0 else "")
                    y_value = _as_number(point[1] if len(point) > 1 else None)
                else:
                    continue
                if x_value and y_value is not None:
                    points.append({"x": x_value, "y": y_value})
        if points:
            parsed_series.append(
                {
                    "name": _as_text(item.get("name") or item.get("label")) or "Series",
                    "points": points,
                }
            )
    return parsed_series


def _result_from_calculated_cache(entry: Dict[str, Any], fallback_label: str, fingerprint: str) -> Optional[Dict[str, Any]]:
    if _as_text(entry.get("input_fingerprint")) != fingerprint:
        return None
    series = _series_from_calculated_cache(entry)
    if series:
        return {
            "label": _as_text(entry.get("node_title")) or fallback_label,
            "unit": _as_text(entry.get("unit")),
            "series": series,
            "points": series[0]["points"],
            "dataKind": "calculated",
            "fingerprint": fingerprint,
            "cacheStatus": "hit",
        }
    points = _points_from_calculated_cache(entry)
    if not points:
        return None
    return {
        "label": _as_text(entry.get("node_title")) or fallback_label,
        "unit": _as_text(entry.get("unit")),
        "points": points,
        "dataKind": "calculated",
        "fingerprint": fingerprint,
        "cacheStatus": "hit",
    }


def _node_chart_data_entry(node_id: str, result: Dict[str, Any], fingerprint: str) -> Dict[str, Any]:
    entry = {
        "kind": "node_chart_series",
        "node_id": node_id,
        "node_title": _as_text(result.get("label")) or node_id,
        "unit": _as_text(result.get("unit")),
        "data_kind": _as_text(result.get("dataKind")) or "calculated",
        "computed_at": datetime.now(timezone.utc).isoformat(),
        "input_fingerprint": fingerprint,
        "columns": ["period", "value"],
        "records": [[_as_text(point.get("x")), _as_number(point.get("y"))] for point in result.get("points", [])],
    }
    series = result.get("series")
    if isinstance(series, list) and series:
        entry["kind"] = "node_chart_multi_series"
        entry["series"] = [
            {
                "name": _as_text(item.get("name") or item.get("label")) or f"Series {index + 1}",
                "points": [[_as_text(point.get("x")), _as_number(point.get("y"))] for point in item.get("points", []) if isinstance(point, dict)],
            }
            for index, item in enumerate(series)
            if isinstance(item, dict)
        ]
    return entry


def _chart_from_saved_node_data(node_id: str, entry: Dict[str, Any]) -> Dict[str, Any]:
    if not entry:
        raise ValueError("No saved node_data exists for this node.")
    title = _as_text(entry.get("node_title")) or node_id
    data_kind = _as_text(entry.get("data_kind")) or "calculated"
    series = _series_from_calculated_cache(entry)
    if series:
        chart_series = [
            {
                "name": _as_text(item.get("name")) or f"Series {index + 1}",
                "color": CALCULATED_SERIES_COLOR if data_kind != "saved" else SAVED_SERIES_COLOR,
                "points": item.get("points", []),
            }
            for index, item in enumerate(series)
            if isinstance(item, dict) and isinstance(item.get("points"), list)
        ]
    else:
        points = _points_from_calculated_cache(entry)
        if not points:
            raise ValueError("Saved node_data has no chartable points.")
        chart_series = [
            {
                "name": title,
                "color": SAVED_SERIES_COLOR if data_kind == "saved" else CALCULATED_SERIES_COLOR,
                "points": points,
            }
        ]
    unit = _as_text(entry.get("unit")) or "Value"
    return {
        "node_id": node_id,
        "node_title": title,
        "data_kind": data_kind,
        "cache_status": "saved",
        "refreshed": False,
        "chartSpec": {
            "type": "line",
            "title": title,
            "xLabel": "Period",
            "yLabel": unit,
            "series": chart_series,
        },
    }


def _evaluate_node(
    node_id: str,
    *,
    nodes: Dict[str, Dict[str, Any]],
    edges: List[Dict[str, Any]],
    variables: List[Dict[str, Any]],
    calculated_data: Dict[str, Any],
    cache: Dict[str, Dict[str, Any]],
    pending_calculated_data: Dict[str, Dict[str, Any]],
    force_refresh: bool = False,
    stack: Optional[set[str]] = None,
) -> Dict[str, Any]:
    if node_id in cache:
        return cache[node_id]
    stack = stack or set()
    if node_id in stack:
        raise ValueError("Calculation graph contains a cycle.")
    stack.add(node_id)

    node = nodes.get(node_id)
    if not node:
        raise ValueError("Node not found.")
    node_type = _as_text(node.get("nodeType"))
    label = _as_text(node.get("node_title")) or node_id

    if node_type == "variable":
        variable = _variable_for_node(node, variables)
        if not variable:
            raise ValueError("No saved data is linked to this variable node.")
        series = _series_from_variable(variable)
        result = {
            "label": _as_text(variable.get("label")) or label,
            "unit": _as_text(variable.get("unit")),
            "points": series[0]["points"] if series else _points_from_variable(variable),
            "dataKind": "saved",
            "fingerprint": _fingerprint(
                {
                    "kind": "validated_variable",
                    "id": _as_text(variable.get("id")),
                    "updatedAt": _as_text(variable.get("updatedAt") or variable.get("updated_at")),
                    "contents": variable.get("contents"),
                }
            ),
        }
        if series:
            result["series"] = series
    elif _is_plain_result_alias(node, edges):
        input_ids = _input_node_ids(node, edges)
        if not input_ids:
            raise ValueError("Result node has no upstream calculation.")
        upstream = _evaluate_node(
            input_ids[0],
            nodes=nodes,
            edges=edges,
            variables=variables,
            calculated_data=calculated_data,
            cache=cache,
            pending_calculated_data=pending_calculated_data,
            force_refresh=force_refresh,
            stack=stack,
        )
        result = {
            **upstream,
            "label": label,
            "dataKind": "calculated",
            "cacheStatus": upstream.get("cacheStatus") or "not_applicable",
            "fingerprint": _fingerprint(
                {
                    "kind": "result_alias",
                    "nodeId": node_id,
                    "label": label,
                    "input": upstream.get("fingerprint"),
                }
            ),
        }
    else:
        input_ids = _input_node_ids(node, edges)
        input_results = [
            _evaluate_node(
                input_id,
                nodes=nodes,
                edges=edges,
                variables=variables,
                calculated_data=calculated_data,
                cache=cache,
                pending_calculated_data=pending_calculated_data,
                force_refresh=force_refresh,
                stack=stack,
            )
            for input_id in input_ids
        ]
        logic = node.get("calculationLogic") if isinstance(node.get("calculationLogic"), dict) else {}
        spec = node.get("calculationSpec") if isinstance(node.get("calculationSpec"), dict) else {}
        replay = spec.get("replay") if isinstance(spec.get("replay"), dict) else {}
        code = _as_text(logic.get("code") or logic.get("calculation_code") or replay.get("code"))
        operation = _operation_symbol(node)
        is_comparison = _is_comparison_node(node)
        result_fingerprint = _fingerprint(
            {
                "kind": "calculation_node",
                "nodeId": node_id,
                "label": label,
                "expression": _as_text(node.get("expression")),
                "operation": operation,
                "is_comparison": is_comparison,
                "parameters": node.get("parameters") if isinstance(node.get("parameters"), dict) else {},
                "code": code,
                "inputs": [item.get("fingerprint") for item in input_results],
            }
        )
        cached = None if force_refresh else _result_from_calculated_cache(_record(calculated_data.get(node_id)), label, result_fingerprint)
        if cached:
            result = cached
        elif is_comparison:
            series = [
                {
                    "name": _as_text(item.get("label")) or input_id,
                    "points": item.get("points", []),
                }
                for input_id, item in zip(input_ids, input_results)
                if item.get("points")
            ]
            result = {
                "label": _as_text(node.get("output")) or label,
                "unit": input_results[0].get("unit") if input_results else "",
                "points": series[0]["points"] if series else [],
                "series": series,
                "dataKind": "calculated",
                "fingerprint": result_fingerprint,
                "cacheStatus": "miss",
            }
        elif code:
            input_payload = {
                input_id: {
                    "label": item.get("label"),
                    "unit": item.get("unit"),
                    "points": item.get("points", []),
                }
                for input_id, item in zip(input_ids, input_results)
            }
            calculated = _safe_exec_calculation(code, input_payload, node.get("parameters") if isinstance(node.get("parameters"), dict) else {})
            series = _series_from_result(calculated)
            points = series[0]["points"] if series else _points_from_result(calculated)
            result = {
                "label": _as_text(calculated.get("label")) or _as_text(node.get("output")) or label,
                "unit": _as_text(calculated.get("unit")) or (input_results[0].get("unit") if input_results else ""),
                "points": points,
                "dataKind": "calculated",
                "fingerprint": result_fingerprint,
                "cacheStatus": "miss",
            }
            if series:
                result["series"] = series
        else:
            if not operation:
                raise ValueError("This calculation has no executable calculation code.")
            result = {
                "label": _as_text(node.get("output")) or label,
                "unit": input_results[0].get("unit") if input_results else "",
                "points": _align_and_calculate(input_results, operation),
                "dataKind": "calculated",
                "fingerprint": result_fingerprint,
                "cacheStatus": "miss",
            }

    stack.remove(node_id)
    if not result.get("points"):
        raise ValueError("No chartable data was produced for this node.")
    cache[node_id] = result
    pending_calculated_data[node_id] = _node_chart_data_entry(
        node_id,
        result,
        _as_text(result.get("fingerprint")) or _fingerprint({"nodeId": node_id, "result": result}),
    )
    return result


def build_model_node_chart(*, user_id: str, project_id: str, node_id: str, refresh: bool = False) -> Dict[str, Any]:
    state = fetch_model_builder_state(user_id=user_id, project_id=project_id)
    nodes = {
        _as_text(node.get("id")): node
        for node in state.get("nodes", [])
        if isinstance(node, dict) and _as_text(node.get("id"))
    }
    edges = [edge for edge in state.get("edges", []) if isinstance(edge, dict)]
    variables = [variable for variable in state.get("variables", []) if isinstance(variable, dict)]
    node_data = state.get("node_data") if isinstance(state.get("node_data"), dict) else {}
    if not refresh:
        return _chart_from_saved_node_data(node_id, _record(node_data.get(node_id)))
    pending_calculated_data: Dict[str, Dict[str, Any]] = {}
    result = _evaluate_node(
        node_id,
        nodes=nodes,
        edges=edges,
        variables=variables,
        calculated_data=node_data,
        cache={},
        pending_calculated_data=pending_calculated_data,
        force_refresh=refresh,
    )
    if pending_calculated_data:
        for pending_node_id, pending_result in pending_calculated_data.items():
            save_node_data_state(
                user_id=user_id,
                project_id=project_id,
                node_id=pending_node_id,
                result=pending_result,
            )
    data_kind = _as_text(result.get("dataKind")) or "calculated"
    title = _as_text(result.get("label")) or node_id
    result_series = result.get("series") if isinstance(result.get("series"), list) else []
    chart_series = (
        [
            {
                "name": _as_text(item.get("name")) or f"Series {index + 1}",
                "color": CALCULATED_SERIES_COLOR,
                "points": item.get("points", []),
            }
            for index, item in enumerate(result_series)
            if isinstance(item, dict) and isinstance(item.get("points"), list)
        ]
        if result_series
        else [
            {
                "name": title,
                "color": SAVED_SERIES_COLOR if data_kind == "saved" else CALCULATED_SERIES_COLOR,
                "points": result["points"],
            }
        ]
    )
    return {
        "node_id": node_id,
        "node_title": title,
        "data_kind": data_kind,
        "cache_status": _as_text(result.get("cacheStatus")) or ("not_applicable" if data_kind == "saved" else "miss"),
        "refreshed": bool(refresh and data_kind == "calculated"),
        "chartSpec": {
            "type": "line",
            "title": title,
            "xLabel": "Period",
            "yLabel": _as_text(result.get("unit")) or "Value",
            "series": chart_series,
        },
    }
