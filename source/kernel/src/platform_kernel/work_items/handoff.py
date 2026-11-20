"""Project bounded, approved execution facts for the WorkItem handoff view."""

from __future__ import annotations

from collections.abc import Sequence
import json
from sqlite3 import Row

from platform_protocol_contracts import DataVersionRefV2, canonical_json

from ..settings import ExecutableModelDefinition


def _verified_input_keys(assets: Sequence[dict]) -> set[bytes]:
    return {
        canonical_json(reference["dataRef"])
        for asset in assets
        if (reference := asset["reference"])["kind"] in {"DATA", "RESULT"}
    }


def _inputs_are_current(row: Row, visible_inputs: set[bytes]) -> bool:
    try:
        refs = json.loads(row["input_references_json"])
        return all(
            canonical_json(DataVersionRefV2.from_storage_dict(ref).as_dict())
            in visible_inputs
            for ref in refs.values()
        )
    except (TypeError, ValueError, AttributeError):
        return False


def _parameters(row: Row, model: ExecutableModelDefinition) -> dict[str, str | bool | int | float]:
    properties = model.input_schema.get("properties", {})
    values = json.loads(row["arguments_json"])
    result: dict[str, str | bool | int | float] = {}
    for key, value in sorted(values.items()):
        definition = properties.get(key)
        if not isinstance(definition, dict) or key in model.data_input_parameters:
            continue
        if type(value) in {bool, int, float} or (
            isinstance(value, str) and len(value) <= 120
            and value in definition.get("enum", ())
        ):
            result[key] = value
    return result


def approved_actions(
    rows: Sequence[Row],
    assets: Sequence[dict],
    models: tuple[ExecutableModelDefinition, ...],
) -> list[dict]:
    installed = {
        (model.model_id, model.model_version, model.model_checksum, model.workflow_checksum): model
        for model in models
    }
    inputs = _verified_input_keys(assets)
    result = []
    for row in rows:
        model = installed.get((row["model_id"], row["model_version"], row["model_checksum"], row["workflow_checksum"]))
        if model is None or not _inputs_are_current(row, inputs):
            continue
        result.append({
            "session_id": row["session_id"],
            "session_title": row["session_title"],
            "event_id": row["event_id"],
            "approved_at": row["approved_at"],
            "goal_revision": row["goal_revision"],
            "decision_revision": row["decision_revision"],
            "position": row["position"],
            "model_id": model.model_id,
            "model_version": model.model_version,
            "label": model.tool_description[:200],
            "parameters": _parameters(row, model),
        })
    return result
