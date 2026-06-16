from __future__ import annotations

from typing import Any, Dict, List, Optional

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Json

from .config import get_settings


def _as_text(value: Any) -> str:
    return str(value or "").strip()


def _connect():
    database_url = _as_text(get_settings().database_url)
    if not database_url:
        raise RuntimeError("DATABASE_URL is not configured.")
    return psycopg.connect(database_url, connect_timeout=20, row_factory=dict_row)


def _text_list(value: Any) -> List[str]:
    if not isinstance(value, list):
        return []
    return [_as_text(item) for item in value if _as_text(item)]


def _unique_text_list(value: Any) -> List[str]:
    seen: set[str] = set()
    items: List[str] = []
    for item in _text_list(value):
        if item in seen:
            continue
        seen.add(item)
        items.append(item)
    return items


def _clean_id(value: Any, fallback: str) -> str:
    cleaned = "".join(ch.lower() if ch.isalnum() else "_" for ch in _as_text(value)).strip("_")
    while "__" in cleaned:
        cleaned = cleaned.replace("__", "_")
    return cleaned[:80] or fallback


def _jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _normalize_variable(value: Any, index: int) -> Dict[str, Any]:
    raw = value if isinstance(value, dict) else {}
    variable_id = _as_text(raw.get("id")) or _as_text(raw.get("name")) or f"variable-{index + 1}"
    status = _as_text(raw.get("validationStatus") or raw.get("validation_status"))
    normalized = {
        "id": variable_id,
        "name": _as_text(raw.get("name")) or variable_id,
        "label": _as_text(raw.get("label")) or _as_text(raw.get("name")) or f"Variable {index + 1}",
        "sourceName": _as_text(raw.get("sourceName") or raw.get("source_name")),
        "metric": _as_text(raw.get("metric")),
        "unit": _as_text(raw.get("unit")),
        "geography": _as_text(raw.get("geography")),
        "frequency": _as_text(raw.get("frequency")),
        "seasonalTreatment": _as_text(raw.get("seasonalTreatment") or raw.get("seasonal_treatment")),
        "transformSummary": _as_text(raw.get("transformSummary") or raw.get("transform_summary")),
        "validationStatus": status if status in {"candidate", "rejected"} else "validated",
    }
    node_description = _as_text(raw.get("nodeDescription") or raw.get("node_description"))
    if node_description:
        normalized["nodeDescription"] = node_description
    contents_summary = _as_text(raw.get("contentsSummary") or raw.get("contents_summary"))
    if contents_summary:
        normalized["contentsSummary"] = contents_summary
    contents = raw.get("contents")
    if isinstance(contents, dict):
        normalized["contents"] = contents
    return normalized


def _normalize_node(value: Any, index: int) -> Dict[str, Any]:
    raw = value if isinstance(value, dict) else {}
    node_id = _as_text(raw.get("id")) or f"node-{index + 1}"
    node_type = _as_text(raw.get("nodeType") or raw.get("node_type"))
    if node_type not in {"variable", "calculation", "result"}:
        node_type = "variable"
    node: Dict[str, Any] = {
        "id": node_id,
        "node_title": _as_text(raw.get("node_title")) or node_id,
        "node_description": _as_text(raw.get("node_description")),
        "nodeType": node_type,
    }
    for source_key, target_key in (
        ("variableId", "variableId"),
        ("variable_id", "variableId"),
        ("expression", "expression"),
        ("method", "method"),
        ("logicSummary", "logicSummary"),
        ("logic_summary", "logicSummary"),
        ("output", "output"),
        ("sourceCalculationId", "sourceCalculationId"),
        ("source_calculation_id", "sourceCalculationId"),
        ("tooltip", "tooltip"),
    ):
        text = _as_text(raw.get(source_key))
        if text:
            node[target_key] = text
    for source_key, target_key in (
        ("inputs", "inputs"),
        ("parameters", "parameters"),
        ("calculationLogic", "calculationLogic"),
        ("calculation_logic", "calculationLogic"),
        ("calculationSpec", "calculationSpec"),
        ("calculation_spec", "calculationSpec"),
    ):
        item = raw.get(source_key)
        if isinstance(item, (dict, list)):
            node[target_key] = item
    for source_key, target_key in (("positionX", "positionX"), ("positionY", "positionY")):
        try:
            number = float(raw.get(source_key))
        except Exception:
            continue
        if number == number:
            node[target_key] = number
    return node


