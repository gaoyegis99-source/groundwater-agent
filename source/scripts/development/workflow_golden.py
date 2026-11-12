from __future__ import annotations

import copy
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Sequence

from jsonschema import Draft202012Validator

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
for _source_directory in (
    REPOSITORY_ROOT / "packages/operator-pack/src",
    REPOSITORY_ROOT / "packages/workflow-compiler/src",
):
    if str(_source_directory) not in sys.path:
        sys.path.insert(0, str(_source_directory))

from moperators.catalog import OperatorCatalog, OperatorDefinition  # noqa: E402
from moperators.smoke import (  # noqa: E402
    SmokeRunRefusal,
    execute_operator_task,
    output_evidence,
    published_document,
    stage_catalog_fixtures,
    validate_operator_outputs,
)
from platform_workflow_compiler import compile_workflow  # noqa: E402
from platform_workflow_compiler.validation import WorkflowValidationError  # noqa: E402
from scripts.development.product_selection import (  # noqa: E402
    ProductSelectionRefusal,
    load_selected_product,
    resolve_selected_workflow,
)


class WorkflowGoldenRefusal(ValueError):  # noqa: N818 - stable CLI refusal family
    """Stable workflow-golden boundary refusal suitable for CLI automation."""

    def __init__(self, code: str, detail: str | None = None) -> None:
        self.code = code
        self.detail = detail
        super().__init__(code)


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _digest_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _digest_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return "sha256:" + digest.hexdigest()


def _write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )


def _pointer_token(value: object) -> str:
    return str(value).replace("~", "~0").replace("/", "~1")


def golden_differences(expected: Any, actual: Any) -> list[dict[str, Any]]:
    """Return deterministic field-level differences using JSON Pointer paths."""

    differences: list[dict[str, Any]] = []

    def compare(left: Any, right: Any, path: str) -> None:
        if isinstance(left, dict) and isinstance(right, dict):
            for key in sorted(set(left) | set(right)):
                child_path = f"{path}/{_pointer_token(key)}"
                if key not in right:
                    differences.append(
                        {
                            "kind": "FIELD_MISSING",
                            "path": child_path,
                            "expected": left[key],
                        }
                    )
                elif key not in left:
                    differences.append(
                        {
                            "kind": "FIELD_UNEXPECTED",
                            "path": child_path,
                            "actual": right[key],
                        }
                    )
                else:
                    compare(left[key], right[key], child_path)
            return
        if isinstance(left, list) and isinstance(right, list):
            shared = min(len(left), len(right))
            for index in range(shared):
                compare(left[index], right[index], f"{path}/{index}")
            for index in range(shared, len(left)):
                differences.append(
                    {
                        "kind": "ITEM_MISSING",
                        "path": f"{path}/{index}",
                        "expected": left[index],
                    }
                )
            for index in range(shared, len(right)):
                differences.append(
                    {
                        "kind": "ITEM_UNEXPECTED",
                        "path": f"{path}/{index}",
                        "actual": right[index],
                    }
                )
            return
        if type(left) is not type(right):
            differences.append(
                {
                    "kind": "TYPE_CHANGED",
                    "path": path or "",
                    "expected": left,
                    "actual": right,
                }
            )
        elif left != right:
            differences.append(
                {
                    "kind": "VALUE_CHANGED",
                    "path": path or "",
                    "expected": left,
                    "actual": right,
                }
            )

    compare(expected, actual, "")
    return differences


