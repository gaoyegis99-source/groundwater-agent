from __future__ import annotations

import hashlib
import json
import math

from jsonschema import Draft202012Validator, validators
from jsonschema.exceptions import ValidationError


class WorkflowValidationError(ValueError):
    def __init__(self, code: str, path: str = "") -> None:
        super().__init__(code)
        self.code = code
        self.path = path


StrictValidator = validators.extend(
    Draft202012Validator,
    type_checker=Draft202012Validator.TYPE_CHECKER.redefine(
        "integer", lambda _checker, value: type(value) is int,
    ),
)


def pointer(parts) -> str:
    return "".join("/" + str(part).replace("~", "~0").replace("/", "~1") for part in parts)


def require_json(value: object, path: str = "") -> None:
    if value is None or type(value) in (bool, int):
        return
    if isinstance(value, str):
        try:
            value.encode("utf-8")
        except UnicodeEncodeError as cause:
            raise WorkflowValidationError("WORKFLOW_JSON_INVALID", path) from cause
        return
    if type(value) is float:
        if not math.isfinite(value):
            raise WorkflowValidationError("WORKFLOW_JSON_INVALID", path)
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            require_json(item, path + pointer([index]))
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise WorkflowValidationError("WORKFLOW_JSON_INVALID", path)
            require_json(key, path)
            require_json(item, path + pointer([key]))
        return
    raise WorkflowValidationError("WORKFLOW_JSON_INVALID", path)


def checksum(value: object) -> str:
    content = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(content).hexdigest()


def validate(value: object, schema: dict, code: str, path: str = "") -> None:
    try:
        StrictValidator(schema).validate(value)
    except ValidationError as cause:
        raise WorkflowValidationError(code, path + pointer(cause.absolute_path)) from cause


def require_string(value: object, error: str) -> str:
    if not isinstance(value, str) or not value:
        raise WorkflowValidationError(error)
    require_json(value)
    return value


def string_set(value: object, error: str) -> frozenset[str]:
    if (
        not isinstance(value, list)
        or any(not isinstance(item, str) or not item for item in value)
        or len(value) != len(set(value))
    ):
        raise WorkflowValidationError(error)
    return frozenset(value)


TEXT = {"type": "string", "minLength": 1}
NAME = {"type": "string", "pattern": r"^[A-Za-z_][A-Za-z0-9_.-]*$"}
OPERATOR_ID = {"type": "string", "pattern": r"^[a-z][a-z0-9_.-]*$"}
VERSION = {"type": "string", "pattern": r"^[0-9]+\.[0-9]+\.[0-9]+$"}
DIGEST = {"type": "string", "pattern": r"^sha256:[0-9a-f]{64}$"}
TEXT_LIST = {"type": "array", "items": TEXT, "uniqueItems": True}


def object_schema(properties: dict, required: list[str] | None = None) -> dict:
    return {
        "type": "object", "properties": properties,
        "required": list(properties) if required is None else required,
        "additionalProperties": False,
    }


NODE_OUTPUT = object_schema({"nodeId": OPERATOR_ID, "port": NAME})
SINGLE_INPUT_BINDING = {
    "oneOf": [
        object_schema({"workflowInput": NAME}),
        object_schema({"nodeOutput": NODE_OUTPUT}),
    ],
}
INPUT_BINDING = {
    "oneOf": [
        *SINGLE_INPUT_BINDING["oneOf"],
        object_schema({"collection": {
            "type": "array", "items": SINGLE_INPUT_BINDING, "minItems": 1,
        }}),
    ],
}
PARAMETER_BINDING = object_schema({"workflowParameter": NAME})
WORKFLOW_INPUT = object_schema({
    "name": NAME, "cardinality": {"enum": ["one", "many"]},
    "mediaTypes": {**TEXT_LIST, "minItems": 1},
    "formats": {**TEXT_LIST, "minItems": 1},
})
NODE = object_schema({
    "nodeId": OPERATOR_ID, "operatorId": OPERATOR_ID, "operatorVersion": VERSION,
    "inputs": {
        "type": "object", "propertyNames": NAME, "additionalProperties": INPUT_BINDING,
    },
    "parameters": {
        "type": "object", "propertyNames": NAME, "additionalProperties": PARAMETER_BINDING,
    },
})
GRAPH_PROPERTIES = {
    "inputs": {"type": "array", "items": WORKFLOW_INPUT},
    "parameters": {"type": "array", "items": NAME, "uniqueItems": True},
    "nodes": {"type": "array", "items": NODE, "minItems": 1},
    "outputs": {
        "type": "array", "minItems": 1,
        "items": object_schema({"name": NAME, "nodeOutput": NODE_OUTPUT}),
    },
}
GRAPH_SCHEMA = object_schema(GRAPH_PROPERTIES)
WORKFLOW_SCHEMA = object_schema({
    "schemaVersion": {"const": "workflow.v6"}, "workflowId": OPERATOR_ID, "version": VERSION,
    **GRAPH_PROPERTIES,
})


def validate_raw_workflow(raw: object) -> None:
    require_json(raw)
    if isinstance(raw, dict) and raw.get("schemaVersion") != "workflow.v6":
        raise WorkflowValidationError("WORKFLOW_DSL_VERSION_INVALID", "/schemaVersion")
    validate(raw, WORKFLOW_SCHEMA, "WORKFLOW_FIELDS_INVALID")