def _normalize_edge(value: Any, index: int) -> Dict[str, Any] | None:
    raw = value if isinstance(value, dict) else {}
    source = _as_text(raw.get("sourceNodeId") or raw.get("source_node_id") or raw.get("from"))
    target = _as_text(raw.get("targetNodeId") or raw.get("target_node_id") or raw.get("to"))
    if not source or not target:
        return None
    return {
        "id": _as_text(raw.get("id")) or f"edge-{index + 1}",
        "sourceNodeId": source,
        "targetNodeId": target,
    }


def _looks_like_generated_node_id(node_id: str) -> bool:
    text = _as_text(node_id)
    if not text.startswith("node-"):
        return False
    suffix = text.removeprefix("node-")
    return suffix.isdigit()


def _validate_graph_integrity(
    *,
    nodes: List[Dict[str, Any]],
    edges: List[Dict[str, Any]],
    node_data: Dict[str, Any],
    allow_missing_node_data_ids: Optional[set[str]] = None,
) -> None:
    allow_missing_node_data_ids = allow_missing_node_data_ids or set()
    node_ids = [_as_text(node.get("id")) for node in nodes if isinstance(node, dict)]
    if len(node_ids) != len(set(node_ids)):
        raise RuntimeError("Model graph update rejected: node ids must be unique.")
    if any(not node_id for node_id in node_ids):
        raise RuntimeError("Model graph update rejected: every visible node must include a stable node id.")
    generated_ids = [node_id for node_id in node_ids if _looks_like_generated_node_id(node_id)]
    if generated_ids:
        raise RuntimeError(
            "Model graph update rejected: generated node ids are not allowed. Use stable semantic node ids."
        )

    node_id_set = set(node_ids)
    for node in nodes:
        node_id = _as_text(node.get("id"))
        if _is_symbol_only_calculation_node(node):
            raise RuntimeError(
                f"Model graph update rejected: node {node_id} is a standalone math-symbol node. "
                "Put the expression on the named output node."
            )
        if not _as_text(node.get("node_title")):
            raise RuntimeError(f"Model graph update rejected: node {node_id} is missing node_title.")
        if not _as_text(node.get("node_description")):
            raise RuntimeError(f"Model graph update rejected: node {node_id} is missing node_description.")
        if node_id not in node_data and node_id not in allow_missing_node_data_ids:
            raise RuntimeError(f"Model graph update rejected: node {node_id} has no saved node_data.")

    edge_pairs: set[tuple[str, str]] = set()
    for edge in edges:
        source_id = _as_text(edge.get("sourceNodeId"))
        target_id = _as_text(edge.get("targetNodeId"))
        if source_id not in node_id_set or target_id not in node_id_set:
            raise RuntimeError(
                f"Model graph update rejected: edge {source_id}->{target_id} references a missing node."
            )
        edge_pairs.add((source_id, target_id))

    if len(nodes) > 1 and not edge_pairs:
        raise RuntimeError("Model graph update rejected: multi-node models must preserve graph links.")

    for node in nodes:
        if _as_text(node.get("nodeType")) != "calculation":
            continue
        node_id = _as_text(node.get("id"))
        input_ids = _unique_text_list(node.get("inputs"))
        if not input_ids:
            raise RuntimeError(f"Model graph update rejected: calculation node {node_id} must include inputs.")
        for input_id in input_ids:
            if input_id not in node_id_set:
                raise RuntimeError(
                    f"Model graph update rejected: calculation node {node_id} input {input_id} is missing."
                )
            if (input_id, node_id) not in edge_pairs:
                raise RuntimeError(
                    f"Model graph update rejected: calculation node {node_id} input {input_id} has no matching edge."
                )


