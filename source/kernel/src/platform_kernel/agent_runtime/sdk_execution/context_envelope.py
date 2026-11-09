"""Body-free manifests for model context envelopes."""

from __future__ import annotations

import hashlib
import json

from agents import (
    AgentOutputSchema,
)

from ..memory import token_count


def _envelope_manifest(
    *, purpose: str, policy_version: str, available: int, estimated: int,
    system_prompt: str | None, input_items: list, agent,
) -> dict[str, object]:
    envelope = {
        "instructions": system_prompt,
        "tools": [
            {
                "name": tool.name,
                "description": tool.description,
                "input_schema": tool.params_json_schema,
                "output_schema": tool.output_json_schema,
            }
            for tool in agent.tools
        ],
        "input": input_items,
    }
    encoded = json.dumps(
        envelope, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        allow_nan=False, default=str,
    ).encode("utf-8")
    return {
        "schemaVersion": "context-manifest.v1",
        "purpose": purpose,
        "policy_version": policy_version,
        "tokenizer": "o200k_base",
        "available_input_tokens": available,
        "estimated_input_tokens": estimated,
        "blocks": [{
            "block_id": "provider:envelope",
            "kind": "COMPACTION_INPUT" if purpose == "COMPACTION" else "RUN_STATE_ENVELOPE",
            "authority": (
                "NON_AUTHORITATIVE_SUMMARY" if purpose == "COMPACTION" else "KERNEL_POLICY"
            ),
            "retention": "PINNED",
            "source_type": "MODEL_ENVELOPE",
            "source_id": None,
            "source_revision": None,
            "content_hash": "sha256:" + hashlib.sha256(encoded).hexdigest(),
            "token_count": estimated,
            "disposition": "INCLUDED",
            "reason": None,
        }],
    }


def _envelope_blocks(agent, system_prompt, usage: dict) -> list[dict[str, object]]:
    values = (
        ("provider:instructions", "STATIC_INSTRUCTIONS", system_prompt or "", usage["breakdown"]["instructions"]),
        (
            "provider:tools", "TOOL_SCHEMAS",
            [{
                "name": tool.name,
                "description": tool.description,
                "input_schema": tool.params_json_schema,
                "output_schema": tool.output_json_schema,
            } for tool in agent.tools],
            usage["breakdown"]["tools"],
        ),
        (
            "provider:output-schema", "OUTPUT_SCHEMA",
            (
                AgentOutputSchema(agent.output_type).json_schema()
                if agent.output_type is not None else None
            ),
            token_count(
                AgentOutputSchema(agent.output_type).json_schema()
                if agent.output_type is not None else None
            ),
        ),
    )
    result = []
    for block_id, kind, value, tokens in values:
        encoded = json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
            allow_nan=False, default=str,
        ).encode("utf-8")
        result.append({
            "block_id": block_id,
            "kind": kind,
            "authority": "KERNEL_POLICY",
            "retention": "PINNED",
            "source_type": "STATIC_ASSEMBLY",
            "source_id": None,
            "source_revision": None,
            "content_hash": "sha256:" + hashlib.sha256(encoded).hexdigest(),
            "token_count": tokens,
            "disposition": "INCLUDED",
            "reason": None,
        })
    return result


def _state_envelope_block(input_items: list) -> dict[str, object]:
    encoded = json.dumps(
        input_items, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        allow_nan=False, default=str,
    ).encode("utf-8")
    return {
        "block_id": "provider:run-state",
        "kind": "RUN_STATE_ENVELOPE",
        "authority": "KERNEL_FACT",
        "retention": "PINNED",
        "source_type": "AGENT_RUN_STATE",
        "source_id": None,
        "source_revision": None,
        "content_hash": "sha256:" + hashlib.sha256(encoded).hexdigest(),
        "token_count": token_count(input_items),
        "disposition": "INCLUDED",
        "reason": None,
    }
