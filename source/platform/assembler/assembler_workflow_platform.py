"""Validate product Workflow Profiles against the existing compiled presets."""

from collections import defaultdict
from functools import cache
import hashlib
import json
from pathlib import Path
import sys

from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import ValidationError

from assembler_common import CHECKSUM_PATTERN, _canonical, _checksum, _validate_value, resolve_inside


SCHEMA = "workflow-platform.v1.schema.json"
ARTIFACT = "workflow-platform.json"
CONFIG_ERROR = "WORKFLOW_PLATFORM_INVALID"
RESULT_ERROR = "WORKFLOW_RESULT_CONTRACT_INVALID"
PARAMETER_ERROR = "WORKFLOW_PROFILE_PARAMETER_INVALID"
EVIDENCE_ERROR = "WORKFLOW_PROFILE_EVIDENCE_INVALID"


@cache
def _validator():
    path = Path(__file__).with_name(SCHEMA)
    if not path.is_file():
        path = Path(sys.prefix) / "share/platform-assembler" / SCHEMA
    schema = json.loads(path.read_bytes())
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def _pinned_json(directory, reference, code):
    if not isinstance(reference, dict) or set(reference) != {"path", "checksum"}:
        raise ValueError(code)
    if not isinstance(reference["path"], str):
        raise ValueError(code)
    try:
        path = resolve_inside(directory, reference["path"], code)
        data = path.read_bytes()
        if "sha256:" + hashlib.sha256(data).hexdigest() != reference["checksum"]:
            raise ValueError(code)
        document = json.loads(data)
        json.dumps(document, ensure_ascii=False, allow_nan=False).encode("utf-8")
    except (OSError, ValueError, UnicodeError) as error:
        raise ValueError(code) from error
    if not isinstance(document, dict):
        raise ValueError(code)
    return document


def _ref(document, id_field, checksum):
    return {"id": document[id_field], "version": document["version"], "checksum": checksum}


def _identity(reference):
    return reference["id"], reference["version"]


def _contract(raw, workflows):
    workflow = workflows.get(_identity(raw["workflowRef"]))
    if workflow is None or raw["workflowRef"] != _ref(workflow, "workflowId", workflow["definitionChecksum"]):
        raise ValueError(RESULT_ERROR)
    ports = {
        (node["nodeId"], port["name"]): port
        for node in workflow["nodes"] for port in node["outputContracts"]
    }
    workflow_outputs = {output["name"]: output["nodeOutput"] for output in workflow["outputs"]}
    outputs = {item["name"]: item for item in raw["outputs"]}
    if len(outputs) != len(raw["outputs"]) or not outputs.keys() <= workflow_outputs.keys():
        raise ValueError(RESULT_ERROR)
    for name, specification in outputs.items():
        binding = workflow_outputs[name]
        port = ports[binding["nodeId"], binding["port"]]
        pair = {field: specification[field] for field in ("mediaType", "format")}
        quality = outputs.get(specification["qualitySource"]["outputName"])
        if (
            specification["kind"] != port["kind"]
            or (port["kind"] == "artifact" and pair not in port["formatPairs"])
            or (port["kind"] == "value" and pair != {"mediaType": "application/json", "format": "json"})
            or quality is None
            or quality["kind"] != "value"
        ):
            raise ValueError(RESULT_ERROR)
    return {**raw, "ref": _ref(raw, "id", _checksum(raw))}


def _profile_parameters(raw, model):
    parameter_spec = raw["parameters"]
    fixed = parameter_spec["fixed"]
    adjustable = set(parameter_spec["adjustable"])
    schema = model["tool"]["inputSchema"]
    defaults = model["defaultParameters"]
    available = schema["properties"].keys() - set(model["inputBindings"].values())
    if fixed.keys() & adjustable or fixed.keys() | adjustable | defaults.keys() != available:
        raise ValueError(PARAMETER_ERROR)
    for name, value in fixed.items():
        _validate_value(schema["properties"][name], value, PARAMETER_ERROR)
    return fixed, {name: value for name, value in defaults.items() if name not in fixed}


