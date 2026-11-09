"""Fail-closed export of the portable workflow.v6 subset to CWL v1.2 JSON."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
from typing import Any


class CwlExportError(ValueError):
    pass


_DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")
_IMAGE = re.compile(r"[^\s]+@sha256:[0-9a-f]{64}\Z")


def _fail(code: str) -> None:
    raise CwlExportError(code)


def _json_type(schema: dict, required: bool = True) -> Any:
    kind = schema.get("type")
    mapped: Any
    if kind == "string":
        mapped = "string"
    elif kind == "number":
        mapped = "double"
    elif kind == "integer":
        mapped = "long"
    elif kind == "boolean":
        mapped = "boolean"
    elif kind == "array" and isinstance(schema.get("items"), dict):
        mapped = {"type": "array", "items": _json_type(schema["items"])}
    elif kind == "object":
        mapped = "Any"
    else:
        _fail("CWL_JSON_SCHEMA_TYPE_UNSUPPORTED")
    return mapped if required else ["null", mapped]


def _operator_manifest(catalog: Path, operator_id: str, version: str) -> dict:
    path = catalog / "operators" / operator_id / version / "manifest.json"
    if not path.is_file():
        _fail("CWL_OPERATOR_MANIFEST_NOT_FOUND")
    value = json.loads(path.read_text(encoding="utf-8"))
    if value.get("id") != operator_id or value.get("version") != version:
        _fail("CWL_OPERATOR_MANIFEST_IDENTITY_MISMATCH")
    return value


def _registry(registry: dict) -> dict[tuple[str, str], dict]:
    if registry.get("schemaVersion") != "v6-cwl-portability.v1":
        _fail("CWL_PORTABILITY_REGISTRY_INVALID")
    entries: dict[tuple[str, str], dict] = {}
    for item in registry.get("operators", []):
        if not isinstance(item, dict):
            _fail("CWL_PORTABILITY_REGISTRY_INVALID")
        key = (item.get("id"), item.get("version"))
        if not all(isinstance(value, str) and value for value in key) or key in entries:
            _fail("CWL_PORTABILITY_REGISTRY_INVALID")
        if item.get("kind") not in {"command", "container"}:
            _fail("CWL_OPERATOR_KIND_UNSUPPORTED")
        command = item.get("baseCommand")
        if (
            not isinstance(command, list)
            or not command
            or any(not isinstance(value, str) or not value for value in command)
        ):
            _fail("CWL_COMMAND_INVALID")
        if item["kind"] == "container":
            if _IMAGE.fullmatch(str(item.get("containerImage", ""))) is None:
                _fail("CWL_CONTAINER_IMAGE_NOT_DIGEST_PINNED")
        elif _DIGEST.fullmatch(str(item.get("commandChecksum", ""))) is None:
            _fail("CWL_COMMAND_CHECKSUM_REQUIRED")
        entries[key] = item
    return entries


def _binding(binding: dict, node_ids: set[str], workflow_inputs: set[str]) -> str:
    if set(binding) == {"workflowInput"} and binding["workflowInput"] in workflow_inputs:
        return binding["workflowInput"]
    if set(binding) == {"nodeOutput"} and isinstance(binding["nodeOutput"], dict):
        source = binding["nodeOutput"]
        if set(source) == {"nodeId", "port"} and source["nodeId"] in node_ids:
            return f"{source['nodeId']}/{source['port']}"
    _fail("CWL_BINDING_UNSUPPORTED")


def _tool(
    *,
    node: dict,
    manifest: dict,
    portability: dict,
    parameter_schemas: dict[str, dict],
    required_parameters: set[str],
) -> tuple[dict, dict[str, Any]]:
    input_ports = {item["name"]: item for item in manifest.get("inputs", [])}
    output_ports = {item["name"]: item for item in manifest.get("outputs", [])}
    input_bindings = portability.get("inputBindings", {})
    parameter_bindings = portability.get("parameterBindings", {})
    output_globs = portability.get("outputGlobs", {})
    if set(node.get("inputs", {})) != set(input_bindings):
        _fail("CWL_INPUT_BINDINGS_INCOMPLETE")
    if set(node.get("parameters", {})) != set(parameter_bindings):
        _fail("CWL_PARAMETER_BINDINGS_INCOMPLETE")
    if set(output_ports) != set(output_globs):
        _fail("CWL_OUTPUT_BINDINGS_INCOMPLETE")
    inputs: dict[str, dict] = {}
    for name, binding in input_bindings.items():
        port = input_ports.get(name)
        if port is None or not isinstance(binding, dict):
            _fail("CWL_INPUT_BINDING_INVALID")
        value_type: Any = "File"
        if port.get("cardinality") == "many":
            value_type = {"type": "array", "items": "File"}
        if not port.get("required", True):
            value_type = ["null", value_type]
        inputs[f"input__{name}"] = {
            "type": value_type,
            "inputBinding": binding,
        }
    for name, binding in parameter_bindings.items():
        source = node["parameters"][name]
        if set(source) != {"workflowParameter"}:
            _fail("CWL_PARAMETER_BINDING_UNSUPPORTED")
        workflow_parameter = source["workflowParameter"]
        schema = parameter_schemas.get(workflow_parameter)
        if schema is None or not isinstance(binding, dict):
            _fail("CWL_PARAMETER_SCHEMA_MISSING")
        inputs[f"parameter__{name}"] = {
            "type": _json_type(
                schema, workflow_parameter in required_parameters
            ),
            "inputBinding": binding,
        }
    outputs: dict[str, dict] = {}
    output_types: dict[str, Any] = {}
    for name, port in output_ports.items():
        declared = output_globs[name]
        if not isinstance(declared, dict) or not isinstance(declared.get("glob"), str):
            _fail("CWL_OUTPUT_BINDING_INVALID")
        if port.get("kind") == "artifact":
            value_type: Any = "File"
        elif port.get("kind") == "value":
            if declared.get("loadContents") is not True or not isinstance(
                declared.get("outputEval"), str
            ):
                _fail("CWL_VALUE_OUTPUT_ADAPTER_REQUIRED")
            value_type = _json_type(port.get("schema", {}))
        else:
            _fail("CWL_OUTPUT_KIND_UNSUPPORTED")
        outputs[name] = {"type": value_type, "outputBinding": declared}
        output_types[name] = value_type
    requirements: dict[str, Any] = {}
    if portability["kind"] == "container":
        requirements["DockerRequirement"] = {
            "dockerPull": portability["containerImage"]
        }
    tool = {
        "cwlVersion": "v1.2",
        "class": "CommandLineTool",
        "$namespaces": {"v6": "https://platform.local/contracts/cwl#"},
        "baseCommand": portability["baseCommand"],
        "arguments": portability.get("arguments", []),
        "inputs": inputs,
        "outputs": outputs,
        "requirements": requirements,
        "hints": {
            "v6:ImplementationIdentity": {
                "operatorId": node["operatorId"],
                "operatorVersion": node["operatorVersion"],
                "checksum": portability.get("commandChecksum")
                or portability["containerImage"].rsplit("@", 1)[1],
            }
        },
    }
    return tool, output_types


def export_cwl_package(
    workflow: dict,
    model: dict,
    portability_registry: dict,
    catalog: Path,
) -> dict[str, bytes]:
    if workflow.get("schemaVersion") != "workflow.v6":
        _fail("CWL_WORKFLOW_KIND_UNSUPPORTED")
    if model.get("workflowRef", {}).get("workflowId") != workflow.get("workflowId"):
        _fail("CWL_MODEL_WORKFLOW_MISMATCH")
    forbidden = {"agent", "approval", "journal", "checkpoint", "pythonCallable"}
    if forbidden.intersection(workflow):
        _fail("CWL_WORKFLOW_SEMANTICS_UNSUPPORTED")
    entries = _registry(portability_registry)
    nodes = workflow.get("nodes")
    if not isinstance(nodes, list) or not nodes:
        _fail("CWL_WORKFLOW_INVALID")
    node_ids = {node.get("nodeId") for node in nodes if isinstance(node, dict)}
    if len(node_ids) != len(nodes) or None in node_ids:
        _fail("CWL_WORKFLOW_INVALID")
    workflow_inputs = {item["name"] for item in workflow.get("inputs", [])}
    input_bindings = model.get("inputBindings", {})
    parameter_bindings = model.get("parameterBindings", {})
    if set(input_bindings) != workflow_inputs:
        _fail("CWL_MODEL_INPUT_BINDINGS_INCOMPLETE")
    properties = model.get("tool", {}).get("inputSchema", {}).get("properties", {})
    required_arguments = set(
        model.get("tool", {}).get("inputSchema", {}).get("required", [])
    )
    parameter_schemas = {
        workflow_parameter: properties[argument]
        for workflow_parameter, argument in parameter_bindings.items()
        if argument in properties
    }
    required_parameters = {
        workflow_parameter
        for workflow_parameter, argument in parameter_bindings.items()
        if argument in required_arguments
    }
    cwl_inputs: dict[str, Any] = {name: "File" for name in sorted(workflow_inputs)}
    for parameter in workflow.get("parameters", []):
        schema = parameter_schemas.get(parameter)
        if schema is None:
            _fail("CWL_PARAMETER_SCHEMA_MISSING")
        specification: Any = _json_type(schema, parameter in required_parameters)
        if parameter in model.get("defaultParameters", {}):
            specification = {
                "type": specification,
                "default": model["defaultParameters"][parameter],
            }
        cwl_inputs[parameter] = specification

    files: dict[str, bytes] = {}
    steps: dict[str, dict] = {}
    known_outputs: dict[tuple[str, str], Any] = {}
    for node in nodes:
        key = (node.get("operatorId"), node.get("operatorVersion"))
        portability = entries.get(key)
        if portability is None:
            _fail("CWL_OPERATOR_NOT_PORTABLE")
        manifest = _operator_manifest(catalog, *key)
        tool, output_types = _tool(
            node=node,
            manifest=manifest,
            portability=portability,
            parameter_schemas=parameter_schemas,
            required_parameters=required_parameters,
        )
        tool_path = f"tools/{node['nodeId']}.cwl"
        step_inputs = {
            f"input__{name}": _binding(binding, node_ids, workflow_inputs)
            for name, binding in node.get("inputs", {}).items()
        }
        for name, binding in node.get("parameters", {}).items():
            if set(binding) != {"workflowParameter"}:
                _fail("CWL_PARAMETER_BINDING_UNSUPPORTED")
            step_inputs[f"parameter__{name}"] = binding["workflowParameter"]
        step = {
            "run": tool_path,
            "in": step_inputs,
            "out": sorted(output_types),
        }
        condition = node.get("condition")
        if condition is not None:
            if (
                not isinstance(condition, dict)
                or set(condition) != {"workflowParameter"}
                or condition["workflowParameter"] not in cwl_inputs
            ):
                _fail("CWL_CONDITION_UNSUPPORTED")
            step["in"]["__condition"] = condition["workflowParameter"]
            step["when"] = "$(inputs.__condition)"
            tool["inputs"]["__condition"] = {"type": "boolean"}
        files[tool_path] = _encode(tool)
        steps[node["nodeId"]] = step
        for port, value_type in output_types.items():
            known_outputs[(node["nodeId"], port)] = value_type
    for node in nodes:
        for binding in node.get("inputs", {}).values():
            if "nodeOutput" in binding:
                source = binding["nodeOutput"]
                if (source.get("nodeId"), source.get("port")) not in known_outputs:
                    _fail("CWL_NODE_OUTPUT_NOT_FOUND")
    dependencies = {
        node["nodeId"]: {
            binding["nodeOutput"]["nodeId"]
            for binding in node.get("inputs", {}).values()
            if "nodeOutput" in binding
        }
        for node in nodes
    }
    pending = set(node_ids)
    complete: set[str] = set()
    while pending:
        ready = {node_id for node_id in pending if dependencies[node_id] <= complete}
        if not ready:
            _fail("CWL_WORKFLOW_CYCLE")
        pending -= ready
        complete |= ready

    outputs: dict[str, dict] = {}
    for output in workflow.get("outputs", []):
        source = output.get("nodeOutput", {})
        key = (source.get("nodeId"), source.get("port"))
        if key not in known_outputs:
            _fail("CWL_WORKFLOW_OUTPUT_NOT_FOUND")
        outputs[output["name"]] = {
            "type": known_outputs[key],
            "outputSource": f"{key[0]}/{key[1]}",
        }
    document = {
        "cwlVersion": "v1.2",
        "class": "Workflow",
        "$namespaces": {"v6": "https://platform.local/contracts/cwl#"},
        "label": workflow["workflowId"],
        "inputs": cwl_inputs,
        "outputs": outputs,
        "steps": steps,
        "requirements": (
            {"InlineJavascriptRequirement": {}}
            if any("when" in step for step in steps.values())
            else {}
        ),
        "hints": {
            "v6:DefinitionIdentity": {
                "workflowId": workflow["workflowId"],
                "workflowVersion": workflow["version"],
                "workflowChecksum": model["workflowRef"]["checksum"],
            }
        },
    }
    files["workflow.cwl"] = _encode(document)
    identities = {
        path: "sha256:" + hashlib.sha256(content).hexdigest()
        for path, content in sorted(files.items())
    }
    files["export-manifest.json"] = _encode(
        {
            "schemaVersion": "v6-cwl-export.v1",
            "workflowId": workflow["workflowId"],
            "workflowVersion": workflow["version"],
            "workflowChecksum": model["workflowRef"]["checksum"],
            "files": identities,
        }
    )
    return files


def write_cwl_package(files: dict[str, bytes], output: Path) -> None:
    if output.exists() and (not output.is_dir() or any(output.iterdir())):
        _fail("CWL_OUTPUT_DIRECTORY_NOT_EMPTY")
    output.mkdir(parents=True, exist_ok=True)
    for relative, content in files.items():
        target = output / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)


def _encode(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
