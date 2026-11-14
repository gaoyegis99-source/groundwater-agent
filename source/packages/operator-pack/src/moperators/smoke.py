from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import resource
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Literal

from jsonschema import Draft202012Validator

from moperators.catalog import OperatorCatalog, OperatorDefinition, OperatorSmokeCase


class SmokeRunRefusal(ValueError):
    """A stable quick-run boundary refusal suitable for CLI automation."""

    def __init__(self, code: str, detail: str | None = None) -> None:
        self.code = code
        self.detail = detail
        super().__init__(code)


def bundled_catalog_directory() -> Path:
    return Path(__file__).with_name("catalog_data") / "operators"


def _limits(timeout_seconds: int, memory_bytes: int) -> None:
    resource.setrlimit(resource.RLIMIT_CPU, (timeout_seconds, timeout_seconds))
    resource.setrlimit(resource.RLIMIT_AS, (memory_bytes, memory_bytes))
    resource.setrlimit(resource.RLIMIT_NOFILE, (256, 256))
    resource.setrlimit(resource.RLIMIT_FSIZE, (memory_bytes, memory_bytes))


def _canonical_json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )


def _digest_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _digest_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def _write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )


def _smoke_fixture_ids(smoke: OperatorSmokeCase) -> dict[str, tuple[str, ...]]:
    result: dict[str, tuple[str, ...]] = {}
    for port, references in smoke.inputs.items():
        refs = references if isinstance(references, list) else [references]
        result[port] = tuple(reference.fixture_id for reference in refs)
    return result


def _resolved_fixture_ids(
    catalog: OperatorCatalog,
    operator: OperatorDefinition,
    smoke: OperatorSmokeCase,
    overrides: dict[str, tuple[str, ...]],
) -> dict[str, tuple[str, ...]]:
    ports = {port.name: port for port in operator.inputs}
    unknown = set(overrides) - set(ports)
    if unknown:
        raise SmokeRunRefusal("OPERATOR_INPUT_PORT_NOT_FOUND")
    resolved = _smoke_fixture_ids(smoke)
    resolved.update(overrides)
    if set(resolved) != set(ports):
        raise SmokeRunRefusal("OPERATOR_SMOKE_INPUTS_INVALID")
    for port_name, fixture_ids in resolved.items():
        port = ports[port_name]
        if not fixture_ids or (port.cardinality == "one" and len(fixture_ids) != 1):
            raise SmokeRunRefusal("OPERATOR_FIXTURE_CARDINALITY_INVALID")
        if port.max_items is not None and len(fixture_ids) > port.max_items:
            raise SmokeRunRefusal("OPERATOR_FIXTURE_CARDINALITY_INVALID")
        for fixture_id in fixture_ids:
            fixture = catalog.fixtures.get(fixture_id)
            if fixture is None:
                raise SmokeRunRefusal("OPERATOR_FIXTURE_NOT_FOUND")
            if fixture.media_type not in port.media_types or fixture.format not in port.formats:
                raise SmokeRunRefusal("OPERATOR_FIXTURE_FORMAT_INVALID")
    return resolved


def _resolved_parameters(
    operator: OperatorDefinition,
    smoke: OperatorSmokeCase,
    overrides: dict[str, Any],
) -> dict[str, Any]:
    parameters = {**smoke.parameters, **overrides}
    errors = sorted(
        Draft202012Validator(operator.parameters_schema).iter_errors(parameters),
        key=lambda error: tuple(str(item) for item in error.absolute_path),
    )
    if errors:
        first = errors[0]
        location = ".".join(str(item) for item in first.absolute_path) or "$"
        raise SmokeRunRefusal("OPERATOR_PARAMETERS_INVALID", f"{location}: {first.message}")
    return parameters


