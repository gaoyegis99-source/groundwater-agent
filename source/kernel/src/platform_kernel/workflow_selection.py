"""Persisted selections for a preset model in a conversation turn."""

import json
import math

from .contracts import DataVersionRefV2

from .errors import ConfirmationConflictError
from .settings_types import ExecutableModelDefinition


MODEL_FIELDS = (
    "model_id", "model_version", "model_checksum", "workflow_id", "workflow_version", "workflow_checksum",
)
REFERENCE_FIELDS = ("dataset_id", "version_id", "semantic_id", "object_id", "checksum", "format", "media_type", "size_bytes")


def selected_model(
    selection: dict, models: tuple[ExecutableModelDefinition, ...],
) -> ExecutableModelDefinition:
    for model in models:
        if all(selection[field] == getattr(model, field) for field in MODEL_FIELDS):
            return model
    raise ConfirmationConflictError("WORKFLOW_SELECTION_STALE")


def bind_workflow_selection(selection: dict, model: ExecutableModelDefinition, references: tuple) -> dict:
    bindings = selection["input_bindings"]
    parameters = selection["parameters"]
    properties = model.input_schema.get("properties", {})
    try:
        json.dumps(parameters, ensure_ascii=False, allow_nan=False).encode("utf-8")
    except (ValueError, UnicodeError) as error:
        raise ConfirmationConflictError("WORKFLOW_SELECTION_INPUT_INVALID") from error
    if (
        set(bindings) - set(model.data_input_parameters)
        or set(parameters) - set(properties)
        or set(parameters) & set(model.data_input_parameters)
        or any(not 0 <= index < len(references) for index in bindings.values())
    ):
        raise ConfirmationConflictError("WORKFLOW_SELECTION_INPUT_INVALID")
    return {**selection, "inputs": {
        name: DataVersionRefV2.from_dict(references[index]).as_storage_dict()
        for name, index in bindings.items()
    }}


def _same_selected_value(expected: object, actual: object) -> bool:
    """Compare JSON values without treating booleans as numbers."""
    if isinstance(expected, (int, float)) and not isinstance(expected, bool):
        return (
            isinstance(actual, (int, float))
            and not isinstance(actual, bool)
            and expected == actual
            and (expected != 0 or math.copysign(1, expected) == math.copysign(1, actual))
        )
    if isinstance(expected, dict) and isinstance(actual, dict):
        return expected.keys() == actual.keys() and all(
            _same_selected_value(value, actual[key]) for key, value in expected.items()
        )
    if isinstance(expected, list) and isinstance(actual, list):
        return len(expected) == len(actual) and all(
            _same_selected_value(left, right) for left, right in zip(expected, actual, strict=True)
        )
    return type(expected) is type(actual) and expected == actual


def require_selected_arguments(selection: dict | None, arguments: dict, references: dict | None = None) -> None:
    if selection is None:
        return
    parameters = {**selection["parameters"], **{name: ref["version_id"] for name, ref in selection["inputs"].items()}}
    if any(
        name not in arguments or not _same_selected_value(value, arguments[name])
        for name, value in parameters.items()
    ):
        raise ValueError("WORKFLOW_SELECTION_ARGUMENTS_CHANGED")
    if references is not None:
        for name, expected in selection["inputs"].items():
            actual = references.get(name, {})
            if any(actual.get(field) != expected[field] for field in REFERENCE_FIELDS):
                raise ValueError("WORKFLOW_SELECTION_DATA_CHANGED")
