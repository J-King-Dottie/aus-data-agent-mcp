from __future__ import annotations

from datetime import datetime, timezone
import json
import logging
import re
from typing import Any, Dict, List, Optional

import httpx
import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from .config import get_settings


logger = logging.getLogger("abs.backend.project_memory")

PROJECT_COMPACT_MEMORY_CHAR_LIMIT = 4000
PROJECT_MEMORY_SEARCH_LIMIT = 30
PROJECT_CHAT_HISTORY_RUN_LIMIT = 50
PROJECT_MEMORY_COMPACT_EVERY_PAIRS = 5


def _as_text(value: Any) -> str:
    return str(value or "").strip()


def _as_int(value: Any) -> int:
    try:
        numeric = int(value)
    except (TypeError, ValueError):
        return 0
    return numeric if numeric > 0 else 0


def _as_float(value: Any) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return 0.0
    return numeric if numeric > 0 else 0.0


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _db_url() -> str:
    return _as_text(get_settings().database_url)


def _connect():
    database_url = _db_url()
    if not database_url:
        raise RuntimeError("DATABASE_URL is not configured.")
    return psycopg.connect(database_url, connect_timeout=20, row_factory=dict_row)


def _extract_response_text(response_data: Dict[str, Any]) -> str:
    output = response_data.get("output")
    if not isinstance(output, list):
        return ""
    parts: List[str] = []
    for item in output:
        if not isinstance(item, dict):
            continue
        content = item.get("content")
        if not isinstance(content, list):
            continue
        for entry in content:
            if not isinstance(entry, dict):
                continue
            text = entry.get("text")
            if isinstance(text, str):
                parts.append(text)
    return "\n".join(part for part in parts if part).strip()


def _parse_json_object(text: str) -> Dict[str, Any]:
    if not text:
        return {}
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, flags=re.S)
        if not match:
            return {}
        try:
            parsed = json.loads(match.group(0))
        except json.JSONDecodeError:
            return {}
    return parsed if isinstance(parsed, dict) else {}