def _validate_fixture_receipts(directory, requested, recorded, workflow):
    index = json.loads((directory / "index.json").read_bytes())
    if index.get("schemaVersion") != "operator-fixtures.v1":
        raise ValueError(EVIDENCE_ERROR)
    if not isinstance(recorded, dict) or recorded.keys() != requested.keys():
        raise ValueError(EVIDENCE_ERROR)
    for port in workflow["inputs"]:
        name = port["name"]
        fixture_id = requested[name]
        definition = index["fixtures"].get(fixture_id) if isinstance(fixture_id, str) else None
        if not isinstance(definition, dict):
            raise ValueError(EVIDENCE_ERROR)
        pair = {field: definition[field] for field in ("mediaType", "format")}
        try:
            path = resolve_inside(directory, definition["path"], EVIDENCE_ERROR)
            with path.open("rb") as fixture:
                checksum = "sha256:" + hashlib.file_digest(fixture, "sha256").hexdigest()
            size = path.stat().st_size
        except OSError as error:
            raise ValueError(EVIDENCE_ERROR) from error
        expected = {"fixtureId": fixture_id, **pair, "sha256": checksum, "sizeBytes": size}
        if (
            pair not in port["formatPairs"] or checksum != definition["sha256"]
            or expected["sizeBytes"] != definition["sizeBytes"] or recorded[name] != [expected]
        ):
            raise ValueError(EVIDENCE_ERROR)


def _validate_output_receipts(contract, receipt_outputs, key_values):
    for output in contract["outputs"]:
        name = output["name"]
        identity = receipt_outputs[name]
        if (
            not isinstance(identity, dict) or identity.get("kind") != output["kind"]
            or not CHECKSUM_PATTERN.fullmatch(str(identity.get("sha256", "")))
            or type(identity.get("sizeBytes")) is not int or identity["sizeBytes"] < 0
        ):
            raise ValueError(EVIDENCE_ERROR)
        if output["kind"] == "artifact" and (
            any(identity.get(key) != output[key] for key in ("mediaType", "format"))
            or not isinstance(identity.get("fileName"), str) or not identity["fileName"]
        ):
            raise ValueError(EVIDENCE_ERROR)
        if output["kind"] == "value" and name in key_values and (
            identity["sha256"] != _checksum(key_values[name])
            or identity["sizeBytes"] != len(_canonical(key_values[name]))
        ):
            raise ValueError(EVIDENCE_ERROR)
        source = output["qualitySource"]
        fields = key_values.get(source["outputName"])
        if not isinstance(fields, dict) or not set(source["fields"]) <= fields.keys():
            raise ValueError(EVIDENCE_ERROR)


