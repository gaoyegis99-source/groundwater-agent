from __future__ import annotations

from copy import deepcopy

from .formats import _port_pairs
from .graph import _compile_workflow, input_members
from .validation import (
    DIGEST,
    GRAPH_SCHEMA,
    NAME,
    StrictValidator,
    TEXT,
    WorkflowValidationError,
    object_schema,
    pointer,
    require_json,
    validate,
)


SOURCE_SCHEMA = object_schema({
    "name": NAME,
    "source": {"enum": ["USER", "DATA", "KNOWLEDGE", "OPERATOR_DEFAULT"]},
    "explanation": TEXT,
})
DATA_REFERENCE_SCHEMA = object_schema({
    "kind": {"const": "data_version"}, "service": {"const": "data-primary"},
    "dataset_id": TEXT, "version_id": TEXT, "semantic_id": TEXT, "object_id": TEXT,
    "checksum": {"type": "string", "pattern": "^sha256:[0-9a-f]{64}$"},
    "media_type": TEXT, "format": TEXT,
    "size_bytes": {"type": "integer", "minimum": 0},
    "output_name": {"type": ["string", "null"], "minLength": 1},
}, required=[
    "kind", "service", "dataset_id", "version_id", "semantic_id", "object_id",
    "checksum", "media_type", "format", "size_bytes",
])
DATA_BINDING_SCHEMA = object_schema({
    "input_name": NAME, "data_ref": DATA_REFERENCE_SCHEMA,
})


def _pending_required(error, bound: set[str]) -> bool:
    return (
        error.validator == "required" and not error.absolute_path
        and set(error.validator_value) - set(error.instance) <= bound
    )


def _validate_node_parameters(node: dict, values: dict, node_index: int) -> None:
    schema = deepcopy(node["parameterSchema"])
    supplied = {
        parameter: values[binding["workflowParameter"]]
        for parameter, binding in node["parameters"].items()
        if binding["workflowParameter"] in values
    }
    schema["required"] = [name for name in schema.get("required", []) if name in supplied]
    bound = set(node["parameters"])
    for error in StrictValidator(schema).iter_errors(supplied):
        if _pending_required(error, bound):
            continue
        # A missing discriminator is already a draft input gap, not a known
        # choice of the conditional branch (JSON Schema's `if` is otherwise
        # vacuously true for an absent property).
        location = list(error.absolute_schema_path)
        if (error.validator == "required" and len(location) >= 3
                and location[0] == "allOf" and location[2] in {"then", "else"}):
            condition = schema["allOf"][location[1]]["if"]
            if not set(condition.get("properties", {})) <= set(supplied):
                continue
        if error.validator == "oneOf" and error.context:
            branches: dict[int, list] = {}
            for issue in error.context:
                branches.setdefault(issue.schema_path[0], []).append(issue)
            if any(all(_pending_required(issue, bound) for issue in issues)
                   for issues in branches.values()):
                continue
        missing_binding = error.validator == "required" or (
            error.validator == "oneOf" and error.context
            and all(issue.validator == "required" for issue in error.context)
        )
        value_path = list(error.absolute_path)
        path = pointer(["graph", "nodes", node_index, "parameters"]) if missing_binding else "/parameter_values"
        if not missing_binding and value_path:
            path += pointer([node["parameters"][value_path[0]]["workflowParameter"], *value_path[1:]])
        raise WorkflowValidationError(
            "DRAFT_PARAMETER_BINDING_REQUIRED" if missing_binding else "DRAFT_PARAMETER_INVALID",
            path,
        )


def _draft_parameter_nodes(
    raw: dict, catalog: dict[tuple[str, str], dict]
) -> list[dict] | None:
    workflow_parameters = set(raw["parameters"])
    prepared = []
    for node in raw["nodes"]:
        operator = catalog.get((node["operatorId"], node["operatorVersion"]))
        if operator is None:
            return None
        schema = operator["parametersSchema"]
        properties = schema.get("properties", {})
        required = set(schema.get("required", []))
        bound = node["parameters"]
        if (
            not required <= set(bound)
            or not set(bound) <= set(properties)
            or any(
                binding["workflowParameter"] not in workflow_parameters
                for binding in bound.values()
            )
        ):
            return None
        prepared.append({**node, "parameterSchema": schema})
    return prepared


