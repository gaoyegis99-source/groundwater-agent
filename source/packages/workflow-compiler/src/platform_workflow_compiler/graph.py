"""Compile Workflow v6 in ordered validation and graph-propagation stages."""
from __future__ import annotations

import re

from .formats import _compiled_port, _conditional_output_pairs, _formats_compatible, _port_pairs
from .validation import WorkflowValidationError, checksum, pointer, require_string, string_set, validate_raw_workflow

NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_.-]*$")
VERSION_PATTERN = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")

def _named_list(value: object, name_key: str, error: str) -> dict[str, dict]:
    if not isinstance(value, list):
        raise WorkflowValidationError(error)
    result: dict[str, dict] = {}
    for item in value:
        if not isinstance(item, dict):
            raise WorkflowValidationError(error)
        name = require_string(item.get(name_key), error)
        if name in result:
            raise WorkflowValidationError(error)
        result[name] = item
    return result


def _topological_order(dependencies: dict[str, set[str]]) -> list[str]:
    pending, resolved = (
        {name: set(value) for name, value in dependencies.items()},
        set(),
    )
    topological: list[str] = []
    while pending:
        ready = sorted(name for name, deps in pending.items() if deps <= resolved)
        if not ready:
            raise WorkflowValidationError("WORKFLOW_CYCLE", "/nodes")
        for name in ready:
            resolved.add(name)
            del pending[name]
            topological.append(name)
    return topological


