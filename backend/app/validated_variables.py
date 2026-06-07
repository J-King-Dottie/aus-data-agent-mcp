from __future__ import annotations

import hashlib
import json
import logging
import re
from contextlib import contextmanager
from threading import Lock
from typing import Any, Dict

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Json

from .config import get_settings


logger = logging.getLogger("abs.backend.validated_variables")
_MCP_REPLAY_LOCK = Lock()
ALLOWED_MCP_REPLAY_TOOLS = {
    "retrieve",
    "narrow_artifact",
}
TRANSFORMATION_KEYS = {"transform_code", "code", "formula", "steps"}
URL_KEYS = (
    "validated_api_url",
    "api_request_urls",
    "api_request_url",
    "request_url",
)


def _as_text(value: Any) -> str:
    return str(value or "").strip()


def _jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _slug(value: str) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")
    return cleaned[:72] or "validated_variable"


def _external_key(payload: Dict[str, Any]) -> str:
    source = json.dumps(_jsonable(payload), ensure_ascii=False, sort_keys=True)
    digest = hashlib.sha256(source.encode("utf-8")).hexdigest()[:16]
    label = _slug(_as_text(payload.get("name")) or _as_text(payload.get("metric")))
    return f"{label}_{digest}"


def _connect():
    database_url = _as_text(get_settings().database_url)
    if not database_url:
        raise RuntimeError("DATABASE_URL is not configured.")
    return psycopg.connect(database_url, connect_timeout=20, row_factory=dict_row)


def _looks_like_url(value: Any) -> str:
    text = _as_text(value)
    if "…" in text or (text.endswith("...") and not text.endswith("....")):
        return ""
    if text.startswith(("https://", "http://")):
        return text
    return ""


def _extract_validated_api_url(*values: Any) -> str:
    def walk(value: Any) -> str:
        if isinstance(value, dict):
            for key in URL_KEYS:
                candidate = value.get(key)
                if isinstance(candidate, list):
                    for item in candidate:
                        found = _looks_like_url(item)
                        if found:
                            return found
                else:
                    found = _looks_like_url(candidate)
                    if found:
                        return found
            for item in value.values():
                found = walk(item)
                if found:
                    return found
        if isinstance(value, list):
            for item in value:
                found = walk(item)
                if found:
                    return found
        return _looks_like_url(value)

    for value in values:
        found = walk(value)
        if found:
            return found
    return ""


def _lookup_path(value: Any, path: str) -> Any:
    current = value
    for part in path.split("."):
        if isinstance(current, dict):
            current = current.get(part)
        elif isinstance(current, list) and part.isdigit():
            index = int(part)
            current = current[index] if 0 <= index < len(current) else None
        else:
            return None
    return current


def _interpolate(value: Any, outputs: Dict[str, Any]) -> Any:
    if isinstance(value, str):
        if value.startswith("${") and value.endswith("}"):
            token = value[2:-1].strip()
            if token == "latest_artifact_id":
                return outputs.get("_latest_artifact_id") or ""
            if "." in token:
                step_id, path = token.split(".", 1)
                return _lookup_path(outputs.get(step_id), path) or ""
            return outputs.get(token) or ""
        return value
    if isinstance(value, dict):
        return {key: _interpolate(item, outputs) for key, item in value.items()}
    if isinstance(value, list):
        return [_interpolate(item, outputs) for item in value]
    return value


def _extract_artifact_id(payload: Any) -> str:
    if not isinstance(payload, dict):
        return ""
    for key in ("artifact_id", "artifactId", "id"):
        value = _as_text(payload.get(key))
        if value:
            return value
    artifacts = payload.get("artifacts")
    if isinstance(artifacts, list) and artifacts:
        first = artifacts[0]
        if isinstance(first, dict):
            return _as_text(first.get("artifact_id")) or _as_text(first.get("artifactId")) or _as_text(first.get("id"))
    jobs = payload.get("jobs")
    if isinstance(jobs, list):
        for job in jobs:
            if isinstance(job, dict):
                artifact_id = _extract_artifact_id(job.get("result"))
                if artifact_id:
                    return artifact_id
    return ""


