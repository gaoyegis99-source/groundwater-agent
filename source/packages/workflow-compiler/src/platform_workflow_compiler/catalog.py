from __future__ import annotations

from copy import deepcopy

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

from .formats import _conditional_output_pairs, _port_pairs
from .validation import (
    DIGEST,
    NAME,
    OPERATOR_ID,
    TEXT,
    TEXT_LIST,
    VERSION,
    WorkflowValidationError,
    object_schema,
    require_json,
    validate,
)


INPUT = object_schema({
    "name": NAME, "cardinality": {"enum": ["one", "many"]},
    "required": {"type": "boolean"},
    "mediaTypes": {**TEXT_LIST, "minItems": 1}, "formats": {**TEXT_LIST, "minItems": 1},
    "maxItems": {"type": "integer", "minimum": 1},
}, ["name", "cardinality", "required", "mediaTypes", "formats"])
FORMAT_CASE = object_schema({
    "inputPort": NAME, "inputFormats": {**TEXT_LIST, "minItems": 1},
    "mediaType": TEXT, "format": TEXT,
})
PARAMETER_FORMAT_CASE = object_schema({
    "parameter": NAME, "values": {**TEXT_LIST, "minItems": 1},
    "mediaType": TEXT, "format": TEXT,
})
OUTPUT = {"oneOf": [
    object_schema({
        "name": NAME, "kind": {"const": "artifact"},
        "mediaTypes": {**TEXT_LIST, "minItems": 1}, "formats": {**TEXT_LIST, "minItems": 1},
        "formatCases": {"type": "array", "minItems": 1, "items": FORMAT_CASE},
        "parameterFormatCases": {
            "type": "array", "minItems": 1, "items": PARAMETER_FORMAT_CASE,
        },
    }, ["name", "kind", "mediaTypes", "formats"]),
    object_schema({"name": NAME, "kind": {"const": "value"}, "schema": {"type": "object"}}),
]}
REMOTE_BINDING = object_schema({
    "serviceId": TEXT,
    "operationType": TEXT,
}, ["serviceId", "operationType"])
OPERATOR_SCHEMA = object_schema({
    "id": OPERATOR_ID, "version": VERSION, "title": TEXT, "description": TEXT, "domain": TEXT,
    "tags": {"type": "array", "items": TEXT, "minItems": 1},
    "runtime": {"enum": ["native", "qgis", "remote_http"]},
    "remoteBinding": REMOTE_BINDING,
    "timeoutSeconds": {"type": "integer", "minimum": 1, "maximum": 21600},
    "memoryLimitBytes": {"type": "integer", "minimum": 1},
    "inputs": {"type": "array", "items": INPUT},
    "parametersSchema": {"type": "object"},
    "outputs": {"type": "array", "items": OUTPUT, "minItems": 1},
    "errorCodes": TEXT_LIST,
    "examples": {"type": "array", "items": {"type": "object"}, "minItems": 1},
    "implementationRevision": DIGEST,
}, [
    "id", "version", "title", "description", "domain", "tags", "runtime",
    "timeoutSeconds", "memoryLimitBytes", "inputs", "parametersSchema",
    "outputs", "errorCodes", "examples", "implementationRevision",
])


def _validate_schema(schema: dict) -> None:
    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError as cause:
        raise WorkflowValidationError("OPERATOR_SCHEMA_INVALID") from cause
    pending = [schema]
    while pending:
        value = pending.pop()
        if isinstance(value, dict):
            if "$ref" in value or "$dynamicRef" in value:
                raise WorkflowValidationError("OPERATOR_SCHEMA_REFERENCE_UNSUPPORTED")
            pending.extend(value.values())
        elif isinstance(value, list):
            pending.extend(value)