class _WorkflowCompilation:
    """Own intermediate contracts for one pure, isolated compilation."""

    def __init__(self, raw: dict, operators: dict[tuple[str, str], dict], parameter_values: dict[str, object] | None):
        self.raw = raw
        self.operators = operators
        self.parameter_values = parameter_values
        validate_raw_workflow(self.raw)
        if self.raw["schemaVersion"] != "workflow.v6":
            raise WorkflowValidationError("WORKFLOW_DSL_VERSION_INVALID")
        workflow_id = require_string(self.raw["workflowId"], "WORKFLOW_ID_INVALID")
        version = require_string(self.raw["version"], "WORKFLOW_VERSION_INVALID")
        if not NAME_PATTERN.fullmatch(workflow_id) or not VERSION_PATTERN.fullmatch(
            version
        ):
            raise WorkflowValidationError("WORKFLOW_ID_INVALID")
        self.inputs = _named_list(self.raw["inputs"], "name", "WORKFLOW_INPUT_INVALID")
        if any(item.get("cardinality") != "one" for item in self.inputs.values()):
            raise WorkflowValidationError("WORKFLOW_CARDINALITY_UNSUPPORTED")
        self.parameters = string_set(self.raw["parameters"], "WORKFLOW_PARAMETER_INVALID")
        self.nodes = _named_list(self.raw["nodes"], "nodeId", "WORKFLOW_NODE_INVALID")
        if not self.nodes:
            raise WorkflowValidationError("WORKFLOW_NODE_INVALID", "/nodes")
        self.node_indices = {node_id: index for index, node_id in enumerate(self.nodes)}
        self.declared_outputs: dict[tuple[str, str], dict] = {}
        self.node_operators: dict[str, dict] = {}

    def compile(self) -> dict:
        self._resolve_operators()
        self.dependencies = {}
        for node_id, node in self.nodes.items():
            self._validate_bindings(node_id, node)
        topological = _topological_order(self.dependencies)
        self.output_contracts: dict[tuple[str, str], dict] = {}
        self.compiled: dict[str, dict] = {}
        self.workflow_inputs = {
            name: _compiled_port(item, _port_pairs(item)) for name, item in self.inputs.items()
        }
        for node_id in topological:
            self._compile_node(node_id)
        self._validate_outputs()
        return {
            **self.raw,
            "inputs": list(self.workflow_inputs.values()),
            "nodes": [self.compiled[node_id] for node_id in self.nodes],
            "definitionChecksum": checksum(self.raw),
        }

    def _resolve_operators(self) -> None:
        for node_id, node in self.nodes.items():
            if set(node) != {
                "nodeId",
                "operatorId",
                "operatorVersion",
                "inputs",
                "parameters",
            } or not NAME_PATTERN.fullmatch(node_id):
                raise WorkflowValidationError("WORKFLOW_NODE_INVALID", pointer(["nodes", self.node_indices[node_id]]))
            key = (node["operatorId"], node["operatorVersion"])
            if key not in self.operators:
                raise WorkflowValidationError("WORKFLOW_OPERATOR_UNKNOWN", pointer(["nodes", self.node_indices[node_id], "operatorId"]))
            operator = self.operators[key]
            self.node_operators[node_id] = operator
            for output in operator.get("outputs", []):
                self.declared_outputs[(node_id, output["name"])] = output

    def _validate_bindings(self, node_id: str, node: dict) -> None:
        operator = self.node_operators[node_id]
        op_inputs = _named_list(
            operator.get("inputs"), "name", "OPERATOR_MANIFEST_INVALID"
        )
        bindings = node["inputs"]
        if not isinstance(bindings, dict) or set(bindings) != set(op_inputs):
            raise WorkflowValidationError("WORKFLOW_INPUT_BINDING_INVALID", pointer(["nodes", self.node_indices[node_id], "inputs"]))
        node_dependencies: set[str] = set()
        for port_name, binding in bindings.items():
            path = pointer(["nodes", self.node_indices[node_id], "inputs", port_name])
            if not isinstance(binding, dict) or len(binding) != 1:
                raise WorkflowValidationError("WORKFLOW_INPUT_BINDING_INVALID", path)
            target = op_inputs[port_name]
            if target.get("cardinality") != "one":
                raise WorkflowValidationError("WORKFLOW_CARDINALITY_UNSUPPORTED", path)
            if "workflowInput" in binding:
                source_name = binding["workflowInput"]
                if source_name not in self.inputs:
                    raise WorkflowValidationError("WORKFLOW_PORT_INCOMPATIBLE", path + "/workflowInput")
            elif "nodeOutput" in binding:
                reference = binding["nodeOutput"]
                if not isinstance(reference, dict) or set(reference) != {
                    "nodeId",
                    "port",
                }:
                    raise WorkflowValidationError("WORKFLOW_INPUT_BINDING_INVALID", path + "/nodeOutput")
                upstream = reference["nodeId"]
                source = self.declared_outputs.get((upstream, reference["port"]))
                if source is None or source.get("kind") != "artifact":
                    raise WorkflowValidationError("WORKFLOW_PORT_INCOMPATIBLE", path + "/nodeOutput")
                node_dependencies.add(upstream)
            else:
                raise WorkflowValidationError("WORKFLOW_INPUT_BINDING_INVALID", path)
        self._validate_parameters(node_id, node)
        self.dependencies[node_id] = node_dependencies

    def _validate_parameters(self, node_id: str, node: dict) -> None:
        operator = self.node_operators[node_id]
        bound = node["parameters"]
        parameter_path = pointer(["nodes", self.node_indices[node_id], "parameters"])
        if not isinstance(bound, dict):
            raise WorkflowValidationError("WORKFLOW_PARAMETER_BINDING_INVALID", parameter_path)
        operator_schema = operator.get("parametersSchema", {})
        properties, required = (
            operator_schema.get("properties", {}),
            set(operator_schema.get("required", [])),
        )
        if not required <= set(bound) or not set(bound) <= set(properties):
            raise WorkflowValidationError("WORKFLOW_PARAMETER_BINDING_INVALID", parameter_path)
        for parameter, binding in bound.items():
            if (
                not isinstance(binding, dict)
                or set(binding) != {"workflowParameter"}
                or binding["workflowParameter"] not in self.parameters
            ):
                raise WorkflowValidationError("WORKFLOW_PARAMETER_BINDING_INVALID", parameter_path + pointer([parameter]))

    def _compile_node(self, node_id: str) -> None:
        node, operator = self.nodes[node_id], self.node_operators[node_id]
        bound_inputs: dict[str, set[tuple[str, str]]] = {}
        input_contracts = []
        for target in operator["inputs"]:
            binding = node["inputs"][target["name"]]
            source = (
                self.workflow_inputs[binding["workflowInput"]]
                if "workflowInput" in binding
                else self.output_contracts[
                    (binding["nodeOutput"]["nodeId"], binding["nodeOutput"]["port"])
                ]
            )
            if not _formats_compatible(source, target):
                raise WorkflowValidationError("WORKFLOW_PORT_INCOMPATIBLE", pointer(["nodes", self.node_indices[node_id], "inputs", target["name"]]))
            bound_inputs[target["name"]] = _port_pairs(source)
            input_contracts.append(_compiled_port(target, _port_pairs(target)))
        compiled_outputs = []
        bound_parameters = (
            None
            if self.parameter_values is None
            else {
                parameter: self.parameter_values[binding["workflowParameter"]]
                for parameter, binding in node["parameters"].items()
                if binding["workflowParameter"] in self.parameter_values
            }
        )
        for output in operator["outputs"]:
            if output["kind"] == "artifact":
                pairs = _conditional_output_pairs(
                    output, operator, bound_inputs, bound_parameters
                )
                contract = _compiled_port(output, pairs)
            else:
                contract = output
            self.output_contracts[(node_id, output["name"])] = contract
            compiled_outputs.append(contract)
        self.compiled[node_id] = {
            **node,
            "runtime": operator["runtime"],
            **(
                {"remoteBinding": operator["remoteBinding"]}
                if operator["runtime"] == "remote_http"
                else {}
            ),
            "implementationRevision": operator["implementationRevision"],
            "memoryLimitBytes": operator["memoryLimitBytes"],
            "timeoutSeconds": operator["timeoutSeconds"],
            "inputContracts": input_contracts,
            "outputContracts": compiled_outputs,
            "parameterSchema": operator["parametersSchema"],
        }

    def _validate_outputs(self) -> None:
        outputs = _named_list(self.raw["outputs"], "name", "WORKFLOW_OUTPUT_INVALID")
        if not outputs:
            raise WorkflowValidationError("WORKFLOW_OUTPUT_INVALID", "/outputs")
        reachable: set[str] = set()
        for index, output in enumerate(outputs.values()):
            reference = output.get("nodeOutput")
            if (
                set(output) != {"name", "nodeOutput"}
                or not isinstance(reference, dict)
                or set(reference) != {"nodeId", "port"}
                or (reference["nodeId"], reference["port"]) not in self.output_contracts
            ):
                raise WorkflowValidationError("WORKFLOW_OUTPUT_INVALID", pointer(["outputs", index]))
            pending_nodes = [reference["nodeId"]]
            while pending_nodes:
                current = pending_nodes.pop()
                if current not in reachable:
                    reachable.add(current)
                    pending_nodes.extend(self.dependencies[current])
        if reachable != set(self.nodes):
            unreachable = next(node_id for node_id in self.nodes if node_id not in reachable)
            raise WorkflowValidationError("WORKFLOW_NODE_UNREACHABLE", pointer(["nodes", self.node_indices[unreachable]]))


def _compile_workflow(
    raw: dict, operators: dict[tuple[str, str], dict], *,
    parameter_values: dict[str, object] | None = None,
) -> dict:
    return _WorkflowCompilation(raw, operators, parameter_values).compile()


def compile_workflow(raw: dict, operators: dict[tuple[str, str], dict]) -> dict:
    return _compile_workflow(raw, operators)