def _summarize_replay_output(payload: Any) -> Dict[str, Any]:
    if not isinstance(payload, dict):
        return {"value": payload}
    summary: Dict[str, Any] = {}
    for key in (
        "artifact_id",
        "artifactId",
        "kind",
        "label",
        "dataset_id",
        "datasetId",
        "row_count",
        "series_count",
        "analysis_file",
        "download_filename",
        "summary",
        "columns",
        "dimensions",
    ):
        if key in payload:
            summary[key] = payload.get(key)
    if "jobs" in payload and isinstance(payload.get("jobs"), list):
        summary["jobs"] = [
            {
                "ok": job.get("ok"),
                "datasetId": job.get("datasetId"),
                "artifact_id": _extract_artifact_id(job.get("result")),
            }
            for job in payload.get("jobs", [])
            if isinstance(job, dict)
        ]
    return summary or {"keys": sorted(str(key) for key in payload.keys())[:20]}


@contextmanager
def _mcp_replay_context(conversation_id: str = "", code_container_id: str = ""):
    from . import unified_mcp_server as mcp_tools

    previous_conversation_id = mcp_tools.CONVERSATION_ID
    previous_code_container_id = mcp_tools.CODE_CONTAINER_ID
    if conversation_id:
        mcp_tools.CONVERSATION_ID = conversation_id
    if code_container_id:
        mcp_tools.CODE_CONTAINER_ID = code_container_id
    try:
        yield mcp_tools
    finally:
        mcp_tools.CONVERSATION_ID = previous_conversation_id
        mcp_tools.CODE_CONTAINER_ID = previous_code_container_id


def _replay_mcp_tool(mcp_tools: Any, tool: str, args: Dict[str, Any]) -> Dict[str, Any]:
    tool_map = {
        "retrieve": mcp_tools.retrieve,
        "narrow_artifact": mcp_tools.narrow_artifact,
    }
    if tool not in tool_map:
        raise RuntimeError(f"Validated-variable replay does not allow tool '{tool}'.")
    return tool_map[tool](**args)


def _row_dicts(headers: list[str], rows: list[list[Any]]) -> list[Dict[str, Any]]:
    return [
        {headers[index]: row[index] for index in range(min(len(headers), len(row)))}
        for row in rows
    ]


def _period_sort_key(row: Dict[str, Any]) -> str:
    for key in ("observationKey", "x", "TIME_PERIOD", "TIME"):
        value = _as_text(row.get(key))
        if value:
            return value
    return ""


def _analysis_table_for_artifact(mcp_tools: Any, artifact_id: str, row_limit: int = 5000) -> Dict[str, Any]:
    clean_artifact_id = _as_text(artifact_id)
    if not clean_artifact_id:
        return {}
    payload = mcp_tools._load_artifact_payload(clean_artifact_id)
    kind = mcp_tools._artifact_kind(clean_artifact_id, payload)
    if kind.startswith("domestic"):
        headers, rows = mcp_tools._flatten_domestic_payload(payload)
    elif kind.startswith("macro"):
        headers, rows = mcp_tools._flatten_macro_payload(payload)
    else:
        return {"artifact_id": clean_artifact_id, "kind": kind, "available": False}
    returned_rows = rows[: max(0, int(row_limit))]
    returned_dicts = _row_dicts(headers, returned_rows)
    all_dicts = _row_dicts(headers, rows)
    latest_rows = sorted(all_dicts, key=_period_sort_key)[-20:]
    return {
        "artifact_id": clean_artifact_id,
        "kind": kind,
        "headers": headers,
        "row_count": len(rows),
        "rows_returned": len(returned_rows),
        "rows_truncated": len(rows) > len(returned_rows),
        "rows": returned_dicts,
        "latest_rows": latest_rows,
        "payload_manifest": mcp_tools._summary(payload),
        "instruction": (
            "Rows come from the final replay artifact. If rows_truncated is true, use artifact_id or "
            "analysis_file for full-series calculation rather than expanding the whole table into chat context."
        ),
    }