def _profile_evidence(directory, fixtures_directory, raw, model, contract, fixed, defaults, workflow):
    evidence = raw["validationEvidence"]
    cases = _pinned_json(directory, evidence["cases"], EVIDENCE_ERROR)
    receipt = _pinned_json(directory, evidence["receipt"], EVIDENCE_ERROR)
    scope = _pinned_json(directory, evidence["scope"], EVIDENCE_ERROR)
    case_id = evidence["caseId"]
    workflow_ref = raw["workflowRef"]
    cases_by_id = cases.get("cases")
    case = cases_by_id.get(case_id) if isinstance(cases_by_id, dict) else None
    if cases.get("schemaVersion") != "workflow-golden-cases.v2" or not isinstance(case, dict):
        raise ValueError(EVIDENCE_ERROR)
    parameters = case.get("parameters")
    case_inputs = case.get("inputs")
    output_names = {item["name"] for item in contract["outputs"]}
    receipt_outputs = receipt.get("outputs")
    key_values = receipt.get("keyValues")
    if (
        case.get("workflowId") != workflow_ref["id"]
        or not isinstance(case_inputs, dict)
        or case_inputs.keys() != raw["inputRoles"].keys()
        or not isinstance(parameters, dict)
        or receipt.get("schemaVersion") != "workflow-golden-snapshot.v1"
        or receipt.get("caseId") != case_id
        or receipt.get("workflow") != {
            "id": workflow_ref["id"], "version": workflow_ref["version"],
            "definitionChecksum": workflow_ref["checksum"], "sourceSha256": workflow_ref["checksum"],
        }
        or not CHECKSUM_PATTERN.fullmatch(str(receipt.get("operatorCatalogDigest", "")))
        or receipt.get("parametersSha256") != _checksum(parameters)
        or not isinstance(receipt_outputs, dict)
        or receipt_outputs.keys() != output_names
        or not isinstance(key_values, dict)
    ):
        raise ValueError(EVIDENCE_ERROR)
    # The Golden snapshot covers its full pack. The linked replay records
    # which exact implementations ran, preserving evidence when unrelated
    # operators are added to the active catalog.
    if scope != {
        "schemaVersion": "workflow-profile-golden-scope.v1", "status": "PASS",
        "caseId": case_id, "workflow": receipt["workflow"],
        "operatorCatalogDigest": receipt["operatorCatalogDigest"],
        "baselineSha256": evidence["receipt"]["checksum"],
        "operatorsByNode": {
            node["nodeId"]: {
                "id": node["operatorId"], "version": node["operatorVersion"],
                "runtime": node["runtime"], "implementationRevision": node["implementationRevision"],
            }
            for node in workflow["nodes"]
        },
    }:
        raise ValueError(EVIDENCE_ERROR)
    _validate_fixture_receipts(fixtures_directory, case_inputs, receipt.get("fixtures"), workflow)
    _validate_output_receipts(contract, receipt_outputs, key_values)
    adapted = {}
    for name, value in parameters.items():
        target = model["parameterBindings"].get(name)
        if target is None or (target in adapted and _canonical(adapted[target]) != _canonical(value)):
            raise ValueError(EVIDENCE_ERROR)
        adapted[target] = value
    if any(name not in adapted or _canonical(adapted[name]) != _canonical(value) for name, value in fixed.items()):
        raise ValueError(EVIDENCE_ERROR)
    arguments = {**defaults, **adapted, **{
        argument: case_inputs[name] for name, argument in model["inputBindings"].items()
    }}
    try:
        Draft202012Validator(model["tool"]["inputSchema"], format_checker=FormatChecker()).validate(arguments)
    except ValidationError as error:
        raise ValueError(EVIDENCE_ERROR) from error


