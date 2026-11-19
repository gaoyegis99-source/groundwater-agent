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
    "minItems": {"type": "integer", "minimum": 1}, "logicalType": TEXT,
    "rulesSchema": {"type": "string", "pattern": r"^[a-z][a-z0-9-]+\.v[1-9][0-9]*$"},
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
    object_schema({"name": NAME, "kind": {"const": "value"}, "schema": {"type": "object"},
                   "maxBytes": {"type": "integer", "minimum": 1}}, ["name", "kind", "schema"]),
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
    "implementationRevision": DIGEST, "composition": {"type": "object"},
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
    if not isinstance(operators, list) or not operators:
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


def _summary(operator: dict, collections: bool = False) -> dict:
    reasons = [
        {"code": "WORKFLOW_CARDINALITY_UNSUPPORTED", "port": port["name"]}
        for port in operator["inputs"] if port["cardinality"] != "one" and not collections
    ]
    return {
        "operator_id": operator["id"], "operator_version": operator["version"],
        "title": operator["title"], "description": operator["description"],
        "domain": operator["domain"], "tags": list(operator["tags"]),
        "composition_supported": not reasons, "unsupported_reasons": reasons,
    }


def _query_match(operator: dict, needle: str) -> tuple[int, str]:
    identity = operator["id"]
    if needle in (identity, f"{identity}@{operator['version']}"):
        return 10000, identity
    best = (1, "description") if needle in operator["description"].casefold() else (0, "")
    terms = (operator["title"], *operator["tags"], *identity.split("."))
    for term in terms:
        alias = term.casefold().removeprefix("capability:").strip()
        if alias == needle:
            return 1000, term
        score = 0
        if alias and needle in alias:
            score = 50 + len(needle)
        if len(alias) >= (2 if not alias.isascii() else 4) and alias in needle:
            score = max(score, 100 + len(alias))
        if score > best[0]:
            best = score, term
    return best


def list_operators(
    catalog: dict[tuple[str, str], dict], *, after: str | None = None,
    limit: int = 20, query: str | None = None, domain: str | None = None,
    capability: str | None = None,
    input_format: str | None = None, output_role: str | None = None,
    collection_operators: dict | None = None,
) -> dict:
    if type(limit) is not int or not 1 <= limit <= 50:
        raise WorkflowValidationError("OPERATOR_PAGE_LIMIT_INVALID")
    for field, value in (
        ("after", after), ("query", query), ("domain", domain),
        ("capability", capability), ("input_format", input_format), ("output_role", output_role),
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
    matches = []
    reasons = {}
    for key in ordered:
        operator = catalog[key]
        if domain is not None and operator["domain"] != domain:
            continue
        if capability_tag and capability_tag not in {tag.casefold() for tag in operator["tags"]}:
            continue
        if input_format is not None and not any(input_format in port["formats"] for port in operator["inputs"]):
            continue
        if output_role is not None:
            roles = {port["name"] for port in operator["outputs"]}
            roles.update(
                output.get("fixed", {}).get("valueKind")
                for output in operator.get("composition", {}).get("outputSemantics", {}).values()
            )
            if output_role not in roles:
                continue
        score, term = _query_match(operator, needle) if needle else (1, "")
        if score:
            matches.append((-score, key))
            reasons[key] = {
                name: value for name, value in (
                    ("query_term", term), ("capability", capability), ("domain", domain),
                    ("input_format", input_format), ("output_role", output_role),
                ) if value
            }
    matches.sort()
    keys = [key for _, key in matches]
    if after is not None:
        key = identities[after]
        if key not in keys:
            raise WorkflowValidationError("OPERATOR_CURSOR_INVALID")
        keys = keys[keys.index(key) + 1:]
    page = keys[:limit]
    return {
        "items": [
            {
                **_summary(catalog[key], key in (collection_operators or {})),
                "matched_by": reasons[key],
            }
            for key in page
        ],
        "next_cursor": f"{page[-1][0]}@{page[-1][1]}" if len(keys) > limit else None,
    }


def get_operator(catalog: dict[tuple[str, str], dict], operator_id: str, version: str, *, collections: bool = False) -> dict:
    validate(operator_id, OPERATOR_ID, "OPERATOR_ID_INVALID")
    validate(version, VERSION, "OPERATOR_VERSION_INVALID")
    operator = catalog.get((operator_id, version))
    if operator is None:
        raise WorkflowValidationError("OPERATOR_UNKNOWN")
    return {
        **_summary(operator, collections),
        "implementation_revision": operator["implementationRevision"],
        "runtime": operator["runtime"],
        "inputs": deepcopy(operator["inputs"]),
        "parametersSchema": deepcopy(operator["parametersSchema"]),
        "outputs": deepcopy(operator["outputs"]),
        "errorCodes": list(operator["errorCodes"]),
        "binding_rules": {
            "inputs": ["workflowInput", "nodeOutput", "collection"] if collections else ["workflowInput", "nodeOutput"],
            "parameters": ["workflowParameter"],
            "many_inputs_supported": collections, "value_outputs_feed_nodes": False,
            "value_outputs_can_be_final": True,
        },
    }