def stage_catalog_fixtures(
    catalog: OperatorCatalog,
    fixture_ids: dict[str, tuple[str, ...]],
    input_directory: Path,
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, list[dict[str, Any]]]]:
    """Verify and stage catalog fixtures for a production task request."""

    input_directory.mkdir(parents=True)
    payload: dict[str, list[dict[str, Any]]] = {}
    evidence: dict[str, list[dict[str, Any]]] = {}
    for port, identifiers in fixture_ids.items():
        payload[port] = []
        evidence[port] = []
        for index, fixture_id in enumerate(identifiers):
            fixture = catalog.fixtures[fixture_id]
            path = catalog.fixture_root / fixture.path
            if not path.is_file() or path.is_symlink():
                raise SmokeRunRefusal("OPERATOR_FIXTURE_INVALID")
            digest = _digest_file(path)
            if path.stat().st_size != fixture.size_bytes or digest != fixture.sha256:
                raise SmokeRunRefusal("OPERATOR_FIXTURE_INTEGRITY_MISMATCH")
            staged_path = input_directory / f"{port}-{index}"
            shutil.copyfile(path, staged_path)
            payload[port].append(
                {
                    "path": str(staged_path),
                    "mediaType": fixture.media_type,
                    "format": fixture.format,
                    "metadata": {},
                }
            )
            evidence[port].append(
                {
                    "fixtureId": fixture_id,
                    "mediaType": fixture.media_type,
                    "format": fixture.format,
                    "sizeBytes": fixture.size_bytes,
                    "sha256": fixture.sha256,
                }
            )
    return payload, evidence


def _runtime_environment() -> dict[str, str]:
    environment = dict(os.environ)
    package_source = str(Path(__file__).resolve().parents[1])
    inherited = environment.get("PYTHONPATH", "")
    entries = [item for item in inherited.split(os.pathsep) if item]
    environment["PYTHONPATH"] = os.pathsep.join(
        [package_source, *(item for item in entries if item != package_source)]
    )
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    return environment