def _profile(directory, fixtures_directory, raw, workflows, models, contracts, ui):
    workflow_ref, model_ref = raw["workflowRef"], raw["modelRef"]
    workflow = workflows.get(_identity(workflow_ref))
    model = models.get(_identity(model_ref))
    if workflow is None or workflow_ref != _ref(workflow, "workflowId", workflow["definitionChecksum"]):
        raise ValueError("WORKFLOW_PROFILE_WORKFLOW_MISMATCH")
    if (
        model is None
        or model_ref != _ref(model, "modelId", model["modelChecksum"])
        or model["workflowRef"] != {
            "workflowId": workflow_ref["id"], "version": workflow_ref["version"], "checksum": workflow_ref["checksum"],
        }
    ):
        raise ValueError("WORKFLOW_PROFILE_MODEL_MISMATCH")
    result_ref = raw["resultContractRef"]
    contract = contracts.get(_identity(result_ref))
    if contract is None or contract["workflowRef"] != workflow_ref:
        raise ValueError("WORKFLOW_PROFILE_RESULT_MISMATCH")
    publications = model["outputPublications"]
    if {output["name"] for output in contract["outputs"]} != set(model["finalOutputs"]):
        raise ValueError(RESULT_ERROR)
    for output in contract["outputs"]:
        name = output["name"]
        viewer = ui["outputs"].get(name, {}).get("viewer")
        if (
            any(output[key] != publications[name][key] for key in ("mediaType", "format"))
            or (viewer is not None and viewer not in output["viewers"])
        ):
            raise ValueError(RESULT_ERROR)
    if contract["ref"] != result_ref:
        raise ValueError("WORKFLOW_PROFILE_RESULT_MISMATCH")
    inputs = {item["name"]: item for item in workflow["inputs"]}
    if raw["inputRoles"].keys() != inputs.keys() or len(set(model["inputBindings"].values())) != len(inputs):
        raise ValueError("WORKFLOW_PROFILE_INPUT_INVALID")
    fixed, defaults = _profile_parameters(raw, model)
    semantic_requirements = model.get("semanticRequirements", [])
    interval_starts = {
        item["startParameter"] for item in semantic_requirements if item["kind"] == "timeRange"
    }
    non_overlapping = raw.get("nonOverlappingTimeRanges", [])
    if any(not set(pair) <= interval_starts for pair in non_overlapping):
        raise ValueError(PARAMETER_ERROR)
    _profile_evidence(directory, fixtures_directory, raw, model, contract, fixed, defaults, workflow)
    return {
        "ref": _ref(raw, "id", _checksum(raw)),
        **{key: raw[key] for key in (
            "label", "workflowRef", "modelRef", "resultContractRef", "applicability",
            "limitations", "visibleRoles", "validationEvidence",
        )},
        "inputRoles": {
            name: {
                **role, "modelParameter": model["inputBindings"][name],
                "formatPairs": inputs[name]["formatPairs"],
            }
            for name, role in raw["inputRoles"].items()
        },
        "fieldRequirements": model.get("fieldRequirements", []),
        "semanticRequirements": semantic_requirements,
        "nonOverlappingTimeRanges": non_overlapping,
        "inputSchema": model["tool"]["inputSchema"],
        "fixedParameters": fixed,
        "adjustableParameters": raw["parameters"]["adjustable"],
        "modelDefaults": defaults,
        "defaultSource": raw["parameters"]["defaultSource"],
    }


def _resource_assessment(model, nodes, capacities):
    by_runtime = defaultdict(list)
    for node in nodes:
        by_runtime[node["runtime"]].append(node)
    max_nodes = model["maxParallelNodes"]
    estimates = {}
    for runtime, members in sorted(by_runtime.items()):
        capacity = capacities[runtime]
        memory_budget = capacity["memoryBytes"]
        allowed_memory = sorted(
            node["memoryLimitBytes"] for node in members if node["memoryLimitBytes"] <= memory_budget
        )
        count_limit = min(max_nodes, capacity["slots"], len(allowed_memory))
        # The smallest limits give the largest count that could fit. The
        # graph's dependencies and other jobs can only reduce concurrency.
        while sum(allowed_memory[:count_limit]) > memory_budget:
            count_limit -= 1
        upper_memory = sum(allowed_memory[len(allowed_memory) - count_limit:])
        estimates[runtime] = {
            "workerSlots": capacity["slots"], "workerMemoryBytes": memory_budget,
            "concurrentNodesUpperBound": count_limit,
            "declaredMemoryUpperBoundBytes": min(memory_budget, upper_memory),
            "unschedulableNodes": sorted(
                node["nodeId"] for node in members if node["memoryLimitBytes"] > memory_budget
            ),
        }
    return {
        "maxParallelNodes": max_nodes, "workflowDeadlineSeconds": model["deadlineSeconds"],
        "longestNodeTimeoutSeconds": max(node["timeoutSeconds"] for node in nodes),
        "runtimes": estimates,
    }