def topological_node_ids(nodes: list[dict[str, Any]]) -> tuple[str, ...]:
    """Return stable dependency order for already compiler-validated nodes."""

    by_id = {node["nodeId"]: node for node in nodes}
    if len(by_id) != len(nodes):
        raise WorkflowGoldenRefusal("WORKFLOW_GOLDEN_GRAPH_INVALID")
    pending: dict[str, set[str]] = {}
    for node_id, node in by_id.items():
        dependencies: set[str] = set()
        for binding in node.get("inputs", {}).values():
            if isinstance(binding, dict) and isinstance(
                binding.get("nodeOutput"), dict
            ):
                upstream = binding["nodeOutput"].get("nodeId")
                if isinstance(upstream, str):
                    dependencies.add(upstream)
        if not dependencies <= set(by_id):
            raise WorkflowGoldenRefusal("WORKFLOW_GOLDEN_GRAPH_INVALID")
        pending[node_id] = dependencies
    ordered: list[str] = []
    resolved: set[str] = set()
    while pending:
        ready = sorted(
            node_id
            for node_id, dependencies in pending.items()
            if dependencies <= resolved
        )
        if not ready:
            raise WorkflowGoldenRefusal("WORKFLOW_GOLDEN_GRAPH_INVALID")
        for node_id in ready:
            ordered.append(node_id)
            resolved.add(node_id)
            del pending[node_id]
    return tuple(ordered)


