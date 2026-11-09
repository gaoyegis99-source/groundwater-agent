from __future__ import annotations

import json
from pathlib import Path
import re
from uuid import uuid4

from ...database import connect_database, utc_now


_PURPOSES = {"INTENT_ROUTING", "AGENT_INITIAL", "AGENT_RESUME", "COMPACTION"}
_STATUSES = {"COMPILED", "DISPATCHED", "COMPLETE", "FAILED", "REJECTED"}
_TERMINAL = {"COMPLETE", "FAILED", "REJECTED"}
_MANIFEST_KEYS = {
    "schemaVersion", "purpose", "policy_version", "tokenizer",
    "available_input_tokens", "estimated_input_tokens", "blocks",
}
_BLOCK_KEYS = {
    "block_id", "kind", "authority", "retention", "source_type",
    "source_id", "source_revision", "content_hash", "token_count",
    "disposition", "reason",
}
_KINDS = {
    "CURRENT_MESSAGE", "ROUTE_CANDIDATES", "TASK_ACTIVE",
    "TASK_PREPARATION",
    "DIALOGUE_SUMMARY", "DIALOGUE_TURN", "SOURCE_WATERMARK",
    "EXECUTION_ACTIVE", "EXECUTION_HISTORY", "EXECUTION_INDEX",
    "STATIC_INSTRUCTIONS", "TOOL_SCHEMAS", "OUTPUT_SCHEMA",
    "COMPACTION_INPUT", "RUN_STATE_ENVELOPE",
}
_SOURCE_TYPES = {
    "KERNEL", "TASK", "MEMORY", "MESSAGE_REQUEST", "TURN",
    "TASK_PREPARATION",
    "CONTEXT_SNAPSHOT", "WORKFLOW_RUN", "EXECUTION_INDEX",
    "MODEL_ENVELOPE", "STATIC_ASSEMBLY", "AGENT_RUN_STATE",
}
_REASONS = {
    None, "ROUTE_BUDGET", "MEMORY_COMPACTION", "MEMORY_WATERMARK",
    "HISTORICAL_SUCCESS",
}
_SAFE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,199}")


def create_context_view(
    database: Path,
    *,
    request_agent_run_id: str,
    segment_id: str,
    request_ordinal: int,
    purpose: str,
    manifest: dict[str, object],
    status: str = "DISPATCHED",
    error_code: str | None = None,
) -> str | None:
    """Persist a body-free context manifest before a provider request.

    Standalone AgentRuntime unit entry points can have no durable message request.
    Such calls have no valid manifest owner and therefore return ``None``. Normal
    Interaction requests always have the required message_requests row.
    """
    _validate_manifest(manifest, purpose)
    if status not in _STATUSES or status in {"COMPLETE", "FAILED"}:
        raise ValueError("CONTEXT_VIEW_STATUS_INVALID")
    if type(request_ordinal) is not int or request_ordinal < 1:
        raise ValueError("CONTEXT_VIEW_ORDINAL_INVALID")
    _validate_error(error_code)
    context_view_id = str(uuid4())
    now = utc_now()
    with connect_database(database) as connection:
        owner = connection.execute(
            "SELECT 1 FROM message_requests WHERE agent_run_id=?",
            (request_agent_run_id,),
        ).fetchone()
        if owner is None:
            return None
        connection.execute(
            """INSERT INTO context_views(
                   context_view_id,request_agent_run_id,segment_id,request_ordinal,
                   purpose,policy_version,status,tokenizer,available_input_tokens,
                   estimated_input_tokens,actual_input_tokens,manifest_json,error_code,
                   created_at,finished_at
               ) VALUES (?,?,?,?,?,?,?,?,?,?,NULL,?,?,?,?)""",
            (
                context_view_id, request_agent_run_id, segment_id, request_ordinal,
                purpose, manifest["policy_version"], status, manifest["tokenizer"],
                manifest["available_input_tokens"], manifest["estimated_input_tokens"],
                json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
                error_code, now, now if status in _TERMINAL else None,
            ),
        )
    return context_view_id


