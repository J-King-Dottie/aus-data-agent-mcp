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
    contents_summary = _as_text(raw.get("contentsSummary") or raw.get("contents_summary"))
    if contents_summary:
        normalized["contentsSummary"] = contents_summary
    contents = raw.get("contents")
    if isinstance(contents, dict):
        normalized["contents"] = contents
    return normalized


def _normalize_assumption(value: Any, index: int) -> Dict[str, Any]:
    raw = value if isinstance(value, dict) else {}
    assumption = {
        "id": _as_text(raw.get("id")) or f"assumption-{index + 1}",
        "variableId": _as_text(raw.get("variableId") or raw.get("variable_id") or raw.get("variable")),
        "label": _as_text(raw.get("label")) or "Assumption",
        "valueText": _as_text(raw.get("valueText") or raw.get("value_text")),
    }
    for source_key, target_key in (
        ("nodeId", "nodeId"),
        ("node_id", "nodeId"),
        ("calculationNodeId", "nodeId"),
        ("calculation_node_id", "nodeId"),
        ("method", "method"),
        ("output", "output"),
        ("logicSummary", "logicSummary"),
        ("logic_summary", "logicSummary"),
    ):
        text = _as_text(raw.get(source_key))
        if text:
            assumption[target_key] = text
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
            assumption[target_key] = item
    return assumption