def _workflow_dependencies(workflows, models, profiles, ui, capacities, composition):
    models_by_workflow, profiles_by_workflow = defaultdict(list), defaultdict(list)
    for model in models.values():
        ref = model["workflowRef"]
        models_by_workflow[ref["workflowId"], ref["version"]].append(model)
    for profile in profiles.values():
        profiles_by_workflow[_identity(profile["workflowRef"])].append({
            "profileRef": profile["ref"], "modelRef": profile["modelRef"],
        })
    dependencies = []
    for identity, workflow in sorted(workflows.items()):
        operators, fingerprints = {}, defaultdict(list)
        nodes = workflow["nodes"]
        for node in nodes:
            key = f"{node['operatorId']}@{node['operatorVersion']}"
            operator = operators.setdefault(key, {
                "id": node["operatorId"], "version": node["operatorVersion"],
                "implementationRevision": node["implementationRevision"], "nodes": [],
            })
            operator["nodes"].append(node["nodeId"])
            fingerprints[_checksum([
                key, node["implementationRevision"], node["inputs"], node["parameters"],
            ])].append(node["nodeId"])
        dependencies.append({
            "workflowRef": _ref(workflow, "workflowId", workflow["definitionChecksum"]),
            "operators": [operators[key] for key in sorted(operators)],
            "compositionUnsupported": sorted(operators.keys() - composition),
            "duplicateComputationCandidates": [group for group in fingerprints.values() if len(group) > 1],
            "profiles": profiles_by_workflow[identity],
            "models": [
                {
                    "modelRef": _ref(model, "modelId", model["modelChecksum"]),
                    "entries": [{"kind": "PRODUCT_MODEL_ENTRY", "id": model["modelId"]}]
                    if model["modelId"] in ui["models"] else [],
                    "resourceAssessment": _resource_assessment(model, nodes, capacities),
                }
                for model in models_by_workflow[identity]
            ],
        })
    return dependencies


def load_workflow_platform(
    directory, reference, *, workflows, models, ui, worker_runtimes, composition, catalog_digest,
    fixtures_directory,
):
    raw = _pinned_json(directory, reference, CONFIG_ERROR)
    try:
        _validator().validate(raw)
    except ValidationError as error:
        raise ValueError(f"{CONFIG_ERROR} at {error.json_path}") from error
    workflow_index = {(row["workflowId"], row["version"]): row for row in workflows}
    model_index = {(row["modelId"], row["version"]): row for row in models}
    contracts = {}
    for item in raw["resultContracts"]:
        identity = _identity(item)
        if identity in contracts:
            raise ValueError(RESULT_ERROR)
        contracts[identity] = _contract(item, workflow_index)
    profiles = {}
    for item in raw["profiles"]:
        identity = _identity(item)
        if identity in profiles:
            raise ValueError("WORKFLOW_PROFILE_ID_DUPLICATE")
        profiles[identity] = _profile(directory, fixtures_directory, item, workflow_index, model_index, contracts, ui)
    if {_identity(row["resultContractRef"]) for row in profiles.values()} != contracts.keys():
        raise ValueError(RESULT_ERROR)
    return {
        "schemaVersion": "assembled-workflow-platform.v1",
        "operatorCatalogDigest": catalog_digest,
        "profiles": [profiles[key] for key in sorted(profiles)],
        "resultContracts": [contracts[key] for key in sorted(contracts)],
        "workflows": _workflow_dependencies(
            workflow_index, model_index, profiles, ui, worker_runtimes, composition,
        ),
    }


def require_immutable_platform_refs(previous_lock: dict, current: dict) -> None:
    previous = previous_lock.get("workflowPlatform")
    if previous is None:
        return
    for collection in ("profiles", "resultContracts"):
        previous_by_version = {_identity(ref): ref for ref in previous[collection]}
        for ref in current[collection]:
            old = previous_by_version.get(_identity(ref))
            if old is not None and old != ref:
                raise ValueError("WORKFLOW_PLATFORM_IDENTITY_MUTATED")