def _normalize_replay_steps(retrieval_logic: Dict[str, Any]) -> list[Dict[str, Any]]:
    api_calls = retrieval_logic.get("api_calls")
    if isinstance(api_calls, list):
        steps = []
        for index, api_call in enumerate(api_calls, start=1):
            if isinstance(api_call, dict):
                steps.append(
                    {
                        "id": _as_text(api_call.get("id")) or f"api_call_{index}",
                        "tool": _as_text(api_call.get("tool")) or "retrieve",
                        "args": api_call.get("args") if isinstance(api_call.get("args"), dict) else {},
                    }
                )
        for index, narrow_step in enumerate(retrieval_logic.get("narrow_steps") or [], start=1):
            if isinstance(narrow_step, dict):
                args = dict(narrow_step.get("args") if isinstance(narrow_step.get("args"), dict) else {})
                args.setdefault("artifactId", "${latest_artifact_id}")
                steps.append(
                    {
                        "id": _as_text(narrow_step.get("id")) or f"narrow_{index}",
                        "tool": "narrow_artifact",
                        "args": args,
                    }
                )
        return steps

    api_call = retrieval_logic.get("api_call")
    if isinstance(api_call, dict):
        steps = [
            {
                "id": _as_text(api_call.get("id")) or "api_call",
                "tool": _as_text(api_call.get("tool")) or "retrieve",
                "args": api_call.get("args") if isinstance(api_call.get("args"), dict) else {},
            }
        ]
        narrow = retrieval_logic.get("narrow")
        if isinstance(narrow, dict):
            args = dict(narrow.get("args") if isinstance(narrow.get("args"), dict) else {})
            args.setdefault("artifactId", "${latest_artifact_id}")
            steps.append(
                {
                    "id": _as_text(narrow.get("id")) or "narrow",
                    "tool": "narrow_artifact",
                    "args": args,
                }
            )
        return steps

    steps = retrieval_logic.get("steps")
    if isinstance(steps, list):
        return [step for step in steps if isinstance(step, dict)]
    tool = _as_text(retrieval_logic.get("tool"))
    args = retrieval_logic.get("args")
    if tool and isinstance(args, dict):
        return [{"id": tool, "tool": tool, "args": args}]
    return []


def _validate_validated_recipe(
    *,
    retrieval_logic: Dict[str, Any],
    transformation_logic: Dict[str, Any],
) -> None:
    steps = _normalize_replay_steps(retrieval_logic)
    if not steps:
        raise RuntimeError(
            "Validated variables must save the approved API/MCP call as retrieval_logic.api_call "
            "or retrieval_logic.steps."
        )
    if _as_text(steps[0].get("tool")) != "retrieve":
        raise RuntimeError("A validated-variable recipe must start with the approved retrieve API/MCP call.")
    if not any(_as_text(step.get("tool")) == "narrow_artifact" for step in steps[1:]):
        raise RuntimeError(
            "A validated-variable recipe must include a narrow_artifact step after retrieve. "
            "Raw retrieve-only recipes are inspect-only and cannot be saved as validated variables."
        )
    for index, step in enumerate(steps, start=1):
        tool = _as_text(step.get("tool"))
        if tool not in ALLOWED_MCP_REPLAY_TOOLS:
            raise RuntimeError(
                f"Validated-variable recipe step {index} uses '{tool}'. "
                "Only the approved retrieve API/MCP call and required narrow_artifact call are allowed."
            )
        if not isinstance(step.get("args"), dict) or not step.get("args"):
            raise RuntimeError(f"Validated-variable recipe step {index} must include concrete saved args.")
    if not isinstance(transformation_logic, dict) or not any(key in transformation_logic for key in TRANSFORMATION_KEYS):
        raise RuntimeError(
            "Validated variables must include transformation_logic with transform_code, code, formula, or steps. "
            "For a direct series, save an identity transform such as transform_code='return the narrowed series unchanged'."
        )


