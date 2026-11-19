"""Freeze the assembled follow-up alongside the source approval."""

from __future__ import annotations

import json
import sqlite3
from uuid import uuid4

def freeze_output_steps(
    connection: sqlite3.Connection,
    confirmation_id: str,
    actions,
    models,
    policies: tuple[dict, ...],
) -> None:
    by_source = {policy["sourceModel"]: policy for policy in policies}
    by_model = {model.model_id: model for model in models}
    for action in actions:
        policy = by_source.get(action.model_id)
        if policy is None:
            continue
        target = by_model[policy["targetModel"]]
        plan = {
            "output": policy["output"],
            "input": policy["targetInput"],
            "tool": target.tool_name,
            "model_id": target.model_id,
            "model_version": target.model_version,
            "model_checksum": target.model_checksum,
            "workflow_id": target.workflow_id,
            "workflow_version": target.workflow_version,
            "workflow_checksum": target.workflow_checksum,
            "input_schema_checksum": target.input_schema_checksum,
            "arguments": {**target.default_parameters, **policy["arguments"]},
            "outputs": list(target.final_outputs),
        }
        connection.execute(
            "INSERT INTO published_output_steps VALUES (?, ?, ?, ?)",
            (confirmation_id, action.tool_call_id, str(uuid4()), json.dumps(plan, sort_keys=True)),
        )


def copy_output_steps(connection, source_id: str, target_id: str) -> None:
    connection.execute(
        """INSERT INTO published_output_steps
           SELECT ?, source_tool_call_id, tool_call_id, plan_json
           FROM published_output_steps p
           WHERE p.confirmation_id=? AND EXISTS (
               SELECT 1 FROM confirmation_actions a
               WHERE a.confirmation_id=? AND a.tool_call_id=p.source_tool_call_id
           )""",
        (target_id, source_id, target_id),
    )