def execute_operator_task(
    operator: OperatorDefinition,
    inputs: dict[str, list[dict[str, Any]]],
    parameters: dict[str, Any],
    directory: Path,
    python_executable: Path,
    *,
    task_command: Sequence[str] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Execute one operator through the production ``runtime.task`` module."""

    directory.mkdir(parents=True, exist_ok=True)
    output_directory = directory / "outputs"
    request = {
        "operatorId": operator.id,
        "operatorVersion": operator.version,
        "inputs": inputs,
        "parameters": parameters,
        "outputDirectory": str(output_directory),
    }
    request_path = directory / "request.json"
    result_path = directory / "runtime-result.json"
    request_path.write_bytes(_canonical_json(request))
    command = [str(python_executable)] if task_command is None else list(task_command)
    if not command or any(not isinstance(item, str) or not item for item in command):
        raise SmokeRunRefusal("OPERATOR_TASK_COMMAND_INVALID")
    subprocess.run(
        [
            *command,
            "-m",
            "moperators.runtime.task",
            str(request_path),
            str(result_path),
        ],
        check=True,
        timeout=operator.timeout_seconds,
        preexec_fn=(
            (lambda: _limits(operator.timeout_seconds, operator.memory_limit_bytes))
            if task_command is None
            else None
        ),
        env=_runtime_environment(),
    )
    try:
        result = json.loads(result_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise SmokeRunRefusal("OPERATOR_RUNTIME_RESULT_INVALID") from error
    if not isinstance(result, dict):
        raise SmokeRunRefusal("OPERATOR_RUNTIME_RESULT_INVALID")
    return result, request


def _execute_case(
    catalog: OperatorCatalog,
    operator: OperatorDefinition,
    fixture_ids: dict[str, tuple[str, ...]],
    parameters: dict[str, Any],
    directory: Path,
    python_executable: Path,
) -> tuple[dict[str, Any], dict[str, list[dict[str, Any]]], dict[str, Any]]:
    inputs, fixture_evidence = stage_catalog_fixtures(catalog, fixture_ids, directory / "inputs")
    result, request = execute_operator_task(
        operator, inputs, parameters, directory, python_executable
    )
    return result, fixture_evidence, request


def validate_operator_outputs(
    operator: OperatorDefinition,
    result: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    """Validate the generic output boundary declared by an operator manifest."""

    if error := result.get("error"):
        code = error.get("code") if isinstance(error, dict) else None
        raise SmokeRunRefusal(
            code if isinstance(code, str) and code else "OPERATOR_EXECUTION_FAILED"
        )
    outputs = result.get("outputs")
    contracts = {port.name: port for port in operator.outputs}
    if not isinstance(outputs, dict) or set(outputs) != set(contracts):
        raise SmokeRunRefusal("OPERATOR_OUTPUT_PORTS_INVALID")
    for port, contract in contracts.items():
        actual = outputs.get(port)
        if not isinstance(actual, dict) or actual.get("kind") != contract.kind:
            raise SmokeRunRefusal("OPERATOR_OUTPUT_KIND_INVALID")
        if contract.kind == "artifact":
            if (
                actual.get("mediaType") not in contract.media_types
                or actual.get("format") not in contract.formats
            ):
                raise SmokeRunRefusal("OPERATOR_OUTPUT_FORMAT_INVALID")
            raw_path = actual.get("path")
            path = Path(raw_path) if isinstance(raw_path, str) else None
            if path is None or not path.is_file() or path.is_symlink():
                raise SmokeRunRefusal("OPERATOR_OUTPUT_ARTIFACT_INVALID")
        elif "value" not in actual:
            raise SmokeRunRefusal("OPERATOR_OUTPUT_VALUE_MISSING")
    return outputs


def _validate_outputs(
    operator: OperatorDefinition,
    smoke: OperatorSmokeCase,
    result: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    outputs = validate_operator_outputs(operator, result)
    if set(outputs) != set(smoke.expected_outputs):
        raise SmokeRunRefusal("OPERATOR_OUTPUT_PORTS_INVALID")
    for port, expected in smoke.expected_outputs.items():
        actual = outputs.get(port)
        if not isinstance(actual, dict) or actual.get("kind") != expected.kind:
            raise SmokeRunRefusal("OPERATOR_OUTPUT_KIND_INVALID")
        if expected.media_type and actual.get("mediaType") != expected.media_type:
            raise SmokeRunRefusal("OPERATOR_OUTPUT_MEDIA_TYPE_INVALID")
        if expected.format and actual.get("format") != expected.format:
            raise SmokeRunRefusal("OPERATOR_OUTPUT_FORMAT_INVALID")
    return outputs


def published_document(value: Any, staging: Path, published: Path) -> Any:
    result = copy.deepcopy(value)

    def replace(item: Any) -> Any:
        if isinstance(item, dict):
            return {key: replace(child) for key, child in item.items()}
        if isinstance(item, list):
            return [replace(child) for child in item]
        if not isinstance(item, str):
            return item
        path = Path(item)
        if not path.is_absolute():
            return item
        try:
            relative = path.relative_to(staging)
        except ValueError:
            return item
        return str(published / relative)

    return replace(result)


def output_evidence(
    outputs: dict[str, dict[str, Any]], staging: Path, published: Path
) -> dict[str, dict[str, Any]]:
    evidence: dict[str, dict[str, Any]] = {}
    for port, actual in outputs.items():
        if actual["kind"] == "artifact":
            path = Path(actual["path"])
            relative = path.relative_to(staging)
            evidence[port] = {key: value for key, value in actual.items() if key != "path"}
            evidence[port].update(
                {
                    "path": str(published / relative),
                    "sizeBytes": path.stat().st_size,
                    "sha256": _digest_file(path),
                }
            )
        else:
            encoded = _canonical_json(actual["value"])
            evidence[port] = {key: value for key, value in actual.items() if key != "value"}
            evidence[port].update(
                {
                    "value": actual["value"],
                    "sizeBytes": len(encoded),
                    "sha256": _digest_bytes(encoded),
                }
            )
    return evidence


def run_operator_smoke(
    catalog_directory: Path,
    operator_id: str,
    operator_version: str,
    run_directory: Path,
    *,
    fixture_overrides: dict[str, tuple[str, ...]] | None = None,
    parameter_overrides: dict[str, Any] | None = None,
    python_executable: str | Path | None = None,
    runtime_evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run one catalog smoke through the production task subprocess.

    The caller owns the output root policy. This function requires a new absolute
    directory and atomically publishes replay inputs, runtime result, summary,
    and checksums only after the smoke contract passes.
    """

    catalog_directory = Path(catalog_directory)
    run_directory = Path(run_directory)
    if not run_directory.is_absolute():
        raise SmokeRunRefusal("OPERATOR_RUN_OUTPUT_NOT_ABSOLUTE")
    if run_directory.exists() or run_directory.is_symlink():
        raise SmokeRunRefusal("OPERATOR_RUN_OUTPUT_EXISTS")
    if not run_directory.parent.is_dir() or run_directory.parent.is_symlink():
        raise SmokeRunRefusal("OPERATOR_RUN_OUTPUT_PARENT_INVALID")
    python = Path(sys.executable if python_executable is None else python_executable)
    if not python.is_absolute() or not python.is_file():
        raise SmokeRunRefusal("OPERATOR_RUNTIME_PYTHON_INVALID")
    try:
        catalog = OperatorCatalog.load(catalog_directory)
    except (OSError, ValueError) as error:
        raise SmokeRunRefusal("OPERATOR_CATALOG_INVALID") from error
    operator = catalog.get(operator_id, operator_version)
    if operator is None:
        raise SmokeRunRefusal("OPERATOR_NOT_FOUND")
    if operator.runtime == "remote_http":
        raise SmokeRunRefusal("OPERATOR_RUNTIME_NOT_LOCAL")
    identity = (operator_id, operator_version)
    smoke = catalog.smoke_cases.get(identity)
    if smoke is None:
        raise SmokeRunRefusal("OPERATOR_SMOKE_NOT_FOUND")
    fixtures = _resolved_fixture_ids(catalog, operator, smoke, fixture_overrides or {})
    parameters = _resolved_parameters(operator, smoke, parameter_overrides or {})
    invocation = {
        "schemaVersion": "operator-smoke-invocation.v1",
        "catalogDigest": catalog.digest,
        "operator": {"id": operator_id, "version": operator_version},
        "fixtures": fixtures,
        "parameters": parameters,
    }
    staging = Path(
        tempfile.mkdtemp(prefix=f".{run_directory.name}.", dir=str(run_directory.parent))
    )
    try:
        try:
            runtime_result, fixture_evidence, request = _execute_case(
                catalog, operator, fixtures, parameters, staging, python
            )
        except (OSError, subprocess.SubprocessError) as error:
            raise SmokeRunRefusal("OPERATOR_PROCESS_FAILED") from error
        outputs = _validate_outputs(operator, smoke, runtime_result)
        published_request = published_document(request, staging, run_directory)
        published_result = published_document(runtime_result, staging, run_directory)
        outputs_evidence = output_evidence(outputs, staging, run_directory)
        summary = {
            "schemaVersion": "operator-smoke-run.v1",
            "status": "PASS",
            "operator": {
                "id": operator.id,
                "version": operator.version,
                "runtime": operator.runtime,
                "implementationRevision": operator.implementation_revision,
            },
            "catalogDigest": catalog.digest,
            "manifestDigest": catalog.manifest_digests[identity],
            "smokeDigest": _digest_bytes(_canonical_json(smoke.model_dump(by_alias=True))),
            "invocationSha256": _digest_bytes(_canonical_json(invocation)),
            "fixtures": fixture_evidence,
            "parameters": parameters,
            "runtime": runtime_evidence or {},
            "runDirectory": str(run_directory),
            "outputs": outputs_evidence,
        }
        _write_json(staging / "request.json", published_request)
        _write_json(staging / "runtime-result.json", published_result)
        summary_path = staging / "summary.json"
        _write_json(summary_path, summary)
        summary_sha256 = _digest_file(summary_path)
        staging.rename(run_directory)
        return {
            "schemaVersion": "operator-smoke-run-result.v1",
            "status": "PASS",
            "runDirectory": str(run_directory),
            "summaryPath": str(run_directory / "summary.json"),
            "summarySha256": summary_sha256,
            "operator": summary["operator"],
            "outputs": outputs_evidence,
        }
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def run_smokes(
    catalog_directory: Path, runtime: Literal["native", "qgis", "remote_http"]
) -> tuple[str, ...]:
    catalog = OperatorCatalog.load(catalog_directory)
    selected = tuple(item for item in catalog.operators if item.runtime == runtime)
    completed: list[str] = []
    with tempfile.TemporaryDirectory(prefix=f"operator-{runtime}-smoke-") as temporary:
        root = Path(temporary)
        for operator in selected:
            identity = (operator.id, operator.version)
            smoke = catalog.smoke_cases[identity]
            if runtime == "remote_http":
                if set(smoke.inputs) != {port.name for port in operator.inputs}:
                    raise ValueError(f"smoke input ports mismatch: {operator.id}")
                if set(smoke.expected_outputs) != {port.name for port in operator.outputs}:
                    raise ValueError(f"smoke output ports mismatch: {operator.id}")
                completed.append(f"{operator.id}@{operator.version}")
                continue
            try:
                result, _fixture_evidence, _request = _execute_case(
                    catalog,
                    operator,
                    _smoke_fixture_ids(smoke),
                    smoke.parameters,
                    root / f"{operator.id}@{operator.version}",
                    Path(sys.executable),
                )
                _validate_outputs(operator, smoke, result)
            except SmokeRunRefusal as error:
                raise ValueError(f"smoke execution failed: {operator.id}:{error.code}") from error
            completed.append(f"{operator.id}@{operator.version}")
    return tuple(completed)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runtime", choices=("native", "qgis", "remote_http"), required=True)
    args = parser.parse_args()
    completed = run_smokes(bundled_catalog_directory(), args.runtime)
    print(f"{args.runtime} operator smokes PASS ({len(completed)})")


if __name__ == "__main__":
    main()