def compile_draft(
    graph: object, catalog: dict[tuple[str, str], dict], *, workflow_id: str,
    operator_catalog_digest: str, parameter_values: object, parameter_sources: object,
    data_bindings: object, version: str = "0.1.0",
    collection_operators: dict[tuple[str, str], dict] | None = None,
) -> dict:
    """Freeze a validated definition and supplied facts; never execute or install it."""
    require_json({
        "graph": graph, "parameter_values": parameter_values,
        "parameter_sources": parameter_sources, "data_bindings": data_bindings,
    })
    validate(graph, GRAPH_SCHEMA, "WORKFLOW_FIELDS_INVALID", "/graph")
    validate(operator_catalog_digest, DIGEST, "OPERATOR_CATALOG_DIGEST_INVALID")
    raw = {"schemaVersion": "workflow.v6", "workflowId": workflow_id, "version": version, **graph}
    validate(parameter_values, {"type": "object", "propertyNames": NAME}, "DRAFT_PARAMETERS_INVALID", "/parameter_values")
    validate(parameter_sources, {"type": "array", "items": SOURCE_SCHEMA}, "DRAFT_PARAMETER_SOURCES_INVALID", "/parameter_sources")
    sources = {source["name"]: source for source in parameter_sources}
    if len(sources) != len(parameter_sources) or set(sources) != set(parameter_values):
        raise WorkflowValidationError("DRAFT_PARAMETER_SOURCES_INVALID", "/parameter_sources")
    if not set(parameter_values) <= set(raw["parameters"]):
        raise WorkflowValidationError("DRAFT_PARAMETER_UNKNOWN", "/parameter_values")
    parameter_nodes = _draft_parameter_nodes(raw, catalog)
    if parameter_nodes is None:
        try:
            _compile_workflow(raw, catalog, collection_operators=collection_operators)
        except WorkflowValidationError as error:
            raise WorkflowValidationError(error.code, "/graph" + error.path) from error
        raise AssertionError("validated workflow parameter bindings were not available")
    parameter_usage: dict[str, list[tuple[dict, str]]] = {
        name: [] for name in raw["parameters"]
    }
    for node in parameter_nodes:
        for parameter, binding in node["parameters"].items():
            parameter_usage[binding["workflowParameter"]].append((node, parameter))
    for name, value in parameter_values.items():
        for node, parameter in parameter_usage[name]:
            schema = node["parameterSchema"]["properties"][parameter]
            validate(
                value,
                schema,
                "DRAFT_PARAMETER_INVALID",
                pointer(["parameter_values", name]),
            )
            if sources[name]["source"] == "OPERATOR_DEFAULT" and (
                "default" not in schema or value != schema["default"]
            ):
                raise WorkflowValidationError(
                    "DRAFT_OPERATOR_DEFAULT_INVALID",
                    pointer(["parameter_values", name]),
                )
    for index, node in enumerate(parameter_nodes):
        _validate_node_parameters(node, parameter_values, index)
    try:
        workflow = _compile_workflow(
            raw, catalog, parameter_values=parameter_values, collection_operators=collection_operators,
        )
    except WorkflowValidationError as error:
        raise WorkflowValidationError(error.code, "/graph" + error.path) from error
    validate(data_bindings, {"type": "array", "items": DATA_BINDING_SCHEMA}, "DRAFT_DATA_BINDINGS_INVALID", "/data_bindings")
    usage: dict[str, list[tuple[dict, str]]] = {name: [] for name in raw["parameters"]}
    for node in workflow["nodes"]:
        for parameter, binding in node["parameters"].items():
            usage[binding["workflowParameter"]].append((node, parameter))
    if any(not targets for targets in usage.values()):
        raise WorkflowValidationError("DRAFT_PARAMETER_UNBOUND", "/graph/parameters")
    inputs = {item["name"]: item for item in workflow["inputs"]}
    used_inputs = {
        binding["workflowInput"] for node in workflow["nodes"]
        for source in node["inputs"].values() for binding in input_members(source)
        if "workflowInput" in binding
    }
    if set(inputs) != used_inputs:
        raise WorkflowValidationError("DRAFT_INPUT_UNBOUND", "/graph/inputs")
    bindings = {binding["input_name"]: binding["data_ref"] for binding in data_bindings}
    if len(bindings) != len(data_bindings) or not set(bindings) <= set(inputs):
        raise WorkflowValidationError("DRAFT_DATA_BINDINGS_INVALID", "/data_bindings")
    for index, (name, reference) in enumerate(bindings.items()):
        if (reference["media_type"], reference["format"]) not in _port_pairs(inputs[name]):
            raise WorkflowValidationError("DRAFT_DATA_FORMAT_INCOMPATIBLE", pointer(["data_bindings", index, "data_ref"]))
    missing = [
        {
            "kind": "DATA", "name": name,
            "required_by": [
                {"node_id": node["nodeId"], "port": port}
                for node in workflow["nodes"] for port, binding in node["inputs"].items()
                if any(member.get("workflowInput") == name for member in input_members(binding))
            ],
        }
        for name in inputs if name not in bindings
    ]
    for name, targets in usage.items():
        required_by = [
            {"node_id": node["nodeId"], "port": parameter}
            for node, parameter in targets
        ]
        if name not in parameter_values and required_by:
            missing.append({"kind": "PARAMETER", "name": name, "required_by": required_by})
    return {
        "schemaVersion": "workflow-draft.v1", "workflow": deepcopy(raw),
        "workflow_checksum": workflow["definitionChecksum"],
        "operator_catalog_digest": operator_catalog_digest,
        "operators": [
            {
                "node_id": node["nodeId"], "operator_id": node["operatorId"],
                "operator_version": node["operatorVersion"],
                "implementation_revision": node["implementationRevision"],
                "runtime": node["runtime"],
                "title": catalog[(node["operatorId"], node["operatorVersion"])]["title"],
            }
            for node in workflow["nodes"]
        ],
        "parameter_values": deepcopy(parameter_values),
        "parameter_sources": deepcopy(parameter_sources),
        "data_bindings": deepcopy(data_bindings),
        "missing_inputs": missing, "execution_allowed": False,
    }