def save_validated_variable_record(
    *,
    user_id: str,
    project_id: str,
    name: str,
    label: str = "",
    source_name: str = "",
    provider_id: str = "",
    dataset_id: str = "",
    metric: str = "",
    unit: str = "",
    geography: str = "",
    frequency: str = "",
    seasonal_treatment: str = "",
    period_start: str = "",
    period_end: str = "",
    validated_api_url: str = "",
    retrieval_logic: Dict[str, Any] | None = None,
    transformation_logic: Dict[str, Any] | None = None,
    transform_summary: str = "",
    recreation_summary: str = "",
    evidence_artifact: Dict[str, Any] | None = None,
    external_key: str = "",
) -> Dict[str, Any]:
    user_id = _as_text(user_id)
    project_id = _as_text(project_id)
    if not user_id or not project_id:
        raise RuntimeError("A Supabase user_id and active project_id are required to save a validated variable.")

    retrieval_logic = retrieval_logic or {}
    transformation_logic = transformation_logic or {}
    evidence_artifact = evidence_artifact or {}
    validated_api_url = _as_text(validated_api_url) or _extract_validated_api_url(
        retrieval_logic,
        evidence_artifact,
    )
    if not validated_api_url:
        raise RuntimeError(
            "Validated variables must save the working validated_api_url from the API/MCP call that passed validation."
        )
    _validate_validated_recipe(
        retrieval_logic=retrieval_logic,
        transformation_logic=transformation_logic,
    )

    payload_for_key = {
        "name": name,
        "provider_id": provider_id,
        "dataset_id": dataset_id,
        "metric": metric,
        "unit": unit,
        "geography": geography,
        "frequency": frequency,
        "seasonal_treatment": seasonal_treatment,
        "validated_api_url": validated_api_url,
        "retrieval_logic": retrieval_logic,
        "transformation_logic": transformation_logic,
    }
    resolved_key = _as_text(external_key) or _external_key(payload_for_key)

    with _connect() as conn:
        variable = conn.execute(
            """
            insert into public.validated_variables (
              user_id, project_id, origin_project_id, external_key, name, label,
              source_name, provider_id, dataset_id, metric, unit, geography,
              frequency, seasonal_treatment, period_start, period_end,
              validation_status, validated_api_url, retrieval_logic, transformation_logic,
              transform_summary, recreation_summary, evidence_artifact, approved_by, approved_at
            )
            values (
              %(user_id)s, %(project_id)s, %(project_id)s, %(external_key)s, %(name)s, %(label)s,
              %(source_name)s, %(provider_id)s, %(dataset_id)s, %(metric)s, %(unit)s, %(geography)s,
              %(frequency)s, %(seasonal_treatment)s, %(period_start)s, %(period_end)s,
              'validated', %(validated_api_url)s, %(retrieval_logic)s, %(transformation_logic)s,
              %(transform_summary)s, %(recreation_summary)s, %(evidence_artifact)s, %(user_id)s, now()
            )
            on conflict (user_id, external_key)
            do update set
              name = excluded.name,
              label = excluded.label,
              source_name = excluded.source_name,
              provider_id = excluded.provider_id,
              dataset_id = excluded.dataset_id,
              metric = excluded.metric,
              unit = excluded.unit,
              geography = excluded.geography,
              frequency = excluded.frequency,
              seasonal_treatment = excluded.seasonal_treatment,
              period_start = excluded.period_start,
              period_end = excluded.period_end,
              validation_status = 'validated',
              validated_api_url = excluded.validated_api_url,
              retrieval_logic = excluded.retrieval_logic,
              transformation_logic = excluded.transformation_logic,
              transform_summary = excluded.transform_summary,
              recreation_summary = excluded.recreation_summary,
              evidence_artifact = excluded.evidence_artifact,
              approved_by = excluded.approved_by,
              approved_at = now(),
              updated_at = now()
            returning id, external_key, name, label, source_name, metric, unit, validated_api_url, transform_summary, recreation_summary, validation_status
            """,
            {
                "user_id": user_id,
                "project_id": project_id,
                "external_key": resolved_key,
                "name": _as_text(name) or _as_text(label) or "Validated variable",
                "label": _as_text(label) or _as_text(name) or "Validated variable",
                "source_name": _as_text(source_name),
                "provider_id": _as_text(provider_id),
                "dataset_id": _as_text(dataset_id),
                "metric": _as_text(metric),
                "unit": _as_text(unit),
                "geography": _as_text(geography),
                "frequency": _as_text(frequency),
                "seasonal_treatment": _as_text(seasonal_treatment),
                "period_start": _as_text(period_start),
                "period_end": _as_text(period_end),
                "validated_api_url": validated_api_url,
                "retrieval_logic": Json(_jsonable(retrieval_logic)),
                "transformation_logic": Json(_jsonable(transformation_logic)),
                "transform_summary": _as_text(transform_summary),
                "recreation_summary": _as_text(recreation_summary),
                "evidence_artifact": Json(_jsonable(evidence_artifact)),
            },
        ).fetchone()
        conn.execute(
            """
            update public.modelling_projects
            set active_validated_variable_ids =
              case
                when active_validated_variable_ids ? %(variable_id)s then active_validated_variable_ids
                else active_validated_variable_ids || to_jsonb(array[%(variable_id)s]::text[])
              end,
              updated_at = now()
            where id = %(project_id)s and user_id = %(user_id)s
            """,
            {
                "user_id": user_id,
                "project_id": project_id,
                "variable_id": str(variable["id"]),
            },
        )
        conn.commit()

    return {
        "saved": True,
        "variable": dict(variable),
        "active_in_project": True,
        "instruction": (
            "The variable is now in the reusable validated-variable library and linked as active "
            "for the current project/model."
        ),
    }