def _read_object(path: Path, code: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise WorkflowGoldenRefusal(code) from error
    if not isinstance(value, dict):
        raise WorkflowGoldenRefusal(code)
    return value


def _load_case(
    repository_root: Path,
    product_manifest_path: Path,
    case_file: Path,
    baseline_file: Path,
    case_id: str,
) -> tuple[dict[str, Any], dict[str, Any], Path, dict[str, Any], Path]:
    cases = _read_object(case_file, "WORKFLOW_GOLDEN_CASES_INVALID")
    if cases.get("schemaVersion") != "workflow-golden-cases.v2" or not isinstance(
        cases.get("cases"), dict
    ):
        raise WorkflowGoldenRefusal("WORKFLOW_GOLDEN_CASES_INVALID")
    case = cases["cases"].get(case_id)
    if not isinstance(case, dict):
        raise WorkflowGoldenRefusal("WORKFLOW_GOLDEN_CASE_NOT_FOUND")
    if set(case) != {"workflowId", "inputs", "parameters", "keyOutputs"}:
        raise WorkflowGoldenRefusal("WORKFLOW_GOLDEN_CASE_INVALID")
    if (
        not isinstance(case["inputs"], dict)
        or not isinstance(case["parameters"], dict)
        or not isinstance(case["keyOutputs"], list)
        or not case["keyOutputs"]
        or any(not isinstance(item, str) or not item for item in case["keyOutputs"])
        or len(case["keyOutputs"]) != len(set(case["keyOutputs"]))
    ):
        raise WorkflowGoldenRefusal("WORKFLOW_GOLDEN_CASE_INVALID")
    try:
        selected = load_selected_product(repository_root, product_manifest_path)
        workflow, workflow_path = resolve_selected_workflow(
            selected, case["workflowId"]
        )
    except ProductSelectionRefusal as error:
        raise WorkflowGoldenRefusal(error.code, error.detail) from error

    baselines = _read_object(baseline_file, "WORKFLOW_GOLDEN_BASELINES_INVALID")
    if baselines.get(
        "schemaVersion"
    ) != "workflow-golden-baselines.v1" or not isinstance(baselines.get("cases"), dict):
        raise WorkflowGoldenRefusal("WORKFLOW_GOLDEN_BASELINES_INVALID")
    baseline_reference = baselines["cases"].get(case_id)
    if (
        not isinstance(baseline_reference, str)
        or not baseline_reference
        or Path(baseline_reference).is_absolute()
    ):
        raise WorkflowGoldenRefusal("WORKFLOW_GOLDEN_BASELINE_NOT_FOUND")
    baseline_path = (baseline_file.parent / baseline_reference).resolve()
    if (
        not baseline_path.is_relative_to(repository_root)
        or not baseline_path.is_file()
        or baseline_path.is_symlink()
    ):
        raise WorkflowGoldenRefusal("WORKFLOW_GOLDEN_BASELINE_NOT_FOUND")
    baseline = _read_object(baseline_path, "WORKFLOW_GOLDEN_BASELINE_INVALID")
    if baseline.get("schemaVersion") != "workflow-golden-snapshot.v1":
        raise WorkflowGoldenRefusal("WORKFLOW_GOLDEN_BASELINE_INVALID")
    return case, workflow, workflow_path, baseline, baseline_path


def _catalog_projection(
    catalog: OperatorCatalog,
) -> dict[tuple[str, str], dict[str, Any]]:
    return {
        (operator.id, operator.version): operator.model_dump(by_alias=True)
        for operator in catalog.operators
    }


def _validate_invocation(
    catalog: OperatorCatalog,
    workflow: dict[str, Any],
    compiled: dict[str, Any],
    case: dict[str, Any],
) -> tuple[dict[str, tuple[str, ...]], dict[str, OperatorDefinition]]:
    input_contracts = {item["name"]: item for item in workflow.get("inputs", [])}
    if set(case["inputs"]) != set(input_contracts):
        raise WorkflowGoldenRefusal("WORKFLOW_GOLDEN_INPUTS_INVALID")
    fixtures: dict[str, tuple[str, ...]] = {}
    for input_name, fixture_id in case["inputs"].items():
        if not isinstance(fixture_id, str):
            raise WorkflowGoldenRefusal("WORKFLOW_GOLDEN_INPUTS_INVALID")
        fixture = catalog.fixtures.get(fixture_id)
        if fixture is None:
            raise WorkflowGoldenRefusal("WORKFLOW_GOLDEN_FIXTURE_NOT_FOUND")
        contract = input_contracts[input_name]
        if fixture.media_type not in contract.get(
            "mediaTypes", []
        ) or fixture.format not in contract.get("formats", []):
            raise WorkflowGoldenRefusal("WORKFLOW_GOLDEN_FIXTURE_FORMAT_INVALID")
        fixtures[input_name] = (fixture_id,)
    declared_parameters = workflow.get("parameters")
    if not isinstance(declared_parameters, list) or set(case["parameters"]) != set(
        declared_parameters
    ):
        raise WorkflowGoldenRefusal("WORKFLOW_GOLDEN_PARAMETERS_INVALID")

    operators: dict[str, OperatorDefinition] = {}
    for node in compiled["nodes"]:
        operator = catalog.get(node["operatorId"], node["operatorVersion"])
        if operator is None or operator.runtime == "remote_http":
            raise WorkflowGoldenRefusal("WORKFLOW_GOLDEN_OPERATOR_INVALID")
        parameters = {
            name: case["parameters"][binding["workflowParameter"]]
            for name, binding in node["parameters"].items()
        }
        errors = sorted(
            Draft202012Validator(operator.parameters_schema).iter_errors(parameters),
            key=lambda error: tuple(str(item) for item in error.absolute_path),
        )
        if errors:
            first = errors[0]
            location = ".".join(str(item) for item in first.absolute_path) or "$"
            raise WorkflowGoldenRefusal(
                "WORKFLOW_GOLDEN_PARAMETERS_INVALID",
                f"{node['nodeId']}.{location}: {first.message}",
            )
        operators[node["nodeId"]] = operator
    return fixtures, operators


def _node_inputs(
    node: dict[str, Any],
    workflow_inputs: dict[str, list[dict[str, Any]]],
    node_outputs: dict[str, dict[str, dict[str, Any]]],
) -> dict[str, list[dict[str, Any]]]:
    resolved: dict[str, list[dict[str, Any]]] = {}
    for port, binding in node["inputs"].items():
        if "workflowInput" in binding:
            resolved[port] = copy.deepcopy(workflow_inputs[binding["workflowInput"]])
            continue
        reference = binding["nodeOutput"]
        output = node_outputs[reference["nodeId"]][reference["port"]]
        if output.get("kind") != "artifact":
            raise WorkflowGoldenRefusal("WORKFLOW_GOLDEN_BINDING_INVALID")
        resolved[port] = [
            {
                "path": output["path"],
                "mediaType": output["mediaType"],
                "format": output["format"],
                "metadata": {},
            }
        ]
    return resolved


def _node_parameters(
    node: dict[str, Any], parameters: dict[str, Any]
) -> dict[str, Any]:
    return {
        name: parameters[binding["workflowParameter"]]
        for name, binding in node["parameters"].items()
    }


def _workflow_outputs(
    workflow: dict[str, Any],
    node_outputs: dict[str, dict[str, dict[str, Any]]],
) -> dict[str, dict[str, Any]]:
    return {
        output["name"]: node_outputs[output["nodeOutput"]["nodeId"]][
            output["nodeOutput"]["port"]
        ]
        for output in workflow["outputs"]
    }


def _output_identities(
    evidence: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    return {
        name: {
            key: copy.deepcopy(value)
            for key, value in output.items()
            if key not in {"path", "value"}
        }
        for name, output in sorted(evidence.items())
    }


def _snapshot(
    case_id: str,
    workflow: dict[str, Any],
    compiled: dict[str, Any],
    catalog: OperatorCatalog,
    fixture_evidence: dict[str, list[dict[str, Any]]],
    parameters: dict[str, Any],
    outputs: dict[str, dict[str, Any]],
    evidence: dict[str, dict[str, Any]],
    key_outputs: list[str],
) -> dict[str, Any]:
    unknown = set(key_outputs) - set(outputs)
    if unknown or any(outputs[name].get("kind") != "value" for name in key_outputs):
        raise WorkflowGoldenRefusal("WORKFLOW_GOLDEN_KEY_OUTPUT_INVALID")
    return {
        "schemaVersion": "workflow-golden-snapshot.v1",
        "caseId": case_id,
        "workflow": {
            "id": workflow["workflowId"],
            "version": workflow["version"],
            "definitionChecksum": compiled["definitionChecksum"],
            "sourceSha256": _digest_bytes(_canonical_json(workflow)),
        },
        "operatorCatalogDigest": catalog.digest,
        "fixtures": fixture_evidence,
        "parametersSha256": _digest_bytes(_canonical_json(parameters)),
        "outputs": _output_identities(evidence),
        "keyValues": {
            name: copy.deepcopy(outputs[name]["value"]) for name in key_outputs
        },
    }


def run_workflow_golden(
    *,
    repository_root: Path,
    product_manifest_path: Path,
    catalog_directory: Path,
    case_file: Path,
    baseline_file: Path,
    case_id: str,
    run_directory: Path,
    python_executable: str | Path,
    runtime_evidence: dict[str, Any] | None = None,
    task_command: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Run one golden case through compiler plus production task subprocesses."""

    repository_root = Path(repository_root).resolve()
    run_directory = Path(run_directory)
    python = Path(python_executable)
    if not repository_root.is_dir():
        raise WorkflowGoldenRefusal("WORKFLOW_GOLDEN_REPOSITORY_INVALID")
    if not run_directory.is_absolute():
        raise WorkflowGoldenRefusal("WORKFLOW_GOLDEN_OUTPUT_NOT_ABSOLUTE")
    if run_directory.exists() or run_directory.is_symlink():
        raise WorkflowGoldenRefusal("WORKFLOW_GOLDEN_OUTPUT_EXISTS")
    if not run_directory.parent.is_dir() or run_directory.parent.is_symlink():
        raise WorkflowGoldenRefusal("WORKFLOW_GOLDEN_OUTPUT_PARENT_INVALID")
    if not python.is_absolute() or not python.is_file():
        raise WorkflowGoldenRefusal("WORKFLOW_GOLDEN_PYTHON_INVALID")

    case, workflow, workflow_path, baseline, baseline_path = _load_case(
        repository_root,
        Path(product_manifest_path),
        Path(case_file),
        Path(baseline_file),
        case_id,
    )
    try:
        catalog = OperatorCatalog.load(Path(catalog_directory))
        compiled = compile_workflow(workflow, _catalog_projection(catalog))
    except (OSError, ValueError, WorkflowValidationError) as error:
        raise WorkflowGoldenRefusal("WORKFLOW_GOLDEN_DEFINITION_INVALID") from error
    fixtures, operators = _validate_invocation(catalog, workflow, compiled, case)
    nodes = {node["nodeId"]: node for node in compiled["nodes"]}
    ordered_nodes = topological_node_ids(compiled["nodes"])

    staging = Path(
        tempfile.mkdtemp(
            prefix=f".{run_directory.name}.", dir=str(run_directory.parent)
        )
    )
    try:
        try:
            workflow_inputs, fixture_evidence = stage_catalog_fixtures(
                catalog, fixtures, staging / "inputs"
            )
            node_outputs: dict[str, dict[str, dict[str, Any]]] = {}
            requests: dict[str, dict[str, Any]] = {}
            runtime_results: dict[str, dict[str, Any]] = {}
            for node_id in ordered_nodes:
                node = nodes[node_id]
                operator = operators[node_id]
                result, request = execute_operator_task(
                    operator,
                    _node_inputs(node, workflow_inputs, node_outputs),
                    _node_parameters(node, case["parameters"]),
                    staging / "nodes" / node_id,
                    python,
                    task_command=task_command,
                )
                node_outputs[node_id] = validate_operator_outputs(operator, result)
                requests[node_id] = request
                runtime_results[node_id] = result
        except SmokeRunRefusal as error:
            raise WorkflowGoldenRefusal(error.code, error.detail) from error
        except (OSError, subprocess.SubprocessError) as error:
            raise WorkflowGoldenRefusal(
                "WORKFLOW_GOLDEN_NODE_PROCESS_FAILED"
            ) from error

        selected_outputs = _workflow_outputs(workflow, node_outputs)
        evidence = output_evidence(selected_outputs, staging, run_directory)
        snapshot = _snapshot(
            case_id,
            workflow,
            compiled,
            catalog,
            fixture_evidence,
            case["parameters"],
            selected_outputs,
            evidence,
            case["keyOutputs"],
        )
        differences = golden_differences(baseline, snapshot)
        status = "PASS" if not differences else "FAIL"
        node_evidence = {
            node_id: {
                "operator": {
                    "id": operators[node_id].id,
                    "version": operators[node_id].version,
                    "runtime": operators[node_id].runtime,
                    "implementationRevision": operators[
                        node_id
                    ].implementation_revision,
                },
                "requestPath": str(run_directory / "nodes" / node_id / "request.json"),
                "runtimeResultPath": str(
                    run_directory / "nodes" / node_id / "runtime-result.json"
                ),
            }
            for node_id in ordered_nodes
        }
        summary = {
            "schemaVersion": "workflow-golden-run.v1",
            "status": status,
            "caseId": case_id,
            "workflowPath": str(workflow_path.relative_to(repository_root)),
            "workflow": snapshot["workflow"],
            "operatorCatalogDigest": catalog.digest,
            "baselinePath": str(baseline_path),
            "baselineSha256": _digest_file(baseline_path),
            "fixtures": fixture_evidence,
            "parameters": case["parameters"],
            "runtime": runtime_evidence or {},
            "nodes": node_evidence,
            "runDirectory": str(run_directory),
            "outputs": evidence,
            "snapshot": snapshot,
            "differences": differences,
        }
        for node_id in ordered_nodes:
            node_directory = staging / "nodes" / node_id
            _write_json(
                node_directory / "request.json",
                published_document(requests[node_id], staging, run_directory),
            )
            _write_json(
                node_directory / "runtime-result.json",
                published_document(runtime_results[node_id], staging, run_directory),
            )
        _write_json(staging / "snapshot.json", snapshot)
        summary_path = staging / "summary.json"
        _write_json(summary_path, summary)
        summary_sha256 = _digest_file(summary_path)
        staging.rename(run_directory)
        return {
            "schemaVersion": "workflow-golden-run-result.v1",
            "status": status,
            "caseId": case_id,
            "runDirectory": str(run_directory),
            "summaryPath": str(run_directory / "summary.json"),
            "summarySha256": summary_sha256,
            "differenceCount": len(differences),
            "differences": differences,
            "workflow": snapshot["workflow"],
            "operatorCatalogDigest": catalog.digest,
        }
    finally:
        if staging.exists():
            shutil.rmtree(staging)