def _is_symbol_only_calculation_node(node: Dict[str, Any]) -> bool:
    if _as_text(node.get("nodeType")) != "calculation":
        return False
    label = _as_text(node.get("node_title"))
    expression = _as_text(node.get("expression"))
    symbol_values = {"+", "-", "−", "*", "x", "×", "/", "÷", "="}
    if label not in symbol_values:
        return False
    if expression and expression not in symbol_values:
        return False
    return True


def normalize_model_builder_state(value: Any) -> Dict[str, Any]:
    raw = value if isinstance(value, dict) else {}
    graph = normalize_model_graph_state(raw)
    return {
        "variables": [
            _normalize_variable(item, index)
            for index, item in enumerate(raw.get("variables") if isinstance(raw.get("variables"), list) else [])
        ],
        "nodes": graph["nodes"],
        "edges": graph["edges"],
    }


def normalize_model_graph_state(value: Any) -> Dict[str, Any]:
    raw = value if isinstance(value, dict) else {}
    nodes = [
        _normalize_node(item, index)
        for index, item in enumerate(raw.get("nodes") if isinstance(raw.get("nodes"), list) else [])
    ]
    edges = []
    for index, item in enumerate(raw.get("edges") if isinstance(raw.get("edges"), list) else []):
        edge = _normalize_edge(item, index)
        if edge:
            edges.append(edge)
    return {
        "nodes": nodes,
        "edges": edges,
    }


def normalize_node_data(value: Any) -> Dict[str, Any]:
    raw = value if isinstance(value, dict) else {}
    normalized: Dict[str, Any] = {}
    for key, item in raw.items():
        node_id = _as_text(key)
        if not node_id or not isinstance(item, dict):
            continue
        columns = item.get("columns") if isinstance(item.get("columns"), list) else ["period", "value"]
        records = item.get("records") if isinstance(item.get("records"), list) else []
        series = item.get("series") if isinstance(item.get("series"), list) else []
        normalized[node_id] = {
            "kind": _as_text(item.get("kind")) or "calculated_series",
            "node_id": _as_text(item.get("node_id")) or node_id,
            "node_title": _as_text(item.get("node_title")),
            "unit": _as_text(item.get("unit")),
            "data_kind": _as_text(item.get("data_kind")) or "calculated",
            "computed_at": _as_text(item.get("computed_at")),
            "input_fingerprint": _as_text(item.get("input_fingerprint")),
            "columns": [_as_text(column) for column in columns if _as_text(column)] or ["period", "value"],
            "records": records,
        }
        if series:
            normalized[node_id]["series"] = series
    return normalized


def _edge_id(source: str, target: str) -> str:
    return _clean_id(f"{source}_{target}", f"{source}_{target}")


def _fetch_active_variables(conn, *, user_id: str, project_id: str) -> List[Dict[str, Any]]:
    rows = conn.execute(
        """
        select
          vv.id::text as id,
          vv.name,
          vv.label,
          vv.source_name as "sourceName",
          vv.metric,
          vv.unit,
          vv.geography,
          vv.frequency,
          vv.seasonal_treatment as "seasonalTreatment",
          vv.transform_summary as "transformSummary",
          vv.node_description as "nodeDescription",
          vv.contents_summary as "contentsSummary",
          vv.validated_data as contents,
          vv.validation_status as "validationStatus"
        from public.validated_variables vv
        join public.modelling_projects mp
          on mp.id = %(project_id)s and mp.user_id = %(user_id)s
        where vv.user_id = %(user_id)s
          and mp.active_validated_variable_ids ? vv.id::text
          and vv.validation_status = 'validated'
        order by vv.updated_at desc
        """,
        {"user_id": user_id, "project_id": project_id},
    ).fetchall()
    return [_normalize_variable(row, index) for index, row in enumerate(rows)]


