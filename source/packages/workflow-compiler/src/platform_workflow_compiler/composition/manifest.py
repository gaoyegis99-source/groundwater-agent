"""Validate operator contracts before using them in a candidate graph."""

from __future__ import annotations

from ..formats import MEDIA_TYPE_FORMATS
from ..graph import _named_list
from ..validation import WorkflowValidationError, pointer
from .documents import definition_digest, validate_document, validate_parameter_schema
from .semantics import FACT_TYPES, validate_condition, validate_fact_value


SINGLE_ITEM = {"minItems": 1, "maxItems": 1, "ordered": True, "duplicates": False}
NO_PARAMETERS = {"type": "object", "additionalProperties": False}
USER_PARAMETER = {"allowedSources": ["USER"], "defaultPolicy": "none", "allowNodeOutput": False}


def format_pairs(port: dict) -> set[tuple[str, str]]:
    return {(pair["mediaType"], pair["format"]) for pair in port.get("formatPairs", [])}


def validate_port(port: dict, path: str) -> None:
    cardinality = port.get("cardinality", SINGLE_ITEM)
    if cardinality["minItems"] > cardinality["maxItems"]:
        raise WorkflowValidationError("COLLECTION_CARDINALITY_INVALID", path + "/cardinality")
    if port["kind"] == "value":
        schema = port["schema"]
        validate_parameter_schema(schema, path + "/schema")
        scalar = schema.get("items") if schema.get("type") == "array" else schema
        if (
            not isinstance(scalar, dict)
            or scalar.get("type") not in ("boolean", "string", "number", "integer")
            or (scalar["type"] == "string" and "maxLength" not in scalar)
            or (schema.get("type") == "array" and "maxItems" not in schema)
        ):
            raise WorkflowValidationError("COMPOSITION_VALUE_SCHEMA_UNBOUNDED", path + "/schema")
        return
    for index, pair in enumerate(port["formatPairs"]):
        if pair["format"] not in MEDIA_TYPE_FORMATS.get(pair["mediaType"], set()):
            raise WorkflowValidationError("WORKFLOW_PORT_FORMAT_INVALID", path + pointer(["formatPairs", index]))


def validate_requirements(requirements: dict, vocabulary: dict, path: str) -> None:
    for name, value in sorted(requirements.items()):
        validate_fact_value(name, value, vocabulary, path + pointer([name]))


def validate_operator_manifest(document: dict, vocabulary: dict) -> None:
    validate_document("operator-manifest.v2", document)
    if document["definitionDigest"] != definition_digest(document):
        raise WorkflowValidationError("COMPOSITION_DEFINITION_DIGEST_MISMATCH", "/definitionDigest")
    schema = document.get("parametersSchema", NO_PARAMETERS)
    parameter_schema = validate_parameter_schema(schema, "/parametersSchema")
    parameters = schema.get("properties", {})
    if (
        schema.get("type") != "object"
        or schema.get("additionalProperties") is not False
        or not set(schema.get("required", [])) <= parameters.keys()
    ):
        raise WorkflowValidationError("COMPOSITION_PARAMETER_SCHEMA_INVALID", "/parametersSchema")
    policies = document.get("parameterPolicies", {})
    if policies.keys() - parameters.keys():
        raise WorkflowValidationError("COMPOSITION_PARAMETER_UNKNOWN", "/parameterPolicies")
    for name, policy in policies.items():
        if policy.get("defaultPolicy", "none") == "declared" and (
            not isinstance(parameters[name], dict)
            or "default" not in parameters[name]
            or "OPERATOR_DEFAULT" not in policy["allowedSources"]
        ):
            raise WorkflowValidationError("COMPOSITION_PARAMETER_DEFAULT_INVALID", pointer(["parameterPolicies", name]))
    inputs = _named_list(document["inputs"], "name", "COMPOSITION_PORT_DUPLICATE", collection="inputs")
    outputs = _named_list(document["outputs"], "name", "COMPOSITION_PORT_DUPLICATE", collection="outputs")
    for group in ("inputs", "outputs"):
        for index, port in enumerate(document[group]):
            path = pointer([group, index])
            validate_port(port, path)
            if "selector" in port and (group != "inputs" or port["selector"]["parameter"] not in parameters):
                raise WorkflowValidationError("COMPOSITION_SELECTOR_INVALID", path + "/selector")
            if "formatCases" in port:
                if group != "outputs":
                    raise WorkflowValidationError("COMPOSITION_FORMAT_CASE_INVALID", path + "/formatCases")
                _validate_format_cases(port, parameters, parameter_schema, path)
    input_semantics = document.get("portSemantics", {})
    output_semantics = document.get("outputSemantics", {})
    if input_semantics.keys() - inputs.keys():
        raise WorkflowValidationError("COMPOSITION_PORT_UNKNOWN", "/portSemantics")
    if output_semantics.keys() - outputs.keys():
        raise WorkflowValidationError("COMPOSITION_PORT_UNKNOWN", "/outputSemantics")
    for name, semantics in input_semantics.items():
        path = pointer(["portSemantics", name])
        validate_requirements(semantics.get("requires", {}), vocabulary, path + "/requires")
        if set(semantics.get("requiredFacts", [])) - FACT_TYPES.keys():
            raise WorkflowValidationError("COMPOSITION_FACT_UNSUPPORTED", path + "/requiredFacts")
        for role, binding in semantics.get("fieldBindings", {}).items():
            if binding["parameter"] not in parameters or inputs[name]["logicalType"] not in {"table", "vector"}:
                raise WorkflowValidationError("FIELD_ROLE_UNBOUND", path + pointer(["fieldBindings", role]))
            validate_requirements(binding.get("requires", {}), vocabulary, path + pointer(["fieldBindings", role, "requires"]))
    for name, semantics in output_semantics.items():
        _validate_output_semantics(semantics, inputs, parameters, vocabulary, pointer(["outputSemantics", name]))
    constraints = document.get("constraints", [])
    _named_list(constraints, "id", "COMPOSITION_CHECK_DUPLICATE", collection="constraints")
    for index, check in enumerate(constraints):
        ports = {"input": inputs.keys(), "output": outputs.keys() if check["checkStage"] == "afterNode" else ()}
        validate_condition(check["condition"], ports, parameters, vocabulary, pointer(["constraints", index, "condition"]))
    seed = document.get("executionPolicy", {}).get("seedParameter")
    if seed is not None and seed not in parameters:
        raise WorkflowValidationError("COMPOSITION_PARAMETER_UNKNOWN", "/executionPolicy/seedParameter")