def validate_catalog(operators: object, digest: str) -> dict[tuple[str, str], dict]:
    """Validate an Assembler-frozen catalog projection, without loading implementations.

    The deployment digest also includes source and static-mapping identities. It
    is not a hash of this projection; Assembler verifies those deployment files.
    """
    require_json(operators)
    validate(digest, DIGEST, "OPERATOR_CATALOG_DIGEST_INVALID")
    if not isinstance(operators, list) or len(operators) != 109:
        raise WorkflowValidationError("OPERATOR_CATALOG_SIZE_INVALID")
    result = {}
    for index, operator in enumerate(operators):
        validate(operator, OPERATOR_SCHEMA, "OPERATOR_MANIFEST_INVALID", f"/operators/{index}")
        if (operator["runtime"] == "remote_http") != ("remoteBinding" in operator):
            raise WorkflowValidationError("OPERATOR_REMOTE_BINDING_INVALID")
        identity = operator["id"], operator["version"]
        if identity in result:
            raise WorkflowValidationError("OPERATOR_IDENTITY_DUPLICATE")
        schema = operator["parametersSchema"]
        _validate_schema(schema)
        if (
            schema.get("type") != "object" or schema.get("additionalProperties") is not False
            or not isinstance(schema.get("properties"), dict)
            or not isinstance(schema.get("required", []), list)
            or not set(schema.get("required", [])) <= set(schema["properties"])
        ):
            raise WorkflowValidationError("OPERATOR_SCHEMA_INVALID")
        for ports in (operator["inputs"], operator["outputs"]):
            if len({item["name"] for item in ports}) != len(ports):
                raise WorkflowValidationError("OPERATOR_PORT_DUPLICATE")
        bound_inputs = {item["name"]: _port_pairs(item) for item in operator["inputs"]}
        for output in operator["outputs"]:
            if output["kind"] == "artifact":
                _conditional_output_pairs(output, operator, bound_inputs)
            else:
                _validate_schema(output["schema"])
        result[identity] = deepcopy(operator)
    return result


def _summary(operator: dict) -> dict:
    reasons = [
        {"code": "WORKFLOW_CARDINALITY_UNSUPPORTED", "port": port["name"]}
        for port in operator["inputs"] if port["cardinality"] != "one"
    ]
    return {
        "operator_id": operator["id"], "operator_version": operator["version"],
        "title": operator["title"], "description": operator["description"],
        "domain": operator["domain"], "tags": list(operator["tags"]),
        "composition_supported": not reasons, "unsupported_reasons": reasons,
    }


def _matches_query(operator: dict, operator_id: str, needle: str) -> bool:
    """Match both concise queries and exact catalog phrases inside natural text."""
    terms = [
        operator_id,
        operator["title"],
        operator["description"],
        *operator["tags"],
    ]
    folded = [" ".join(term.casefold().split()) for term in terms]
    if any(needle in term for term in folded):
        return True
    aliases = [
        term.removeprefix("capability:")
        for term in folded
        if len(term.removeprefix("capability:").replace(" ", "")) >= 4
    ]
    return any(alias in needle for alias in aliases)


def list_operators(
    catalog: dict[tuple[str, str], dict], *, after: str | None = None,
    limit: int = 20, query: str | None = None, domain: str | None = None,
    capability: str | None = None,
) -> dict:
    if type(limit) is not int or not 1 <= limit <= 50:
        raise WorkflowValidationError("OPERATOR_PAGE_LIMIT_INVALID")
    for field, value in (
        ("after", after), ("query", query), ("domain", domain),
        ("capability", capability),
    ):
        if value is not None:
            require_json(value)
            validate(value, TEXT, "OPERATOR_QUERY_INVALID", f"/{field}")
    ordered = sorted(catalog)
    identities = {f"{key[0]}@{key[1]}": key for key in ordered}
    if after is not None and after not in identities:
        raise WorkflowValidationError("OPERATOR_CURSOR_INVALID")
    needle = (
        " ".join(query.casefold().split()) if query is not None else None
    )
    capability_tag = (
        f"capability:{capability.casefold()}" if capability is not None else None
    )
    selected = [
        catalog[key] for key in ordered
        if (after is None or key > identities[after])
        and (domain is None or catalog[key]["domain"] == domain)
        and (
            capability_tag is None
            or capability_tag in {tag.casefold() for tag in catalog[key]["tags"]}
        )
        and (
            needle is None
            or _matches_query(catalog[key], key[0], needle)
        )
    ]
    page = selected[:limit]
    return {
        "items": [_summary(item) for item in page],
        "next_cursor": (
            f"{page[-1]['id']}@{page[-1]['version']}" if len(selected) > limit else None
        ),
    }


def get_operator(catalog: dict[tuple[str, str], dict], operator_id: str, version: str) -> dict:
    validate(operator_id, OPERATOR_ID, "OPERATOR_ID_INVALID")
    validate(version, VERSION, "OPERATOR_VERSION_INVALID")
    operator = catalog.get((operator_id, version))
    if operator is None:
        raise WorkflowValidationError("OPERATOR_UNKNOWN")
    return {
        **_summary(operator),
        "implementation_revision": operator["implementationRevision"],
        "runtime": operator["runtime"],
        "inputs": deepcopy(operator["inputs"]),
        "parametersSchema": deepcopy(operator["parametersSchema"]),
        "outputs": deepcopy(operator["outputs"]),
        "errorCodes": list(operator["errorCodes"]),
        "binding_rules": {
            "inputs": ["workflowInput", "nodeOutput"], "parameters": ["workflowParameter"],
            "many_inputs_supported": False, "value_outputs_feed_nodes": False,
            "value_outputs_can_be_final": True,
        },
    }