def fetch_model_builder_state(*, user_id: str, project_id: str) -> Dict[str, Any]:
    user_id = _as_text(user_id)
    project_id = _as_text(project_id)
    if not user_id or not project_id:
        return {}
    with _connect() as conn:
        row = conn.execute(
            """
            select model_builder_state, model_graph_state, node_data, active_validated_variable_ids
            from public.modelling_projects
            where user_id = %s and id = %s
            limit 1
            """,
            (user_id, project_id),
        ).fetchone()
        active_variables = _fetch_active_variables(conn, user_id=user_id, project_id=project_id) if row else []
    if not row:
        return {}
    legacy_state = normalize_model_builder_state(row.get("model_builder_state"))
    graph_state = (
        normalize_model_graph_state(row.get("model_graph_state"))
        if "model_graph_state" in row
        else {"nodes": legacy_state["nodes"], "edges": legacy_state["edges"]}
    )
    node_data = normalize_node_data(row.get("node_data"))
    return {
        "variables": active_variables or legacy_state["variables"],
        "nodes": graph_state["nodes"],
        "edges": graph_state["edges"],
        "node_data": node_data,
    }


def save_node_data_state(
    *,
    user_id: str,
    project_id: str,
    node_id: str,
    result: Dict[str, Any],
) -> Dict[str, Any]:
    user_id = _as_text(user_id)
    project_id = _as_text(project_id)
    node_id = _as_text(node_id)
    if not user_id or not project_id or not node_id:
        raise RuntimeError("A Supabase user_id, project_id, and node_id are required to save node data.")
    with _connect() as conn:
        row = conn.execute(
            """
            update public.modelling_projects
            set node_data = jsonb_set(
                  coalesce(node_data, '{}'::jsonb),
                  array[%s]::text[],
                  %s::jsonb,
                  true
                ),
                updated_at = now()
            where user_id = %s and id = %s
            returning node_data
            """,
            (node_id, Json(_jsonable(result)), user_id, project_id),
        ).fetchone()
        conn.commit()
    if not row:
        raise RuntimeError("No matching project was found for this user.")
    return normalize_node_data(row.get("node_data"))


def update_model_builder_state(*, user_id: str, project_id: str, model_builder_state: Dict[str, Any]) -> Dict[str, Any]:
    user_id = _as_text(user_id)
    project_id = _as_text(project_id)
    if not user_id or not project_id:
        raise RuntimeError("A Supabase user_id and project_id are required to update the model builder.")
    normalized = normalize_model_builder_state(model_builder_state)
    graph_state = {
        "nodes": normalized["nodes"],
        "edges": normalized["edges"],
    }
    active_variable_ids = [
        variable["id"]
        for variable in normalized["variables"]
        if _as_text(variable.get("validationStatus")) == "validated" and _as_text(variable.get("id"))
    ]
    with _connect() as conn:
        current = conn.execute(
            """
            select node_data
            from public.modelling_projects
            where user_id = %(user_id)s and id = %(project_id)s
            limit 1
            """,
            {"user_id": user_id, "project_id": project_id},
        ).fetchone()
        current_node_data = normalize_node_data(current.get("node_data") if current else {})
        _validate_graph_integrity(
            nodes=graph_state["nodes"],
            edges=graph_state["edges"],
            node_data=current_node_data,
        )
        row = conn.execute(
            """
            update public.modelling_projects
            set model_builder_state = %(model_builder_state)s,
                model_graph_state = %(model_graph_state)s,
                active_validated_variable_ids = %(active_variable_ids)s,
                updated_at = now()
            where user_id = %(user_id)s and id = %(project_id)s
            returning model_builder_state, model_graph_state, active_validated_variable_ids
            """,
            {
                "user_id": user_id,
                "project_id": project_id,
                "model_builder_state": Json(_jsonable(normalized)),
                "model_graph_state": Json(_jsonable(graph_state)),
                "active_variable_ids": Json(active_variable_ids),
            },
        ).fetchone()
        conn.commit()
        active_variables = _fetch_active_variables(conn, user_id=user_id, project_id=project_id)
    if not row:
        raise RuntimeError("No matching project was found for this user.")
    graph_state = normalize_model_graph_state(row.get("model_graph_state"))
    return {
        "model_builder_state": {
            "variables": active_variables or normalized["variables"],
            "nodes": graph_state["nodes"],
            "edges": graph_state["edges"],
        },
        "active_validated_variable_ids": _text_list(row.get("active_validated_variable_ids")),
    }