def finish_context_view(
    database: Path,
    context_view_id: str | None,
    *,
    status: str,
    actual_input_tokens: int | None = None,
    error_code: str | None = None,
) -> None:
    if context_view_id is None:
        return
    if status not in _TERMINAL:
        raise ValueError("CONTEXT_VIEW_STATUS_INVALID")
    if actual_input_tokens is not None and (
        type(actual_input_tokens) is not int or actual_input_tokens < 0
    ):
        raise ValueError("CONTEXT_VIEW_USAGE_INVALID")
    _validate_error(error_code)
    with connect_database(database) as connection:
        updated = connection.execute(
            """UPDATE context_views
               SET status=?,actual_input_tokens=?,error_code=?,finished_at=?
               WHERE context_view_id=? AND status IN ('COMPILED','DISPATCHED')""",
            (status, actual_input_tokens, error_code, utc_now(), context_view_id),
        )
        if updated.rowcount != 1:
            row = connection.execute(
                "SELECT status,actual_input_tokens,error_code FROM context_views WHERE context_view_id=?",
                (context_view_id,),
            ).fetchone()
            if row is None or (
                row["status"], row["actual_input_tokens"], row["error_code"]
            ) != (status, actual_input_tokens, error_code):
                raise ValueError("CONTEXT_VIEW_TRANSITION_INVALID")


def list_context_views(database: Path, request_agent_run_id: str) -> tuple[dict, ...]:
    with connect_database(database) as connection:
        rows = connection.execute(
            "SELECT * FROM context_views WHERE request_agent_run_id=? "
            "ORDER BY created_at,segment_id,request_ordinal",
            (request_agent_run_id,),
        ).fetchall()
    return tuple({**dict(row), "manifest": json.loads(row["manifest_json"])} for row in rows)


def _validate_manifest(value: object, purpose: str) -> None:
    if purpose not in _PURPOSES or not isinstance(value, dict) or set(value) != _MANIFEST_KEYS:
        raise ValueError("CONTEXT_MANIFEST_INVALID")
    if (
        value.get("schemaVersion") != "context-manifest.v1"
        or value.get("purpose") != purpose
        or value.get("policy_version") != "context-policy.v1"
        or value.get("tokenizer") != "o200k_base"
    ):
        raise ValueError("CONTEXT_MANIFEST_INVALID")
    for name in ("available_input_tokens", "estimated_input_tokens"):
        if type(value.get(name)) is not int or value[name] < 0:
            raise ValueError("CONTEXT_MANIFEST_INVALID")
    blocks = value.get("blocks")
    if not isinstance(blocks, list):
        raise ValueError("CONTEXT_MANIFEST_INVALID")
    for block in blocks:
        if not isinstance(block, dict) or set(block) != _BLOCK_KEYS:
            raise ValueError("CONTEXT_MANIFEST_INVALID")
        if (
            re.fullmatch(_SAFE_ID, str(block["block_id"])) is None
            or block["kind"] not in _KINDS
            or block["authority"] not in {
                "KERNEL_POLICY", "USER_INPUT", "USER_REQUIREMENT",
                "KERNEL_FACT", "NON_AUTHORITATIVE_SUMMARY",
            }
            or block["retention"] not in {"PINNED", "BUDGETED", "RETRIEVABLE"}
            or block["source_type"] not in _SOURCE_TYPES
            or (
                block["source_id"] is not None
                and re.fullmatch(_SAFE_ID, str(block["source_id"])) is None
            )
            or (
                block["source_revision"] is not None
                and (
                    type(block["source_revision"]) is not int
                    or block["source_revision"] < 0
                )
            )
            or block["disposition"] not in {
                "INCLUDED", "COMPACTED", "RETRIEVABLE", "OMITTED", "WOULD_COMPACT",
            }
            or re.fullmatch(r"sha256:[0-9a-f]{64}", str(block["content_hash"])) is None
            or type(block["token_count"]) is not int
            or block["token_count"] < 0
            or block["reason"] not in _REASONS
        ):
            raise ValueError("CONTEXT_MANIFEST_INVALID")


def _validate_error(value: str | None) -> None:
    if value is not None and re.fullmatch(r"[A-Z][A-Z0-9_]*", value) is None:
        raise ValueError("CONTEXT_VIEW_ERROR_INVALID")