def run_validated_variable_record(
    *,
    user_id: str,
    project_id: str,
    conversation_id: str = "",
    code_container_id: str = "",
    variable_id: str = "",
    external_key: str = "",
    name: str = "",
) -> Dict[str, Any]:
    user_id = _as_text(user_id)
    project_id = _as_text(project_id)
    variable_id = _as_text(variable_id)
    external_key = _as_text(external_key)
    name = _as_text(name)
    if not user_id:
        raise RuntimeError("A Supabase user_id is required to run a validated variable.")

    with _connect() as conn:
        if variable_id:
            row = conn.execute(
                """
                select vv.*
                from public.validated_variables vv
                left join public.modelling_projects mp
                  on mp.id = %s and mp.user_id = %s
                where vv.user_id = %s and vv.id = %s
                  and (%s = '' or mp.active_validated_variable_ids ? vv.id::text)
                limit 1
                """,
                (project_id, user_id, user_id, variable_id, project_id),
            ).fetchone()
        elif external_key:
            row = conn.execute(
                """
                select vv.*
                from public.validated_variables vv
                left join public.modelling_projects mp
                  on mp.id = %s and mp.user_id = %s
                where vv.user_id = %s and vv.external_key = %s
                  and (%s = '' or mp.active_validated_variable_ids ? vv.id::text)
                limit 1
                """,
                (project_id, user_id, user_id, external_key, project_id),
            ).fetchone()
        else:
            row = conn.execute(
                """
                select vv.*
                from public.validated_variables vv
                left join public.modelling_projects mp
                  on mp.id = %s and mp.user_id = %s
                where vv.user_id = %s
                  and (%s = '' or mp.active_validated_variable_ids ? vv.id::text)
                  and lower(vv.name) = lower(%s)
                order by vv.updated_at desc
                limit 1
                """,
                (project_id, user_id, user_id, project_id, name),
            ).fetchone()

    if not row:
        raise RuntimeError("No matching active validated variable was found.")

    retrieval_logic = row.get("retrieval_logic") if isinstance(row.get("retrieval_logic"), dict) else {}
    transformation_logic = row.get("transformation_logic") if isinstance(row.get("transformation_logic"), dict) else {}
    steps = _normalize_replay_steps(retrieval_logic)
    if not steps:
        raise RuntimeError("This validated variable does not contain executable retrieval_logic.steps.")
    if not any(_as_text(step.get("tool")) == "narrow_artifact" for step in steps[1:]):
        raise RuntimeError(
            "This validated variable was saved with a raw retrieve-only recipe. "
            "Revalidate it through MCP so the recipe includes strict narrow_artifact evidence before replay."
        )

    outputs: Dict[str, Any] = {}
    replayed_steps = []
    latest_analysis_table: Dict[str, Any] = {}
    with _MCP_REPLAY_LOCK:
        with _mcp_replay_context(conversation_id=conversation_id, code_container_id=code_container_id) as mcp_tools:
            for index, step in enumerate(steps, start=1):
                tool = _as_text(step.get("tool"))
                if tool not in ALLOWED_MCP_REPLAY_TOOLS:
                    raise RuntimeError(f"Saved step {index} uses unsupported tool '{tool}'.")
                step_id = _as_text(step.get("id")) or f"step_{index}"
                raw_args = step.get("args") if isinstance(step.get("args"), dict) else {}
                args = _interpolate(raw_args, outputs)
                result = _replay_mcp_tool(mcp_tools, tool, args)
                artifact_id = _extract_artifact_id(result)
                result_kind = _as_text(result.get("kind") if isinstance(result, dict) else "")
                is_narrowed_result = "narrowed" in result_kind or tool == "narrow_artifact"
                analysis_table = (
                    _analysis_table_for_artifact(mcp_tools, artifact_id)
                    if artifact_id and is_narrowed_result
                    else {}
                )
                if analysis_table and isinstance(result, dict):
                    for key in (
                        "analysis_container_id",
                        "analysis_file_id",
                        "analysis_filename",
                        "analysis_file",
                        "download_filename",
                    ):
                        if result.get(key):
                            analysis_table[key] = result.get(key)
                if artifact_id:
                    outputs["_latest_artifact_id"] = artifact_id
                if analysis_table:
                    latest_analysis_table = analysis_table
                outputs[step_id] = result
                summary = _summarize_replay_output(result)
                if analysis_table:
                    summary["analysis_table_summary"] = {
                        "artifact_id": analysis_table.get("artifact_id"),
                        "kind": analysis_table.get("kind"),
                        "row_count": analysis_table.get("row_count"),
                        "rows_returned": analysis_table.get("rows_returned"),
                        "rows_truncated": analysis_table.get("rows_truncated"),
                    }
                replayed_steps.append(
                    {
                        "id": step_id,
                        "tool": tool,
                        "args": args,
                        "artifact_id": artifact_id,
                        "summary": summary,
                    }
                )

    return {
        "variable": {
            "id": str(row.get("id")),
            "external_key": row.get("external_key"),
            "name": row.get("name"),
            "label": row.get("label"),
            "source_name": row.get("source_name"),
            "metric": row.get("metric"),
            "unit": row.get("unit"),
            "geography": row.get("geography"),
            "frequency": row.get("frequency"),
            "seasonal_treatment": row.get("seasonal_treatment"),
            "period_start": row.get("period_start"),
            "period_end": row.get("period_end"),
            "validated_api_url": row.get("validated_api_url"),
        },
        "replayed_steps": replayed_steps,
        "latest_artifact_id": outputs.get("_latest_artifact_id") or "",
        "latest_analysis_table": latest_analysis_table,
        "validated_api_url": row.get("validated_api_url") or "",
        "transformation_logic": transformation_logic,
        "transform_summary": row.get("transform_summary") or "",
        "recreation_summary": row.get("recreation_summary") or "",
        "instruction": (
            "Use latest_analysis_table rows directly, or inspect latest_artifact_id with MCP if more detail is needed. "
            "Apply transformation_logic to these replayed rows if a final derived series/value is needed. "
            "This replay is only for the exact approved variable named above. Do not change its series, filters, "
            "source URL, or transformation to answer a different request; use normal MCP retrieval for a different series."
        ),
    }