def update_model_graph_state(
    *,
    user_id: str,
    project_id: str,
    nodes: List[Dict[str, Any]],
    edges: List[Dict[str, Any]],
    variables: Optional[List[Dict[str, Any]]] = None,
    allow_missing_node_data_ids: Optional[set[str]] = None,
) -> Dict[str, Any]:
    user_id = _as_text(user_id)
    project_id = _as_text(project_id)
    if not user_id or not project_id:
        raise RuntimeError("A Supabase user_id and project_id are required to update the model graph.")
    graph_state = normalize_model_graph_state({"nodes": nodes, "edges": edges})
    normalized_variables = [
        _normalize_variable(variable, index)
        for index, variable in enumerate(variables if isinstance(variables, list) else [])
    ]
    active_variable_ids = [_as_text(variable.get("id")) for variable in normalized_variables if _as_text(variable.get("id"))]
    with _connect() as conn:
        set_active_variables_sql = ""
        params: Dict[str, Any] = {
            "user_id": user_id,
            "project_id": project_id,
            "model_graph_state": Json(_jsonable(graph_state)),
        }
        if variables is not None:
            set_active_variables_sql = ", active_validated_variable_ids = %(active_variable_ids)s"
            params["active_variable_ids"] = Json(active_variable_ids)
        current = conn.execute(
            """
            select node_data
            from public.modelling_projects
            where user_id = %(user_id)s and id = %(project_id)s
            limit 1
            """,
            params,
        ).fetchone()
        current_node_data = normalize_node_data(current.get("node_data") if current else {})
        _validate_graph_integrity(
            nodes=graph_state["nodes"],
            edges=graph_state["edges"],
            node_data=current_node_data,
            allow_missing_node_data_ids=allow_missing_node_data_ids,
        )
        row = conn.execute(
            f"""
            update public.modelling_projects
            set model_graph_state = %(model_graph_state)s,
                model_builder_state = jsonb_set(
                  jsonb_set(coalesce(model_builder_state, '{{}}'::jsonb), '{{nodes}}', %(nodes)s::jsonb, true),
                  '{{edges}}',
                  %(edges)s::jsonb,
                  true
                )
                {set_active_variables_sql},
                updated_at = now()
            where user_id = %(user_id)s and id = %(project_id)s
            returning model_graph_state, active_validated_variable_ids
            """,
            {
                **params,
                "nodes": Json(_jsonable(graph_state["nodes"])),
                "edges": Json(_jsonable(graph_state["edges"])),
            },
        ).fetchone()
        conn.commit()
    if not row:
        raise RuntimeError("No matching project was found for this user.")
    return {
        "model_builder_state": fetch_model_builder_state(user_id=user_id, project_id=project_id),
        "active_validated_variable_ids": _text_list(row.get("active_validated_variable_ids")),
    }


def _upsert_by_id(items: List[Dict[str, Any]], item: Dict[str, Any]) -> List[Dict[str, Any]]:
    target = _as_text(item.get("id"))
    if not target:
        return items
    replaced = False
    updated: List[Dict[str, Any]] = []
    for current in items:
        if _as_text(current.get("id")) == target:
            updated.append(item)
            replaced = True
        else:
            updated.append(current)
    if not replaced:
        updated.append(item)
    return updated


def _node_label_matches_variable(node: Dict[str, Any], variable: Dict[str, Any]) -> bool:
    node_label = _as_text(node.get("node_title")).lower()
    variable_text = " ".join(
        _as_text(variable.get(key)).lower()
        for key in ("label", "name", "metric")
        if _as_text(variable.get(key))
    )
    if not node_label or not variable_text:
        return False
    return node_label in variable_text or variable_text in node_label


def _resolve_input_node_id(input_id: str, nodes: List[Dict[str, Any]], variables: List[Dict[str, Any]]) -> str:
    clean_input_id = _as_text(input_id)
    if not clean_input_id:
        return ""
    for node in nodes:
        if _as_text(node.get("id")) == clean_input_id:
            return clean_input_id
    for node in nodes:
        if _as_text(node.get("variableId")) == clean_input_id:
            return _as_text(node.get("id"))
    variable = next((item for item in variables if _as_text(item.get("id")) == clean_input_id), None)
    if variable:
        for node in nodes:
            if _as_text(node.get("nodeType")) == "variable" and _node_label_matches_variable(node, variable):
                return _as_text(node.get("id"))
    return clean_input_id