def _validate_format_cases(port, parameters, validator, path):
    names = {case["parameter"] for case in port["formatCases"]}
    if len(names) != 1 or not names <= parameters.keys():
        raise WorkflowValidationError("COMPOSITION_FORMAT_CASE_INVALID", path + "/formatCases")
    parameter = next(iter(names))
    schema = parameters[parameter]
    if not isinstance(schema, dict) or "enum" not in schema:
        raise WorkflowValidationError("COMPOSITION_FORMAT_CASE_INVALID", path + "/formatCases")
    expected = {definition_digest({"value": item}) for item in schema["enum"]}
    seen = set()
    pairs = set()
    for case in port["formatCases"]:
        pair = case["formatPair"]
        pairs.add((pair["mediaType"], pair["format"]))
        for item in case["values"]:
            token = definition_digest({"value": item})
            if token in seen or not validator.evolve(schema=schema).is_valid(item):
                raise WorkflowValidationError("COMPOSITION_FORMAT_CASE_INVALID", path + "/formatCases")
            seen.add(token)
    if seen != expected or pairs != format_pairs(port):
        raise WorkflowValidationError("COMPOSITION_FORMAT_CASE_INVALID", path + "/formatCases")


def _validate_output_semantics(semantics, inputs, parameters, vocabulary, path):
    fixed = semantics.get("fixed", {})
    validate_requirements(fixed, vocabulary, path + "/fixed")
    inherited = []
    for item in semantics.get("inherit", []):
        if item["inputPort"] not in inputs:
            raise WorkflowValidationError("WORKFLOW_INPUT_BINDING_INVALID", path + "/inherit")
        inherited.extend(item["facts"])
    transformed = []
    for item in semantics.get("transform", []):
        name = item["property"]
        if item["parameter"] not in parameters:
            raise WorkflowValidationError("COMPOSITION_PARAMETER_UNKNOWN", path + "/transform")
        if item["rule"] == "convertUnit":
            if name != "unit" or item.get("inputPort") not in inputs:
                raise WorkflowValidationError("COMPOSITION_TRANSFORM_INVALID", path + "/transform")
        elif "inputPort" in item:
            raise WorkflowValidationError("COMPOSITION_TRANSFORM_INVALID", path + "/transform")
        transformed.append(name)
    names = [*fixed, *inherited, *transformed, *semantics.get("measured", [])]
    if set(names) - FACT_TYPES.keys():
        raise WorkflowValidationError("COMPOSITION_FACT_UNSUPPORTED", path)
    if len(names) != len(set(names)):
        raise WorkflowValidationError("COMPOSITION_OUTPUT_FACT_CONFLICT", path)