def _chat_pairs(messages: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    pairs: List[Dict[str, str]] = []
    pending_user = ""
    for message in messages:
        if not isinstance(message, dict):
            continue
        role = _as_text(message.get("role")).lower()
        content = _as_text(message.get("content"))
        if not content or role == "progress":
            continue
        if role == "user":
            pending_user = content
            continue
        if role == "assistant" and pending_user:
            pairs.append(
                {
                    "user": pending_user[:3000],
                    "assistant": content[:3000],
                }
            )
            pending_user = ""
    return pairs


def fetch_project_compact_memory(*, user_id: str, project_id: str) -> Dict[str, Any]:
    if not _as_text(user_id) or not _as_text(project_id) or not _db_url():
        return {}
    try:
        with _connect() as conn:
            row = conn.execute(
                """
                select id, user_id, id as project_id, name as project_tag, memory_text,
                       last_compacted_conversation_id, last_compacted_message_count,
                       last_compacted_created_at, updated_at
                from public.modelling_projects
                where user_id = %s and id = %s
                limit 1
                """,
                (user_id, project_id),
            ).fetchone()
    except Exception as exc:
        logger.warning("project_memory_fetch_failed project_id=%s error=%s", project_id, exc)
        return {}
    return dict(row) if row else {}


def search_project_compact_memory(
    *,
    user_id: str,
    current_project_id: str,
    query: str,
    limit: int = 5,
) -> List[Dict[str, Any]]:
    if not _as_text(user_id) or not _db_url():
        return []
    try:
        with _connect() as conn:
            rows = conn.execute(
                """
                select id as project_id, name as project_tag, memory_text, updated_at
                from public.modelling_projects
                where user_id = %s
                  and id <> %s
                  and memory_text <> ''
                order by updated_at desc
                limit %s
                """,
                (user_id, current_project_id or "00000000-0000-0000-0000-000000000000", PROJECT_MEMORY_SEARCH_LIMIT),
            ).fetchall()
    except Exception as exc:
        logger.warning("project_memory_search_failed user_id=%s error=%s", user_id, exc)
        return []

    query_terms = [term for term in re.findall(r"[a-z0-9]+", _as_text(query).lower()) if len(term) >= 3]
    scored: List[tuple[int, Dict[str, Any]]] = []
    for row in rows:
        memory_text = _as_text(row.get("memory_text"))
        project_tag = _as_text(row.get("project_tag"))
        if not memory_text:
            continue
        haystack = f"{project_tag}\n{memory_text}".lower()
        score = sum(1 for term in query_terms if term in haystack)
        scored.append(
            (
                score,
                {
                    "project_id": _as_text(row.get("project_id")),
                    "project_name": project_tag,
                    "memory": re.sub(r"\s+", " ", memory_text).strip()[:900],
                    "updated_at": row.get("updated_at").isoformat() if hasattr(row.get("updated_at"), "isoformat") else row.get("updated_at"),
                },
            )
        )

    scored.sort(key=lambda item: item[0], reverse=True)
    max_results = max(1, min(int(limit or 5), 10))
    return [item for _, item in scored[:max_results]]


def fetch_project_chat_runs(
    *,
    user_id: str,
    project_id: str,
    limit: int = PROJECT_CHAT_HISTORY_RUN_LIMIT,
) -> List[Dict[str, Any]]:
    if not _as_text(user_id) or not _as_text(project_id) or not _db_url():
        return []
    safe_limit = max(1, min(int(limit or PROJECT_CHAT_HISTORY_RUN_LIMIT), 200))
    try:
        with _connect() as conn:
            rows = conn.execute(
                """
                select *
                from (
                    select id, user_message, progress_notes, final_response, run_cost, run_index, status,
                           conversation_id, created_at
                    from public.modelling_chat_messages
                    where user_id = %s
                      and project_id = %s
                    order by created_at desc, run_index desc
                    limit %s
                ) recent_runs
                order by created_at asc, run_index asc
                """,
                (user_id, project_id, safe_limit),
            ).fetchall()
    except Exception as exc:
        logger.warning("project_chat_history_fetch_failed project_id=%s error=%s", project_id, exc)
        return []
    return [dict(row) for row in rows if isinstance(row, dict)]


def chat_messages_from_runs(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    messages: List[Dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        user_message = _as_text(row.get("user_message"))
        final_response = _as_text(row.get("final_response"))
        if not user_message or not final_response:
            continue
        if user_message:
            messages.append({"role": "user", "content": user_message})
        progress_notes = row.get("progress_notes")
        if isinstance(progress_notes, list):
            for note in progress_notes:
                clean_note = _as_text(note)
                if clean_note:
                    messages.append({"role": "progress", "content": clean_note})
        assistant_message: Dict[str, Any] = {"role": "assistant", "content": final_response}
        run_cost = row.get("run_cost")
        if isinstance(run_cost, dict):
            assistant_message["run_cost"] = run_cost
        messages.append(assistant_message)
    return messages


def fetch_project_chat_messages(*, user_id: str, project_id: str) -> List[Dict[str, Any]]:
    return chat_messages_from_runs(
        fetch_project_chat_runs(user_id=user_id, project_id=project_id)
    )


def persist_project_chat_run(
    *,
    user_id: str,
    project_id: str,
    conversation_id: str,
    user_message: str,
    progress_notes: List[str],
    final_response: str,
    run_cost: Dict[str, Any] | None,
) -> bool:
    if (
        not _as_text(user_id)
        or not _as_text(project_id)
        or not _as_text(conversation_id)
        or not _as_text(user_message)
        or not _as_text(final_response)
        or not _db_url()
    ):
        return False
    try:
        with _connect() as conn:
            with conn.transaction():
                row = conn.execute(
                    """
                    select coalesce(max(run_index), -1) + 1 as next_run_index
                    from public.modelling_chat_messages
                    where user_id = %s
                      and project_id = %s
                      and conversation_id = %s
                    """,
                    (user_id, project_id, conversation_id),
                ).fetchone()
                run_index = int((row or {}).get("next_run_index") or 0)
                chat_row = conn.execute(
                    """
                    insert into public.modelling_chat_messages
                        (user_id, project_id, conversation_id, run_index, user_message,
                         progress_notes, final_response, final_response_format, run_cost, status)
                    values
                        (%s, %s, %s, %s, %s, %s, %s, 'markdown', %s, 'completed')
                    returning id
                    """,
                    (
                        user_id,
                        project_id,
                        conversation_id,
                        run_index,
                        user_message,
                        Jsonb([_as_text(note) for note in progress_notes if _as_text(note)]),
                        final_response,
                        Jsonb(run_cost) if isinstance(run_cost, dict) else None,
                    ),
                ).fetchone()
                chat_message_id = (chat_row or {}).get("id")
                if isinstance(run_cost, dict) and chat_message_id:
                    conn.execute(
                        """
                        insert into public.ai_usage
                            (user_id, project_id, chat_message_id, conversation_id, run_index, model,
                             input_tokens, cached_input_tokens, output_tokens,
                             ai_cost_usd, surcharge_usd, final_cost_usd, pricing, usage_payload)
                        values
                            (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        """,
                        (
                            user_id,
                            project_id,
                            chat_message_id,
                            conversation_id,
                            run_index,
                            _as_text(run_cost.get("model")),
                            _as_int(run_cost.get("input_tokens")),
                            _as_int(run_cost.get("cached_input_tokens")),
                            _as_int(run_cost.get("output_tokens")),
                            _as_float(run_cost.get("ai_cost_usd")),
                            _as_float(run_cost.get("surcharge_usd")),
                            _as_float(run_cost.get("final_cost_usd")),
                            Jsonb(run_cost.get("pricing") if isinstance(run_cost.get("pricing"), dict) else {}),
                            Jsonb(run_cost),
                        ),
                    )
    except Exception as exc:
        logger.warning("project_chat_history_persist_failed project_id=%s conversation_id=%s error=%s", project_id, conversation_id, exc)
        return False
    return True


def _compact_project_memory_with_model(
    *,
    previous_memory: str,
    project_name: str,
    chat_pairs: List[Dict[str, str]],
    model_name: str,
    openai_api_key: str,
) -> str:
    payload = {
        "project_name": project_name,
        "memory_char_limit": PROJECT_COMPACT_MEMORY_CHAR_LIMIT,
        "previous_memory": previous_memory,
        "new_chat_pairs": chat_pairs,
    }
    request_payload = {
        "model": model_name,
        "store": False,
        "reasoning": {"effort": "low"},
        "text": {"format": {"type": "json_object"}},
        "input": [
            {
                "role": "system",
                "content": (
                    "You update compact project memory for Nisaba. Return JSON only. "
                    "Rewrite one concise memory for this project under the character limit. "
                    "Do not rely on downstream truncation: the memory_text you return must be complete, coherent, "
                    "and already within the limit. If there is too much to keep, prioritize durable instructions, "
                    "decisions, open threads, useful project context, validated-variable intent, modelling judgement, "
                    "modelling choices, and unresolved tasks. Drop small talk, repeated wording, transient progress, "
                    "and anything not useful for future project chats."
                ),
            },
            {
                "role": "user",
                "content": (
                    "Update the compact project memory from this data. "
                    "Return {\"memory_text\":\"...\"}.\n\n"
                    + json.dumps(payload, ensure_ascii=False, indent=2)
                ),
            },
        ],
    }
    response = httpx.post(
        "https://api.openai.com/v1/responses",
        headers={
            "Authorization": f"Bearer {openai_api_key}",
            "Content-Type": "application/json",
        },
        json=request_payload,
        timeout=60,
    )
    response.raise_for_status()
    parsed = _parse_json_object(_extract_response_text(response.json()))
    memory_text = _as_text(parsed.get("memory_text")) or previous_memory
    return memory_text[:PROJECT_COMPACT_MEMORY_CHAR_LIMIT].strip()


def compact_project_memory_after_run(
    *,
    user_id: str,
    project_id: str,
    project_name: str,
    conversation_id: str,
    messages: List[Dict[str, Any]],
    model_name: str,
    openai_api_key: str,
) -> bool:
    if not _as_text(user_id) or not _as_text(project_id) or not _db_url():
        return False

    pairs = _chat_pairs(messages)
    if not pairs:
        return False

    previous = fetch_project_compact_memory(user_id=user_id, project_id=project_id)
    previous_count = int(previous.get("last_compacted_message_count") or 0)
    pair_count = len(pairs)
    has_previous_memory = bool(_as_text(previous.get("memory_text")))
    previous_bucket = previous_count // PROJECT_MEMORY_COMPACT_EVERY_PAIRS
    current_bucket = pair_count // PROJECT_MEMORY_COMPACT_EVERY_PAIRS
    if has_previous_memory and previous_count > 0 and current_bucket <= previous_bucket:
        return False

    recent_pairs = pairs[-12:]
    memory_text = _compact_project_memory_with_model(
        previous_memory=_as_text(previous.get("memory_text")),
        project_name=_as_text(project_name) or "Untitled model",
        chat_pairs=recent_pairs,
        model_name=model_name,
        openai_api_key=openai_api_key,
    )
    if not memory_text:
        return False

    with _connect() as conn:
        conn.execute(
            """
            update public.modelling_projects
            set memory_text = %s,
                last_compacted_conversation_id = '',
                last_compacted_message_count = %s,
                last_compacted_created_at = %s,
                updated_at = %s
            where user_id = %s and id = %s
            """,
            (
                memory_text,
                pair_count,
                _utc_now_iso(),
                _utc_now_iso(),
                user_id,
                project_id,
            ),
        )
        conn.commit()
    return True