def _node_for_id(node_id: str, nodes: List[Dict[str, Any]]) -> Dict[str, Any]:
    return next((node for node in nodes if _as_text(node.get("id")) == node_id), {})


def _variable_for_node(node: Dict[str, Any], variables: List[Dict[str, Any]]) -> Dict[str, Any]:
    variable_id = _as_text(node.get("variableId")) or _as_text(node.get("id"))
    variable = next((item for item in variables if _as_text(item.get("id")) == variable_id), None)
    if variable:
        return variable
    return next((item for item in variables if _node_label_matches_variable(node, item)), {})


def _calculation_inputs_spec(input_node_ids: List[str], nodes: List[Dict[str, Any]], variables: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    specs: List[Dict[str, Any]] = []
    for input_node_id in input_node_ids:
        node = _node_for_id(input_node_id, nodes)
        variable = _variable_for_node(node, variables) if node else {}
        specs.append(
            {
                "nodeId": input_node_id,
                "nodeType": _as_text(node.get("nodeType")) or "unknown",
                "node_title": _as_text(node.get("node_title")) or _as_text(variable.get("label")) or input_node_id,
                "variableId": _as_text(node.get("variableId")) or _as_text(variable.get("id")),
                "sourceName": _as_text(variable.get("sourceName")),
                "metric": _as_text(variable.get("metric")),
                "unit": _as_text(variable.get("unit")),
            }
        )
    return specs


def _build_calculation_spec(
    *,
    node_id: str,
    node_title: str,
    input_node_ids: List[str],
    output_label: str,
    output_node_id: str,
    expression: str,
    method: str,
    node_description: str,
    calculation_logic: Dict[str, Any],
    parameters: Dict[str, Any],
    nodes: List[Dict[str, Any]],
    variables: List[Dict[str, Any]],
) -> Dict[str, Any]:
    raw_logic = calculation_logic if isinstance(calculation_logic, dict) else {}
    replay = raw_logic.get("replay") if isinstance(raw_logic.get("replay"), dict) else {}
    formula = _as_text(raw_logic.get("formula") or replay.get("formula") or expression)
    code = _as_text(raw_logic.get("code") or replay.get("code"))
    language = _as_text(raw_logic.get("language") or replay.get("language"))
    steps = raw_logic.get("steps") or replay.get("steps")
    if not isinstance(steps, list):
        steps = [_as_text(method) or _as_text(node_description) or _as_text(node_title)]

    return {
        "version": 1,
        "kind": "custom_calculation",
        "nodeId": node_id,
        "node_title": node_title,
        "node_description": node_description,
        "method": method,
        "expression": expression,
        "inputs": _calculation_inputs_spec(input_node_ids, nodes, variables),
        "parameters": parameters,
        "output": {
            "label": output_label,
            "nodeId": output_node_id,
        },
        "replay": {
            "language": language or ("python" if code else "formula"),
            "formula": formula,
            "code": code,
            "steps": [_as_text(step) for step in steps if _as_text(step)],
        },
        "rawLogic": raw_logic,
    }


def save_custom_calculation_state(
    *,
    user_id: str,
    project_id: str,
    node_id: str,
    node_title: str,
    input_node_ids: List[str],
    output_label: str = "",
    expression: str = "",
    method: str = "",
    node_description: str = "",
    calculation_logic: Optional[Dict[str, Any]] = None,
    parameters: Optional[Dict[str, Any]] = None,
    position_x: float | None = None,
    position_y: float | None = None,
) -> Dict[str, Any]:
    user_id = _as_text(user_id)
    project_id = _as_text(project_id)
    if not user_id or not project_id:
        raise RuntimeError("A Supabase user_id and project_id are required to save a custom calculation.")
    clean_node_title = _as_text(node_title) or _as_text(method) or "Custom calculation"
    clean_node_id = _clean_id(node_id or clean_node_title or method, "custom_calculation")
    current = fetch_model_builder_state(user_id=user_id, project_id=project_id)
    nodes = list(current.get("nodes") if isinstance(current.get("nodes"), list) else [])
    edges = list(current.get("edges") if isinstance(current.get("edges"), list) else [])
    variables = list(current.get("variables") if isinstance(current.get("variables"), list) else [])
    clean_input_node_ids = [
        resolved
        for resolved in (_resolve_input_node_id(item, nodes, variables) for item in input_node_ids)
        if resolved
    ]
    clean_input_node_ids = _unique_text_list(clean_input_node_ids)
    if not clean_input_node_ids:
        raise RuntimeError("A custom calculation must include at least one input node id.")

    logic = calculation_logic if isinstance(calculation_logic, dict) else {}
    params = parameters if isinstance(parameters, dict) else {}
    clean_node_description = _as_text(node_description)
    if not clean_node_description:
        raise RuntimeError("A custom calculation node must include an explicit description.")
    clean_expression = _as_text(expression) or _as_text(method) or "custom"
    calculation_spec = _build_calculation_spec(
        node_id=clean_node_id,
        node_title=clean_node_title,
        input_node_ids=clean_input_node_ids,
        output_label=_as_text(output_label),
        output_node_id="",
        expression=clean_expression,
        method=_as_text(method),
        node_description=clean_node_description,
        calculation_logic=logic,
        parameters=params,
        nodes=nodes,
        variables=variables,
    )
    node: Dict[str, Any] = {
        "id": clean_node_id,
        "node_title": clean_node_title,
        "node_description": clean_node_description,
        "nodeType": "calculation",
        "expression": clean_expression,
        "method": _as_text(method),
        "inputs": clean_input_node_ids,
        "output": _as_text(output_label),
        "parameters": params,
        "calculationLogic": logic,
        "calculationSpec": calculation_spec,
    }
    source_node = next((item for item in nodes if _as_text(item.get("id")) == clean_input_node_ids[0]), {})
    source_x = float(source_node.get("positionX") or 0)
    source_y = float(source_node.get("positionY") or 0)
    if position_x is not None:
        node["positionX"] = position_x
    else:
        node["positionX"] = source_x
    if position_y is not None:
        node["positionY"] = position_y
    else:
        node["positionY"] = source_y + 168
    nodes = _upsert_by_id(nodes, _normalize_node(node, len(nodes)))
    node_map = {_as_text(item.get("id")): item for item in nodes}
    stale_result_node_ids = {
        _as_text(item.get("id"))
        for item in nodes
        if _as_text(item.get("nodeType")) == "result"
        and (
            _as_text(item.get("sourceCalculationId")) == clean_node_id
            or _as_text(item.get("id")) == _clean_id(f"{clean_node_id}_result", f"{clean_node_id}_result")
        )
    }
    if stale_result_node_ids:
        nodes = [item for item in nodes if _as_text(item.get("id")) not in stale_result_node_ids]
        node_map = {_as_text(item.get("id")): item for item in nodes}
    edges = [
        edge
        for edge in edges
        if not (
            _as_text(edge.get("targetNodeId")) == clean_node_id
            and _as_text(edge.get("sourceNodeId")) not in clean_input_node_ids
        )
        and _as_text(edge.get("sourceNodeId")) not in stale_result_node_ids
        and _as_text(edge.get("targetNodeId")) not in stale_result_node_ids
    ]

    for input_node_id in clean_input_node_ids:
        edge = {
            "id": _edge_id(input_node_id, clean_node_id),
            "sourceNodeId": input_node_id,
            "targetNodeId": clean_node_id,
        }
        if not any(_as_text(item.get("id")) == edge["id"] for item in edges if isinstance(item, dict)):
            edges.append(edge)

    result = update_model_graph_state(
        user_id=user_id,
        project_id=project_id,
        nodes=nodes,
        edges=edges,
        allow_missing_node_data_ids={clean_node_id},
    )
    return {
        "model_builder_state": result["model_builder_state"],
        "calculation_node_id": clean_node_id,
    }