def _normalize_node(value: Any, index: int) -> Dict[str, Any]:
    raw = value if isinstance(value, dict) else {}
    node_id = _as_text(raw.get("id")) or f"node-{index + 1}"
    node_type = _as_text(raw.get("nodeType") or raw.get("node_type"))
    if node_type not in {"variable", "assumption", "calculation", "result"}:
        node_type = "variable"
    node: Dict[str, Any] = {
        "id": node_id,
        "label": _as_text(raw.get("label")) or node_id,
        "nodeType": node_type,
    }
    for source_key, target_key in (
        ("variableId", "variableId"),
        ("variable_id", "variableId"),
        ("expression", "expression"),
        ("assumptionId", "assumptionId"),
        ("assumption_id", "assumptionId"),
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


def normalize_model_builder_state(value: Any) -> Dict[str, Any]:
    raw = value if isinstance(value, dict) else {}
    graph = normalize_model_graph_state(raw)
    return {
        "variables": [
            _normalize_variable(item, index)
            for index, item in enumerate(raw.get("variables") if isinstance(raw.get("variables"), list) else [])
        ],
        "assumptions": normalize_model_assumptions(raw.get("assumptions")),
        "nodes": graph["nodes"],
        "edges": graph["edges"],
    }


def normalize_model_assumptions(value: Any) -> List[Dict[str, Any]]:
    return [
        _normalize_assumption(item, index)
        for index, item in enumerate(value if isinstance(value, list) else [])
    ]


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
    edges = _repair_legacy_operation_bypass_edges(nodes, edges)
    edges = _repair_edges_from_calculation_inputs(nodes, edges)
    return {
        "nodes": nodes,
        "edges": edges,
    }


def _repair_legacy_operation_bypass_edges(nodes: List[Dict[str, Any]], edges: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    node_map = {str(node.get("id")): node for node in nodes}
    operation_to_result = [
        edge
        for edge in edges
        if node_map.get(str(edge.get("sourceNodeId")), {}).get("nodeType") == "calculation"
        and node_map.get(str(edge.get("targetNodeId")), {}).get("nodeType") == "result"
    ]
    if not operation_to_result:
        return edges

    repaired: List[Dict[str, Any]] = []
    for edge in edges:
        source = node_map.get(str(edge.get("sourceNodeId")))
        target = node_map.get(str(edge.get("targetNodeId")))
        if not source or target is None or target.get("nodeType") != "result":
            repaired.append(edge)
            continue
        if source.get("nodeType") == "calculation":
            repaired.append(edge)
            continue
        matching_operation_edge = next(
            (candidate for candidate in operation_to_result if candidate.get("targetNodeId") == edge.get("targetNodeId")),
            None,
        )
        if not matching_operation_edge:
            repaired.append(edge)
            continue
        already_feeds_operation = any(
            candidate.get("sourceNodeId") == edge.get("sourceNodeId")
            and candidate.get("targetNodeId") == matching_operation_edge.get("sourceNodeId")
            for candidate in edges
        )
        if already_feeds_operation:
            continue
        base_edge_id = _as_text(edge.get("id")) or f"{edge.get('sourceNodeId')}-{matching_operation_edge.get('sourceNodeId')}"
        repaired.append(
            {
                "id": f"{base_edge_id}-rewired",
                "sourceNodeId": edge.get("sourceNodeId"),
                "targetNodeId": matching_operation_edge.get("sourceNodeId"),
            }
        )
    return repaired


def _edge_id(source: str, target: str) -> str:
    return _clean_id(f"{source}_{target}", f"{source}_{target}")


def _repair_edges_from_calculation_inputs(nodes: List[Dict[str, Any]], edges: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    calculation_inputs = {
        _as_text(node.get("id")): _unique_text_list(node.get("inputs"))
        for node in nodes
        if _as_text(node.get("nodeType")) == "calculation" and _unique_text_list(node.get("inputs"))
    }
    if not calculation_inputs:
        return edges

    repaired = [
        edge
        for edge in edges
        if _as_text(edge.get("targetNodeId")) not in calculation_inputs
        or _as_text(edge.get("sourceNodeId")) in calculation_inputs[_as_text(edge.get("targetNodeId"))]
    ]
    existing_pairs = {
        (_as_text(edge.get("sourceNodeId")), _as_text(edge.get("targetNodeId")))
        for edge in repaired
    }
    for target_node_id, input_node_ids in calculation_inputs.items():
        for input_node_id in input_node_ids:
            pair = (input_node_id, target_node_id)
            if pair in existing_pairs:
                continue
            repaired.append(
                {
                    "id": _edge_id(input_node_id, target_node_id),
                    "sourceNodeId": input_node_id,
                    "targetNodeId": target_node_id,
                }
            )
            existing_pairs.add(pair)
    return repaired


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
          vv.evidence_artifact->>'contents_summary' as "contentsSummary",
          vv.evidence_artifact->'contents' as contents,
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
        try:
            row = conn.execute(
                """
                select model_builder_state, model_assumptions, model_graph_state, active_validated_variable_ids
                from public.modelling_projects
                where user_id = %s and id = %s
                limit 1
                """,
                (user_id, project_id),
            ).fetchone()
        except psycopg.errors.UndefinedColumn:
            conn.rollback()
            row = conn.execute(
                """
                select model_builder_state, active_validated_variable_ids
                from public.modelling_projects
                where user_id = %s and id = %s
                limit 1
                """,
                (user_id, project_id),
            ).fetchone()
        active_variables = _fetch_active_variables(conn, user_id=user_id, project_id=project_id) if row else []
    if not row:
        return {}
    has_split_state = "model_assumptions" in row and "model_graph_state" in row
    legacy_state = normalize_model_builder_state(row.get("model_builder_state"))
    graph_state = (
        normalize_model_graph_state(row.get("model_graph_state"))
        if has_split_state
        else {"nodes": legacy_state["nodes"], "edges": legacy_state["edges"]}
    )
    assumptions = normalize_model_assumptions(row.get("model_assumptions")) if has_split_state else legacy_state["assumptions"]
    return {
        "variables": active_variables or legacy_state["variables"],
        "assumptions": assumptions,
        "nodes": graph_state["nodes"],
        "edges": graph_state["edges"],
    }


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
        row = conn.execute(
            """
            update public.modelling_projects
            set model_builder_state = %(model_builder_state)s,
                model_assumptions = %(model_assumptions)s,
                model_graph_state = %(model_graph_state)s,
                active_validated_variable_ids = %(active_variable_ids)s,
                updated_at = now()
            where user_id = %(user_id)s and id = %(project_id)s
            returning model_builder_state, model_assumptions, model_graph_state, active_validated_variable_ids
            """,
            {
                "user_id": user_id,
                "project_id": project_id,
                "model_builder_state": Json(_jsonable(normalized)),
                "model_assumptions": Json(_jsonable(normalized["assumptions"])),
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
            "assumptions": normalize_model_assumptions(row.get("model_assumptions")),
            "nodes": graph_state["nodes"],
            "edges": graph_state["edges"],
        },
        "active_validated_variable_ids": _text_list(row.get("active_validated_variable_ids")),
    }


def update_model_assumptions_state(
    *,
    user_id: str,
    project_id: str,
    assumptions: List[Dict[str, Any]],
) -> Dict[str, Any]:
    user_id = _as_text(user_id)
    project_id = _as_text(project_id)
    if not user_id or not project_id:
        raise RuntimeError("A Supabase user_id and project_id are required to update model assumptions.")
    normalized = normalize_model_assumptions(assumptions)
    current = fetch_model_builder_state(user_id=user_id, project_id=project_id)
    graph_nodes = list(current.get("nodes") if isinstance(current.get("nodes"), list) else [])
    graph_edges = list(current.get("edges") if isinstance(current.get("edges"), list) else [])
    variables = list(current.get("variables") if isinstance(current.get("variables"), list) else [])
    graph_nodes, graph_edges, normalized = _append_missing_custom_calculation_nodes(
        nodes=graph_nodes,
        edges=graph_edges,
        assumptions=normalized,
        variables=variables,
    )
    graph_state = normalize_model_graph_state({"nodes": graph_nodes, "edges": graph_edges})
    with _connect() as conn:
        row = conn.execute(
            """
            update public.modelling_projects
            set model_assumptions = %(model_assumptions)s,
                model_graph_state = %(model_graph_state)s,
                model_builder_state = jsonb_set(
                  jsonb_set(
                    jsonb_set(coalesce(model_builder_state, '{}'::jsonb), '{assumptions}', %(model_assumptions)s::jsonb, true),
                    '{nodes}',
                    %(nodes)s::jsonb,
                    true
                  ),
                  '{edges}',
                  %(edges)s::jsonb,
                  true
                ),
                updated_at = now()
            where user_id = %(user_id)s and id = %(project_id)s
            returning model_assumptions
            """,
            {
                "user_id": user_id,
                "project_id": project_id,
                "model_assumptions": Json(_jsonable(normalized)),
                "model_graph_state": Json(_jsonable(graph_state)),
                "nodes": Json(_jsonable(graph_state["nodes"])),
                "edges": Json(_jsonable(graph_state["edges"])),
            },
        ).fetchone()
        conn.commit()
    if not row:
        raise RuntimeError("No matching project was found for this user.")
    return {
        "model_builder_state": fetch_model_builder_state(user_id=user_id, project_id=project_id),
    }


def update_model_graph_state(
    *,
    user_id: str,
    project_id: str,
    nodes: List[Dict[str, Any]],
    edges: List[Dict[str, Any]],
    variables: Optional[List[Dict[str, Any]]] = None,
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
    node_label = _as_text(node.get("label")).lower()
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
                "label": _as_text(node.get("label")) or _as_text(variable.get("label")) or input_node_id,
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
    label: str,
    input_node_ids: List[str],
    output_label: str,
    output_node_id: str,
    expression: str,
    method: str,
    assumption_text: str,
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
        steps = [_as_text(method) or _as_text(assumption_text) or _as_text(label)]

    return {
        "version": 1,
        "kind": "custom_calculation",
        "nodeId": node_id,
        "label": label,
        "assumption": assumption_text,
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


def _assumption_implies_custom_calculation(assumption: Dict[str, Any]) -> bool:
    if _as_text(assumption.get("nodeId")) or isinstance(assumption.get("calculationLogic"), dict):
        return True
    text = " ".join(
        _as_text(assumption.get(key)).lower()
        for key in ("label", "valueText", "method", "logicSummary")
    )
    markers = (
        "project",
        "projection",
        "forecast",
        "scenario",
        "continue",
        "continues",
        "average",
        "growth",
        "cagr",
        "annualise",
        "annualize",
        "return to",
        "hold",
        "constant",
    )
    return any(marker in text for marker in markers)


def _append_missing_custom_calculation_nodes(
    *,
    nodes: List[Dict[str, Any]],
    edges: List[Dict[str, Any]],
    assumptions: List[Dict[str, Any]],
    variables: List[Dict[str, Any]],
) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]]]:
    updated_nodes = list(nodes)
    updated_edges = list(edges)
    updated_assumptions = list(assumptions)
    existing_node_ids = {_as_text(node.get("id")) for node in updated_nodes}

    for index, assumption in enumerate(updated_assumptions):
        if not _assumption_implies_custom_calculation(assumption):
            continue
        node_id = _as_text(assumption.get("nodeId")) or _clean_id(f"{assumption.get('id')}_calculation", f"assumption_{index + 1}_calculation")
        if node_id in existing_node_ids:
            if not _as_text(assumption.get("nodeId")):
                assumption["nodeId"] = node_id
            continue

        raw_inputs = _text_list(assumption.get("inputs"))
        variable_id = _as_text(assumption.get("variableId"))
        if not raw_inputs and variable_id:
            raw_inputs = [variable_id]
        input_node_ids = [
            resolved
            for resolved in (_resolve_input_node_id(input_id, updated_nodes, variables) for input_id in raw_inputs)
            if resolved
        ]
        if not input_node_ids:
            continue

        source_node = next((node for node in updated_nodes if _as_text(node.get("id")) == input_node_ids[0]), {})
        source_x = float(source_node.get("positionX") or 0)
        source_y = float(source_node.get("positionY") or 0)
        output_label = _as_text(assumption.get("output")) or _as_text(assumption.get("label")) or "Calculated result"
        result_node_id = _clean_id(f"{node_id}_result", f"{node_id}_result")
        params = assumption.get("parameters") if isinstance(assumption.get("parameters"), dict) else {}
        logic = assumption.get("calculationLogic") if isinstance(assumption.get("calculationLogic"), dict) else {}
        calculation_spec = _build_calculation_spec(
            node_id=node_id,
            label=_as_text(assumption.get("method")) or _as_text(assumption.get("label")) or "Custom calculation",
            input_node_ids=input_node_ids,
            output_label=output_label,
            output_node_id=result_node_id,
            expression=_as_text(assumption.get("method")) or "custom",
            method=_as_text(assumption.get("method")),
            assumption_text=_as_text(assumption.get("valueText")),
            calculation_logic=logic,
            parameters=params,
            nodes=updated_nodes,
            variables=variables,
        )
        calculation_node = _normalize_node(
            {
                "id": node_id,
                "label": _as_text(assumption.get("method")) or _as_text(assumption.get("label")) or "Custom calculation",
                "nodeType": "calculation",
                "expression": _as_text(assumption.get("method")) or "custom",
                "assumptionId": _as_text(assumption.get("id")),
                "method": _as_text(assumption.get("method")),
                "inputs": input_node_ids,
                "output": output_label,
                "logicSummary": _as_text(assumption.get("valueText")),
                "parameters": params,
                "calculationLogic": logic,
                "calculationSpec": calculation_spec,
                "positionX": source_x + 220,
                "positionY": source_y,
            },
            len(updated_nodes),
        )
        updated_nodes.append(calculation_node)
        existing_node_ids.add(node_id)
        assumption["nodeId"] = node_id
        assumption["calculationSpec"] = calculation_spec

        for input_node_id in input_node_ids:
            edge = {
                "id": _edge_id(input_node_id, node_id),
                "sourceNodeId": input_node_id,
                "targetNodeId": node_id,
            }
            if not any(_as_text(item.get("id")) == edge["id"] for item in updated_edges if isinstance(item, dict)):
                updated_edges.append(edge)

        if result_node_id not in existing_node_ids:
            updated_nodes.append(
                _normalize_node(
                    {
                        "id": result_node_id,
                        "label": output_label,
                        "nodeType": "result",
                        "logicSummary": _as_text(assumption.get("valueText")),
                        "calculationSpec": calculation_spec,
                        "positionX": source_x + 320,
                        "positionY": source_y,
                    },
                    len(updated_nodes),
                )
            )
            existing_node_ids.add(result_node_id)
        result_edge = {
            "id": _edge_id(node_id, result_node_id),
            "sourceNodeId": node_id,
            "targetNodeId": result_node_id,
        }
        if not any(_as_text(item.get("id")) == result_edge["id"] for item in updated_edges if isinstance(item, dict)):
            updated_edges.append(result_edge)

    return updated_nodes, updated_edges, updated_assumptions


def save_custom_calculation_state(
    *,
    user_id: str,
    project_id: str,
    node_id: str,
    label: str,
    input_node_ids: List[str],
    output_label: str = "",
    expression: str = "",
    method: str = "",
    assumption_text: str = "",
    assumption_label: str = "",
    calculation_logic: Optional[Dict[str, Any]] = None,
    parameters: Optional[Dict[str, Any]] = None,
    position_x: float | None = None,
    position_y: float | None = None,
) -> Dict[str, Any]:
    user_id = _as_text(user_id)
    project_id = _as_text(project_id)
    if not user_id or not project_id:
        raise RuntimeError("A Supabase user_id and project_id are required to save a custom calculation.")
    clean_node_id = _clean_id(node_id or label or method, "custom_calculation")
    current = fetch_model_builder_state(user_id=user_id, project_id=project_id)
    nodes = list(current.get("nodes") if isinstance(current.get("nodes"), list) else [])
    edges = list(current.get("edges") if isinstance(current.get("edges"), list) else [])
    assumptions = list(current.get("assumptions") if isinstance(current.get("assumptions"), list) else [])
    variables = list(current.get("variables") if isinstance(current.get("variables"), list) else [])
    clean_input_node_ids = [
        resolved
        for resolved in (_resolve_input_node_id(item, nodes, variables) for item in input_node_ids)
        if resolved
    ]
    clean_input_node_ids = _unique_text_list(clean_input_node_ids)
    if not clean_input_node_ids:
        raise RuntimeError("A custom calculation must include at least one input node id.")

    clean_assumption_text = _as_text(assumption_text)
    assumption_id = _clean_id(f"{clean_node_id}_assumption", f"{clean_node_id}_assumption") if clean_assumption_text else ""
    logic = calculation_logic if isinstance(calculation_logic, dict) else {}
    params = parameters if isinstance(parameters, dict) else {}
    result_node_id = _clean_id(f"{clean_node_id}_result", f"{clean_node_id}_result") if _as_text(output_label) else ""
    calculation_spec = _build_calculation_spec(
        node_id=clean_node_id,
        label=_as_text(label) or _as_text(method) or "Custom calculation",
        input_node_ids=clean_input_node_ids,
        output_label=_as_text(output_label),
        output_node_id=result_node_id,
        expression=_as_text(expression) or _as_text(method) or "custom",
        method=_as_text(method),
        assumption_text=clean_assumption_text,
        calculation_logic=logic,
        parameters=params,
        nodes=nodes,
        variables=variables,
    )
    node: Dict[str, Any] = {
        "id": clean_node_id,
        "label": _as_text(label) or _as_text(method) or "Custom calculation",
        "nodeType": "calculation",
        "expression": _as_text(expression) or _as_text(method) or "custom",
        "method": _as_text(method),
        "inputs": clean_input_node_ids,
        "output": _as_text(output_label),
        "logicSummary": clean_assumption_text or _as_text(logic.get("summary")),
        "parameters": params,
        "calculationLogic": logic,
        "calculationSpec": calculation_spec,
    }
    if assumption_id:
        node["assumptionId"] = assumption_id
    if position_x is not None:
        node["positionX"] = position_x
    if position_y is not None:
        node["positionY"] = position_y
    nodes = _upsert_by_id(nodes, _normalize_node(node, len(nodes)))
    node_map = {_as_text(item.get("id")): item for item in nodes}
    edges = [
        edge
        for edge in edges
        if not (
            _as_text(edge.get("targetNodeId")) == clean_node_id
            and _as_text(edge.get("sourceNodeId")) not in clean_input_node_ids
        )
        and not (
            result_node_id
            and _as_text(edge.get("sourceNodeId")) == clean_node_id
            and _as_text(edge.get("targetNodeId")) != result_node_id
            and _as_text(node_map.get(_as_text(edge.get("targetNodeId")), {}).get("nodeType")) == "result"
        )
    ]

    for input_node_id in clean_input_node_ids:
        edge = {
            "id": _edge_id(input_node_id, clean_node_id),
            "sourceNodeId": input_node_id,
            "targetNodeId": clean_node_id,
        }
        if not any(_as_text(item.get("id")) == edge["id"] for item in edges if isinstance(item, dict)):
            edges.append(edge)

    if _as_text(output_label):
        source_node = next((item for item in nodes if _as_text(item.get("id")) == clean_input_node_ids[0]), {})
        result_node = {
            "id": result_node_id,
            "label": _as_text(output_label),
            "nodeType": "result",
            "logicSummary": clean_assumption_text or _as_text(logic.get("summary")),
            "sourceCalculationId": clean_node_id,
            "calculationSpec": calculation_spec,
            "positionX": float(source_node.get("positionX") or 0) + 320,
            "positionY": float(source_node.get("positionY") or 0),
        }
        nodes = _upsert_by_id(nodes, _normalize_node(result_node, len(nodes)))
        result_edge = {
            "id": _edge_id(clean_node_id, result_node_id),
            "sourceNodeId": clean_node_id,
            "targetNodeId": result_node_id,
        }
        if not any(_as_text(item.get("id")) == result_edge["id"] for item in edges if isinstance(item, dict)):
            edges.append(result_edge)

    if clean_assumption_text:
        assumption = {
            "id": assumption_id,
            "nodeId": clean_node_id,
            "label": _as_text(assumption_label) or clean_assumption_text,
            "valueText": clean_assumption_text,
            "method": _as_text(method),
            "inputs": clean_input_node_ids,
            "output": _as_text(output_label),
            "parameters": params,
            "calculationLogic": logic,
            "calculationSpec": calculation_spec,
            "logicSummary": clean_assumption_text,
        }
        assumptions = _upsert_by_id(assumptions, _normalize_assumption(assumption, len(assumptions)))
        update_model_assumptions_state(user_id=user_id, project_id=project_id, assumptions=assumptions)

    result = update_model_graph_state(user_id=user_id, project_id=project_id, nodes=nodes, edges=edges)
    return {
        "model_builder_state": result["model_builder_state"],
        "calculation_node_id": clean_node_id,
        "assumption_id": assumption_id,
        "saved_assumption": bool(clean_assumption_text),
    }
