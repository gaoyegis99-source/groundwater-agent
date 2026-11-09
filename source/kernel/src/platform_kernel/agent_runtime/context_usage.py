"""Local input-budget estimates; only numeric diagnostics leave this module."""
from __future__ import annotations

import json
from typing import Any

from agents import Agent, AgentOutputSchema

from .memory import token_count


def _payload(agent: Agent[Any], instructions: str | None, input_value: object) -> dict:
    return {
        "instructions": instructions,
        "tools": [{
            "name": tool.name, "description": tool.description,
            "input_schema": tool.params_json_schema, "output_schema": tool.output_json_schema,
        } for tool in agent.tools],
        "output_schema": (AgentOutputSchema(agent.output_type).json_schema()
                          if agent.output_type is not None else None),
        "input": input_value,
    }


def context_tokens(agent: Agent[Any], instructions: str | None, input_value: object) -> int:
    return token_count(_payload(agent, instructions, input_value))


def context_usage(agent: Agent[Any], instructions: str | None, inputs: list[dict],
                  *, available: int, observation_names: frozenset[str]) -> dict:
    payload = _payload(agent, instructions, inputs)
    total = token_count(payload)
    instruction_text = instructions or ""
    execution_text = ""
    before, marker, after = instruction_text.rpartition("\nExecutionContextV1:\n")
    if marker:
        _, end = json.JSONDecoder().raw_decode(after)
        execution_text = after[:end]
        instruction_text = before + marker + after[end:]
    calls = {item["call_id"]: item["name"] for item in inputs if item.get("type") == "function_call"}
    dialogue, execution, observations = [], [], []
    for item in inputs:
        if item.get("type") == "function_call_output":
            target = observations if calls[item["call_id"]] in observation_names | {"list_operators", "get_operator"} else execution
        elif item.get("type") == "function_call":
            target = execution
        else:
            target = dialogue
        target.append(item)
    breakdown = {
        "tools": token_count(payload["tools"]) if payload["tools"] else 0,
        "instructions": token_count(instruction_text),
        "dialogue": token_count(dialogue) if dialogue else 0,
        "execution_references": token_count(execution_text) + (token_count(execution) if execution else 0),
        "observations": token_count(observations) if observations else 0,
    }
    if payload["output_schema"] is not None:
        breakdown["tools"] += token_count(payload["output_schema"])
    # Tokenization across JSON/string boundaries is not additive. Keep the
    # original complete-envelope guard authoritative and expose its adjustment.
    breakdown["serialization"] = total - sum(breakdown.values())
    return {
        "tokenizer": "o200k_base", "total_tokens": total,
        "available_input_tokens": available, "excess_tokens": max(0, total - available),
        "breakdown": breakdown,
    }
