from __future__ import annotations

import json
from pathlib import Path
import re

from jsonschema import Draft202012Validator, SchemaError

from .contracts import canonical_json, sha256_bytes


def load_executor_config(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as cause:
        raise ValueError("EXECUTOR_CONFIG_INVALID") from cause
    if (
        not isinstance(value, dict)
        or value.get("schemaVersion")
        not in {"executor-config.v6", "executor-config.v7", "executor-config.v8"}
    ):
        raise ValueError("EXECUTOR_CONFIG_INVALID")
    required = {
        "schemaVersion",
        "productId",
        "operatorCatalogDigest",
        "workerRuntimes",
        "leaseHeartbeatSeconds",
        "leaseSeconds",
        "models",
    }
    if value.get("schemaVersion") == "executor-config.v8":
        required.add("remoteServices")
    if (
        set(value) != required
        or value["leaseHeartbeatSeconds"] != 10
        or value["leaseSeconds"] != 45
    ):
        raise ValueError("EXECUTOR_CONFIG_INVALID")
    try:
        canonical_json(value)
        _text(value["productId"])
        _digest(value["operatorCatalogDigest"])
        runtimes = value["workerRuntimes"]
        fixed_capacities = {
            "native": {"slots": 2, "memoryBytes": 17179869184},
            "qgis": {"slots": 1, "memoryBytes": 8589934592},
            "remote_http": {"slots": 2, "memoryBytes": 2147483648},
        }
        if (
            not isinstance(runtimes, dict)
            or not runtimes
            or any(
                name not in fixed_capacities
                or not isinstance(capacity, dict)
                or set(capacity) != {"slots", "memoryBytes"}
                or isinstance(capacity["slots"], bool)
                or not isinstance(capacity["slots"], int)
                or not 1 <= capacity["slots"] <= 64
                or isinstance(capacity["memoryBytes"], bool)
                or not isinstance(capacity["memoryBytes"], int)
                or not 256 * 1024**2 <= capacity["memoryBytes"] <= 1024**4
                or (
                    value["schemaVersion"] == "executor-config.v6"
                    and capacity != fixed_capacities[name]
                )
                for name, capacity in runtimes.items()
            )
            or not isinstance(value["models"], list)
            or not value["models"]
        ):
            raise ValueError("EXECUTOR_CONFIG_INVALID")
        remote_services = (
            _remote_services(value["remoteServices"])
            if value["schemaVersion"] == "executor-config.v8"
            else {}
        )
        identities = set()
        for model in value["models"]:
            _model(model, runtimes, remote_services)
            identity = (model["modelId"], model["version"])
            if identity in identities:
                raise ValueError("EXECUTOR_CONFIG_INVALID")
            identities.add(identity)
    except (ValueError, SchemaError) as cause:
        raise ValueError("EXECUTOR_CONFIG_INVALID") from cause
    return value


def definition_set_digest(config: dict) -> str:
    """Return the same executable-definition identity calculated by Kernel."""
    value = {
        "operatorCatalogDigest": config["operatorCatalogDigest"],
        "models": sorted(
            (
                {
                    "modelId": model["modelId"],
                    "version": model["version"],
                    "modelChecksum": model["modelChecksum"],
                    "workflowId": model["workflowRef"]["workflowId"],
                    "workflowVersion": model["workflowRef"]["version"],
                    "workflowChecksum": model["workflowRef"]["checksum"],
                    "executorId": model["executorId"],
                }
                for model in config["models"]
            ),
            key=lambda item: (item["modelId"], item["version"]),
        ),
    }
    return sha256_bytes(canonical_json(value))


def _model(model: object, runtimes: dict, remote_services: dict) -> None:
    fields_v4 = {
        "schemaVersion", "modelId", "version", "workflowRef", "tool",
        "inputBindings", "defaultParameters", "parameterBindings", "deadlineSeconds",
        "maxParallelNodes", "finalOutputs", "executionService", "executorId",
        "modelChecksum", "inputSchemaChecksum", "workflow", "outputPublications",
    }
    if not isinstance(model, dict) or model.get("schemaVersion") not in {
        "executable-model.v4", "executable-model.v5", "executable-model.v6"
    }:
        raise ValueError("EXECUTOR_CONFIG_INVALID")
    fields_v5 = fields_v4 | {"fieldRequirements"}
    fields = (
        fields_v5 | {"semanticRequirements"}
        if model["schemaVersion"] == "executable-model.v6"
        else fields_v5
        if model["schemaVersion"] == "executable-model.v5"
        else fields_v4
    )
    _fields(model, fields)
    for name in ("modelId", "version", "executionService", "executorId"):
        _text(model[name])
    for name in ("deadlineSeconds", "maxParallelNodes"):
        _positive(model[name])
    for name in ("inputBindings", "defaultParameters", "parameterBindings"):
        if not isinstance(model[name], dict):
            raise ValueError("EXECUTOR_CONFIG_INVALID")
    for bindings in (model["inputBindings"], model["parameterBindings"]):
        for source, target in bindings.items():
            _text(source)
            _text(target)
    if model["schemaVersion"] in {"executable-model.v5", "executable-model.v6"}:
        requirements = model["fieldRequirements"]
        if not isinstance(requirements, list):
            raise ValueError("EXECUTOR_CONFIG_INVALID")
        for requirement in requirements:
            _fields(
                requirement,
                {"parameter", "inputParameter", "identifier", "allowedTypes"},
            )
            _text(requirement["parameter"])
            _text(requirement["inputParameter"])
            if requirement["identifier"] not in {"fieldId", "name"}:
                raise ValueError("EXECUTOR_CONFIG_INVALID")
            if (
                not isinstance(requirement["allowedTypes"], list)
                or not requirement["allowedTypes"]
                or any(
                    not isinstance(item, str) or not item
                    for item in requirement["allowedTypes"]
                )
            ):
                raise ValueError("EXECUTOR_CONFIG_INVALID")
    if model["schemaVersion"] == "executable-model.v6":
        requirements = model["semanticRequirements"]
        if not isinstance(requirements, list):
            raise ValueError("EXECUTOR_CONFIG_INVALID")
        allowed_shapes = {
            "fieldUnit": (
                {"kind", "inputParameter", "fieldParameter"},
                {"kind", "inputParameter", "fieldParameter", "unitParameter"},
            ),
            "crs": (
                {"kind", "inputParameter"},
                {"kind", "inputParameter", "crsParameter"},
            ),
            "crsParameter": (
                {"kind", "parameter", "requirement"},
            ),
            "timeField": (
                {"kind", "inputParameter", "fieldParameter"},
            ),
            "timeRange": (
                {
                    "kind", "inputParameter", "fieldParameter",
                    "startParameter", "endParameter",
                },
            ),
            "verticalBasis": (
                {
                    "kind", "inputParameter", "referenceFieldParameter",
                    "depthFieldParameter", "referenceMeanings", "depthMeanings",
                },
            ),
        }
        for requirement in requirements:
            if (
                not isinstance(requirement, dict)
                or requirement.get("kind") not in allowed_shapes
                or set(requirement) not in allowed_shapes[requirement["kind"]]
            ):
                raise ValueError("EXECUTOR_CONFIG_INVALID")
            for name, value in requirement.items():
                if name in {"referenceMeanings", "depthMeanings"}:
                    if (
                        not isinstance(value, list)
                        or not value
                        or any(not isinstance(item, str) or not item for item in value)
                        or len(value) != len(set(value))
                    ):
                        raise ValueError("EXECUTOR_CONFIG_INVALID")
                elif not isinstance(value, str) or not value:
                    raise ValueError("EXECUTOR_CONFIG_INVALID")
            if (
                requirement["kind"] == "crsParameter"
                and requirement["requirement"]
                not in {"valid", "projectedMetric"}
            ):
                raise ValueError("EXECUTOR_CONFIG_INVALID")
            if requirement["kind"] == "verticalBasis":
                reference_meanings = {
                    value.casefold() for value in requirement["referenceMeanings"]
                }
                depth_meanings = {
                    value.casefold() for value in requirement["depthMeanings"]
                }
                if (
                    requirement["referenceFieldParameter"]
                    == requirement["depthFieldParameter"]
                    or len(reference_meanings)
                    != len(requirement["referenceMeanings"])
                    or len(depth_meanings) != len(requirement["depthMeanings"])
                    or not reference_meanings.isdisjoint(depth_meanings)
                ):
                    raise ValueError("EXECUTOR_CONFIG_INVALID")
    _fields(model["tool"], {"name", "description", "inputSchema"})
    _text(model["tool"]["name"])
    _text(model["tool"]["description"])
    _schema(model["tool"]["inputSchema"])
    if sha256_bytes(canonical_json(model["tool"]["inputSchema"])) != model["inputSchemaChecksum"]:
        raise ValueError("EXECUTOR_CONFIG_INVALID")
    source = {key: item for key, item in model.items()
              if key not in {"modelChecksum", "inputSchemaChecksum", "workflow"}}
    if sha256_bytes(canonical_json(source)) != model["modelChecksum"]:
        raise ValueError("EXECUTOR_CONFIG_INVALID")
    _fields(model["workflowRef"], {"workflowId", "version", "checksum"})
    workflow = model["workflow"]
    _workflow(workflow, runtimes, remote_services)
    if model["workflowRef"] != {
        "workflowId": workflow["workflowId"], "version": workflow["version"],
        "checksum": workflow["definitionChecksum"],
    }:
        raise ValueError("EXECUTOR_CONFIG_INVALID")
    outputs = model["finalOutputs"]
    if (
        not isinstance(outputs, list) or not outputs
        or any(not isinstance(name, str) for name in outputs)
        or len(set(outputs)) != len(outputs)
        or not set(outputs) <= {item["name"] for item in workflow["outputs"]}
    ):
        raise ValueError("EXECUTOR_CONFIG_INVALID")
    publications = model["outputPublications"]
    if not isinstance(publications, dict) or set(publications) != set(outputs):
        raise ValueError("EXECUTOR_CONFIG_INVALID")
    for publication in publications.values():
        _fields(publication, {"title", "format", "mediaType", "policy", "semantics"})
        for name in ("title", "format", "mediaType", "policy"):
            _text(publication[name])
        if publication["policy"] not in {"FILE_ONLY", "FILE_QUERYABLE", "FILE_AND_METRICS"}:
            raise ValueError("EXECUTOR_CONFIG_INVALID")
        if not isinstance(publication["semantics"], dict):
            raise ValueError("EXECUTOR_CONFIG_INVALID")


def _workflow(workflow: object, runtimes: dict, remote_services: dict) -> None:
    _fields(workflow, {
        "schemaVersion", "workflowId", "version", "inputs", "parameters",
        "nodes", "outputs", "definitionChecksum",
    })
    if workflow["schemaVersion"] != "workflow.v6":
        raise ValueError("EXECUTOR_CONFIG_INVALID")
    _text(workflow["workflowId"])
    _text(workflow["version"])
    _digest(workflow["definitionChecksum"])
    for name in ("inputs", "parameters", "nodes", "outputs"):
        if not isinstance(workflow[name], list):
            raise ValueError("EXECUTOR_CONFIG_INVALID")
    if not workflow["nodes"]:
        raise ValueError("EXECUTOR_CONFIG_INVALID")
    source_nodes = []
    source_fields = {"nodeId", "operatorId", "operatorVersion", "inputs", "parameters"}
    for node in workflow["nodes"]:
        compiled_fields = source_fields | {
            "runtime", "implementationRevision", "memoryLimitBytes", "timeoutSeconds",
            "inputContracts", "outputContracts", "parameterSchema",
        }
        if node.get("runtime") == "remote_http":
            compiled_fields.add("remoteBinding")
        _fields(node, compiled_fields)
        for name in ("nodeId", "operatorId", "operatorVersion"):
            _text(node[name])
        _digest(node["implementationRevision"])
        if not isinstance(node["runtime"], str) or node["runtime"] not in runtimes:
            raise ValueError("EXECUTOR_CONFIG_INVALID")
        if node["runtime"] == "remote_http":
            binding = node["remoteBinding"]
            _fields(binding, {"serviceId", "operationType"})
            _text(binding["serviceId"])
            _text(binding["operationType"])
            if (
                binding["serviceId"] not in remote_services
                or binding["operationType"]
                not in remote_services[binding["serviceId"]]["allowedOperations"]
            ):
                raise ValueError("EXECUTOR_CONFIG_INVALID")
        _positive(node["memoryLimitBytes"])
        _positive(node["timeoutSeconds"])
        if not isinstance(node["inputs"], dict) or not isinstance(node["parameters"], dict):
            raise ValueError("EXECUTOR_CONFIG_INVALID")
        for bindings in node["parameters"].values():
            _fields(bindings, {"workflowParameter"})
            _text(bindings["workflowParameter"])
        for name in ("inputContracts", "outputContracts"):
            if not isinstance(node[name], list) or any(not isinstance(item, dict) for item in node[name]):
                raise ValueError("EXECUTOR_CONFIG_INVALID")
        _schema(node["parameterSchema"])
        source_nodes.append({name: node[name] for name in source_fields})
    source_inputs = []
    for item in workflow["inputs"]:
        if not isinstance(item, dict) or not isinstance(item.get("formatPairs"), list):
            raise ValueError("EXECUTOR_CONFIG_INVALID")
        source_inputs.append({key: value for key, value in item.items() if key != "formatPairs"})
    for item in workflow["outputs"]:
        _fields(item, {"name", "nodeOutput"})
        _text(item["name"])
        _fields(item["nodeOutput"], {"nodeId", "port"})
    source = {key: item for key, item in workflow.items() if key != "definitionChecksum"}
    source.update(nodes=source_nodes, inputs=source_inputs)
    if sha256_bytes(canonical_json(source)) != workflow["definitionChecksum"]:
        raise ValueError("EXECUTOR_CONFIG_INVALID")


def _fields(value: object, required: set[str]) -> None:
    if not isinstance(value, dict) or set(value) != required:
        raise ValueError("EXECUTOR_CONFIG_INVALID")


def _text(value: object) -> None:
    if not isinstance(value, str) or not value:
        raise ValueError("EXECUTOR_CONFIG_INVALID")


def _digest(value: object) -> None:
    if not isinstance(value, str) or re.fullmatch(r"sha256:[0-9a-f]{64}", value) is None:
        raise ValueError("EXECUTOR_CONFIG_INVALID")


def _positive(value: object) -> None:
    if type(value) is not int or value <= 0:
        raise ValueError("EXECUTOR_CONFIG_INVALID")


def _schema(value: object) -> None:
    if not isinstance(value, dict) or value.get("type") != "object" or value.get("additionalProperties") is not False:
        raise ValueError("EXECUTOR_CONFIG_INVALID")
    Draft202012Validator.check_schema(value)


def _remote_services(value: object) -> dict:
    if not isinstance(value, dict):
        raise ValueError("EXECUTOR_CONFIG_INVALID")
    fields = {
        "serviceId", "baseUrlEnvironment", "allowedHostEnvironment",
        "credentialSecret", "caBundleSecret", "connectTimeoutSeconds",
        "pollIntervalSeconds", "apiVersion", "allowedServiceVersions",
        "allowedModelVersions", "allowedOperations",
    }
    for service_id, binding in value.items():
        _text(service_id)
        _fields(binding, fields)
        if binding["serviceId"] != service_id:
            raise ValueError("EXECUTOR_CONFIG_INVALID")
        for name in (
            "baseUrlEnvironment", "allowedHostEnvironment", "credentialSecret",
            "caBundleSecret", "apiVersion",
        ):
            _text(binding[name])
        for name in ("connectTimeoutSeconds", "pollIntervalSeconds"):
            _positive(binding[name])
        versions = binding["allowedServiceVersions"]
        model_versions = binding["allowedModelVersions"]
        operations = binding["allowedOperations"]
        if (
            not isinstance(versions, list)
            or not versions
            or len(versions) != len(set(versions))
            or any(not isinstance(version, str) or not version for version in versions)
            or not isinstance(model_versions, list)
            or not model_versions
            or len(model_versions) != len(set(model_versions))
            or any(
                not isinstance(version, str) or not version
                for version in model_versions
            )
            or not isinstance(operations, list)
            or not operations
            or len(operations) != len(set(operations))
            or any(
                not isinstance(operation, str) or not operation
                for operation in operations
            )
        ):
            raise ValueError("EXECUTOR_CONFIG_INVALID")
    return value
