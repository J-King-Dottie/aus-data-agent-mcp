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
    return {
        "id": variable_id,
        "name": _as_text(raw.get("name")) or variable_id,
        "label": _as_text(raw.get("label")) or _as_text(raw.get("name")) or f"Variable {index + 1}",
        "sourceName": _as_text(raw.get("sourceName") or raw.get("source_name")),
        "metric": _as_text(raw.get("metric")),
        "unit": _as_text(raw.get("unit")),
        "transformSummary": _as_text(raw.get("transformSummary") or raw.get("transform_summary")),
        "validationStatus": status if status in {"candidate", "rejected"} else "validated",
    }


def _normalize_assumption(value: Any, index: int) -> Dict[str, Any]:
    raw = value if isinstance(value, dict) else {}
    return {
        "id": _as_text(raw.get("id")) or f"assumption-{index + 1}",
        "variableId": _as_text(raw.get("variableId") or raw.get("variable_id") or raw.get("variable")),
        "label": _as_text(raw.get("label")) or "Assumption",
        "valueText": _as_text(raw.get("valueText") or raw.get("value_text")),
    }


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
    ):
        text = _as_text(raw.get(source_key))
        if text:
            node[target_key] = text
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
    with _connect() as conn:
        row = conn.execute(
            """
            update public.modelling_projects
            set model_assumptions = %(model_assumptions)s,
                model_builder_state = jsonb_set(
                  coalesce(model_builder_state, '{}'::jsonb),
                  '{assumptions}',
                  %(model_assumptions)s::jsonb,
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
