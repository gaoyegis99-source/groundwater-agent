from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from typing import Any, Sequence
import urllib.error
import urllib.request
import zipfile

from jsonschema import Draft202012Validator

from moperators.catalog import OperatorCatalog, OperatorDefinition
from moperators.smoke import (
    SmokeRunRefusal,
    execute_operator_task,
    output_evidence,
    published_document,
    validate_operator_outputs,
)
from platform_workflow_compiler import compile_workflow
from platform_workflow_compiler.validation import WorkflowValidationError
from scripts.development.workflow_golden import (
    _catalog_projection,
    _node_inputs,
    _node_parameters,
    _workflow_outputs,
    topological_node_ids,
)
from scripts.maintenance.project_environment import EnvironmentRefusal, docker_arguments


class RealCaseRefusal(ValueError):  # noqa: N818 - stable development CLI family
    def __init__(self, code: str, detail: str | None = None) -> None:
        self.code = code
        self.detail = detail
        super().__init__(code)


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _md5(path: Path) -> str:
    digest = hashlib.md5(usedforsecurity=False)
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )


def _read_object(path: Path, code: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RealCaseRefusal(code) from error
    if not isinstance(value, dict):
        raise RealCaseRefusal(code)
    return value


def _inside(root: Path, relative: object, code: str) -> Path:
    if not isinstance(relative, str) or not relative or Path(relative).is_absolute():
        raise RealCaseRefusal(code)
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()):
        raise RealCaseRefusal(code)
    return path


def _verify_file(path: Path, declaration: dict[str, Any], code: str) -> None:
    if not path.is_file() or path.is_symlink():
        raise RealCaseRefusal(code)
    if path.stat().st_size != declaration.get("size"):
        raise RealCaseRefusal(code)
    expected_sha256 = declaration.get("sha256")
    if not isinstance(expected_sha256, str) or _sha256(path) != expected_sha256:
        raise RealCaseRefusal(code)
    expected_md5 = declaration.get("md5")
    if expected_md5 is not None and (
        not isinstance(expected_md5, str) or _md5(path) != expected_md5
    ):
        raise RealCaseRefusal(code)


def _download(declaration: dict[str, Any], destination: Path) -> bool:
    if destination.exists() or destination.is_symlink():
        _verify_file(destination, declaration, "REAL_CASE_SOURCE_CHECKSUM_MISMATCH")
        return False
    url = declaration.get("url")
    if not isinstance(url, str) or not url.startswith("https://"):
        raise RealCaseRefusal("REAL_CASE_SOURCE_URL_INVALID")
    destination.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.", dir=destination.parent
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        request = urllib.request.Request(
            url, headers={"User-Agent": "groundwater-v6-real-case/1.0"}
        )
        try:
            with urllib.request.urlopen(request, timeout=120) as response:  # noqa: S310
                with temporary.open("wb") as stream:
                    shutil.copyfileobj(response, stream)
        except (OSError, urllib.error.URLError) as error:
            raise RealCaseRefusal("REAL_CASE_SOURCE_DOWNLOAD_FAILED") from error
        _verify_file(temporary, declaration, "REAL_CASE_SOURCE_CHECKSUM_MISMATCH")
        temporary.replace(destination)
        return True
    finally:
        temporary.unlink(missing_ok=True)


def _safe_extract(archive: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=False)
    try:
        with zipfile.ZipFile(archive) as bundle:
            for member in bundle.infolist():
                member_path = Path(member.filename)
                mode = member.external_attr >> 16
                if (
                    member_path.is_absolute()
                    or ".." in member_path.parts
                    or (mode & 0o170000) == 0o120000
                ):
                    raise RealCaseRefusal("REAL_CASE_ARCHIVE_UNSAFE")
                target = (destination / member_path).resolve()
                if not target.is_relative_to(destination.resolve()):
                    raise RealCaseRefusal("REAL_CASE_ARCHIVE_UNSAFE")
            bundle.extractall(destination)
    except (OSError, zipfile.BadZipFile) as error:
        raise RealCaseRefusal("REAL_CASE_ARCHIVE_INVALID") from error


def _worker(python: Path, repository_root: Path, *arguments: str | Path) -> None:
    script = Path(__file__).with_name("real_case_worker.py")
    product_root = (repository_root / "products").resolve()
    if (
        not script.resolve().is_relative_to(product_root)
        or not script.is_file()
        or script.is_symlink()
    ):
        raise RealCaseRefusal("REAL_CASE_PREPARATION_WORKER_INVALID")
    try:
        subprocess.run(
            [str(python), str(script), *(str(item) for item in arguments)],
            check=True,
            timeout=1800,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise RealCaseRefusal("REAL_CASE_PREPARATION_FAILED") from error


def _gdal(
    image_id: str,
    workspace: Path,
    executable: str,
    arguments: Sequence[str | Path],
) -> None:
    if (
        not workspace.is_dir()
        or workspace.is_symlink()
        or any(character in str(workspace) for character in (",", "\n", "\r"))
    ):
        raise RealCaseRefusal("REAL_CASE_PREPARATION_PATH_INVALID")
    try:
        command = docker_arguments(
            [
                "run",
                "--rm",
                "--pull",
                "never",
                "--network",
                "none",
                "--read-only",
                "--cap-drop",
                "ALL",
                "--security-opt",
                "no-new-privileges",
                "--pids-limit",
                "256",
                "--memory",
                "2147483648",
                "--memory-swap",
                "2147483648",
                "--cpus",
                "2",
                "--user",
                "0:0",
                "--tmpfs",
                "/tmp:rw,nosuid,nodev,noexec,size=268435456",
                "--mount",
                f"type=bind,src={workspace},dst={workspace}",
                "--workdir",
                str(workspace),
                "--entrypoint",
                executable,
                image_id,
                *(str(item) for item in arguments),
            ]
        )
        subprocess.run(command, check=True, timeout=600)
    except (EnvironmentRefusal, OSError, subprocess.SubprocessError) as error:
        raise RealCaseRefusal("REAL_CASE_GDAL_PREPARATION_FAILED") from error


def _load_manifest(path: Path) -> dict[str, Any]:
    manifest = _read_object(path, "REAL_CASE_MANIFEST_INVALID")
    if (
        manifest.get("schemaVersion") != "real-data-cases.v1"
        or not isinstance(manifest.get("sourceGroups"), dict)
        or not isinstance(manifest.get("cases"), dict)
        or not manifest["cases"]
    ):
        raise RealCaseRefusal("REAL_CASE_MANIFEST_INVALID")
    return manifest


def _select_cases(manifest: dict[str, Any], case_id: str) -> list[str]:
    if case_id == "all":
        return sorted(manifest["cases"])
    if case_id not in manifest["cases"]:
        raise RealCaseRefusal("REAL_CASE_NOT_FOUND")
    return [case_id]


def _resolve_sources(
    manifest: dict[str, Any],
    selected_cases: list[str],
    source_root: Path,
    source_cache: Path,
) -> tuple[dict[str, dict[str, Path]], dict[str, Any]]:
    groups = {
        manifest["cases"][case_id].get("sourceGroup") for case_id in selected_cases
    }
    if None in groups or not groups <= set(manifest["sourceGroups"]):
        raise RealCaseRefusal("REAL_CASE_MANIFEST_INVALID")
    resolved: dict[str, dict[str, Path]] = {}
    evidence: dict[str, Any] = {}
    for group_id in sorted(groups):
        group = manifest["sourceGroups"][group_id]
        files = group.get("files")
        if group.get("kind") not in {"local", "download"} or not isinstance(
            files, list
        ):
            raise RealCaseRefusal("REAL_CASE_MANIFEST_INVALID")
        group_files: dict[str, Path] = {}
        file_evidence: list[dict[str, Any]] = []
        for declaration in files:
            if not isinstance(declaration, dict) or not isinstance(
                declaration.get("id"), str
            ):
                raise RealCaseRefusal("REAL_CASE_MANIFEST_INVALID")
            if group["kind"] == "local":
                path = _inside(
                    source_root,
                    declaration.get("path"),
                    "REAL_CASE_LOCAL_SOURCE_INVALID",
                )
                downloaded = False
                code = "REAL_CASE_LOCAL_SOURCE_INVALID"
            else:
                name = declaration.get("name")
                if not isinstance(name, str) or not name or Path(name).name != name:
                    raise RealCaseRefusal("REAL_CASE_MANIFEST_INVALID")
                path = source_cache / group_id / name
                downloaded = _download(declaration, path)
                code = "REAL_CASE_SOURCE_CHECKSUM_MISMATCH"
            _verify_file(path, declaration, code)
            group_files[declaration["id"]] = path
            file_evidence.append(
                {
                    "id": declaration["id"],
                    "path": str(path),
                    "size": path.stat().st_size,
                    "sha256": f"sha256:{_sha256(path)}",
                    "downloaded": downloaded,
                    "url": declaration.get("url"),
                }
            )
        resolved[group_id] = group_files
        evidence[group_id] = {
            "title": group.get("title"),
            "kind": group["kind"],
            "qualityStatus": group.get("qualityStatus"),
            "license": group.get("license"),
            "landingPage": group.get("landingPage"),
            "files": file_evidence,
        }
    return resolved, evidence


def _prepare_local(
    repository_root: Path,
    python: Path,
    source_files: dict[str, Path],
    staging: Path,
) -> dict[str, Any]:
    output = staging / "prepared/local-monitoring-trend/observations.csv"
    stats = output.parent / "preparation.json"
    _worker(
        python,
        repository_root,
        "prepare-local",
        source_files["monthly-observation"],
        output,
        stats,
    )
    return _read_object(stats, "REAL_CASE_PREPARATION_INVALID")


def _prepare_mrva(
    repository_root: Path,
    python: Path,
    image_id: str,
    source_files: dict[str, Path],
    staging: Path,
) -> dict[str, Any]:
    prepared = staging / "prepared/usgs-mrva-2024"
    points = prepared / "points-source"
    raster = prepared / "raster-source"
    prepared.mkdir(parents=True)
    _safe_extract(source_files["measured-points"], points)
    _safe_extract(source_files["potentiometric-raster"], raster)
    shapefiles = list(points.rglob("*.shp"))
    reference_rasters = list(raster.rglob("*.tif"))
    if len(shapefiles) != 1 or len(reference_rasters) != 1:
        raise RealCaseRefusal("REAL_CASE_SOURCE_CONTENT_INVALID")
    raw = prepared / "mrva-raw.csv"
    _gdal(
        image_id,
        staging,
        "ogr2ogr",
        [
            "-f",
            "CSV",
            raw,
            shapefiles[0],
            "-t_srs",
            "EPSG:5070",
            "-lco",
            "GEOMETRY=AS_XY",
            "-select",
            "SITE_BADGE,ALT_88,WLA88_2024,D2W_2024,DATE_2024,USE_2024",
            "-where",
            "USE_2024 > 0 AND WLA88_2024 IS NOT NULL",
        ],
    )
    flow = prepared / "flow-observations.csv"
    kriging = prepared / "kriging-points.csv"
    stats = prepared / "preparation.json"
    _worker(
        python,
        repository_root,
        "prepare-mrva",
        raw,
        flow,
        kriging,
        stats,
    )
    official = prepared / "official-potentiometric-head-feet.tif"
    shutil.copyfile(reference_rasters[0], official)
    result = _read_object(stats, "REAL_CASE_PREPARATION_INVALID")
    result["officialRasterSha256"] = f"sha256:{_sha256(official)}"
    _write_json(stats, result)
    return result


def _prepare_odnr(
    repository_root: Path,
    python: Path,
    source_files: dict[str, Path],
    staging: Path,
) -> dict[str, Any]:
    prepared = staging / "prepared/odnr-franklin-drastic"
    extracted = prepared / "source"
    prepared.mkdir(parents=True)
    _safe_extract(source_files["franklin-vulnerability"], extracted)
    shapefiles = list(extracted.rglob("*.shp"))
    if len(shapefiles) != 1:
        raise RealCaseRefusal("REAL_CASE_SOURCE_CONTENT_INVALID")
    shapefile = shapefiles[0]
    stats = prepared / "preparation.json"
    _worker(
        python,
        repository_root,
        "prepare-odnr-source",
        shapefile,
        prepared,
        stats,
    )
    return _read_object(stats, "REAL_CASE_PREPARATION_INVALID")


def _validate_invocation(
    catalog: OperatorCatalog,
    workflow: dict[str, Any],
    compiled: dict[str, Any],
    case: dict[str, Any],
) -> dict[str, OperatorDefinition]:
    input_contracts = {item["name"]: item for item in workflow.get("inputs", [])}
    if set(case.get("inputs", {})) != set(input_contracts):
        raise RealCaseRefusal("REAL_CASE_INPUTS_INVALID")
    for name, declaration in case["inputs"].items():
        contract = input_contracts[name]
        if (
            not isinstance(declaration, dict)
            or declaration.get("mediaType") not in contract.get("mediaTypes", [])
            or declaration.get("format") not in contract.get("formats", [])
        ):
            raise RealCaseRefusal("REAL_CASE_INPUTS_INVALID")
    if set(case.get("parameters", {})) != set(workflow.get("parameters", [])):
        raise RealCaseRefusal("REAL_CASE_PARAMETERS_INVALID")
    operators: dict[str, OperatorDefinition] = {}
    for node in compiled["nodes"]:
        operator = catalog.get(node["operatorId"], node["operatorVersion"])
        if operator is None or operator.runtime == "remote_http":
            raise RealCaseRefusal("REAL_CASE_OPERATOR_INVALID")
        parameters = _node_parameters(node, case["parameters"])
        errors = sorted(
            Draft202012Validator(operator.parameters_schema).iter_errors(parameters),
            key=lambda error: tuple(str(item) for item in error.absolute_path),
        )
        if errors:
            first = errors[0]
            location = ".".join(str(item) for item in first.absolute_path) or "$"
            raise RealCaseRefusal(
                "REAL_CASE_PARAMETERS_INVALID",
                f"{node['nodeId']}.{location}: {first.message}",
            )
        operators[node["nodeId"]] = operator
    return operators


def _execute_case(
    *,
    repository_root: Path,
    catalog_directory: Path,
    case_id: str,
    case: dict[str, Any],
    staging_root: Path,
    published_root: Path,
    python: Path,
    runtime_evidence: dict[str, Any],
    task_command: Sequence[str],
) -> dict[str, Any]:
    workflow_path = _inside(
        repository_root, case.get("workflow"), "REAL_CASE_WORKFLOW_INVALID"
    )
    if not workflow_path.is_file() or workflow_path.is_symlink():
        raise RealCaseRefusal("REAL_CASE_WORKFLOW_INVALID")
    workflow = _read_object(workflow_path, "REAL_CASE_WORKFLOW_INVALID")
    try:
        catalog = OperatorCatalog.load(catalog_directory)
        compiled = compile_workflow(workflow, _catalog_projection(catalog))
    except (OSError, ValueError, WorkflowValidationError) as error:
        raise RealCaseRefusal("REAL_CASE_DEFINITION_INVALID") from error
    operators = _validate_invocation(catalog, workflow, compiled, case)
    run_directory = staging_root / "cases" / case_id
    published_directory = published_root / "cases" / case_id
    run_directory.mkdir(parents=True)
    workflow_inputs: dict[str, list[dict[str, Any]]] = {}
    input_evidence: dict[str, Any] = {}
    for name, declaration in sorted(case["inputs"].items()):
        path = _inside(staging_root, declaration["path"], "REAL_CASE_INPUTS_INVALID")
        if not path.is_file() or path.is_symlink():
            raise RealCaseRefusal("REAL_CASE_INPUTS_INVALID")
        workflow_inputs[name] = [
            {
                "path": str(path),
                "mediaType": declaration["mediaType"],
                "format": declaration["format"],
                "metadata": {},
            }
        ]
        input_evidence[name] = {
            "path": str(published_root / path.relative_to(staging_root)),
            "mediaType": declaration["mediaType"],
            "format": declaration["format"],
            "size": path.stat().st_size,
            "sha256": f"sha256:{_sha256(path)}",
        }
    nodes = {node["nodeId"]: node for node in compiled["nodes"]}
    ordered = topological_node_ids(compiled["nodes"])
    node_outputs: dict[str, dict[str, dict[str, Any]]] = {}
    requests: dict[str, dict[str, Any]] = {}
    runtime_results: dict[str, dict[str, Any]] = {}
    try:
        for node_id in ordered:
            node = nodes[node_id]
            operator = operators[node_id]
            result, request = execute_operator_task(
                operator,
                _node_inputs(node, workflow_inputs, node_outputs),
                _node_parameters(node, case["parameters"]),
                run_directory / "nodes" / node_id,
                python,
                task_command=task_command,
            )
            node_outputs[node_id] = validate_operator_outputs(operator, result)
            requests[node_id] = request
            runtime_results[node_id] = result
    except SmokeRunRefusal as error:
        raise RealCaseRefusal(error.code, error.detail) from error
    except (OSError, subprocess.SubprocessError) as error:
        raise RealCaseRefusal("REAL_CASE_NODE_PROCESS_FAILED") from error
    selected_outputs = _workflow_outputs(workflow, node_outputs)
    evidence = output_evidence(selected_outputs, run_directory, published_directory)
    node_evidence: dict[str, Any] = {}
    for node_id in ordered:
        node_directory = run_directory / "nodes" / node_id
        _write_json(
            node_directory / "request.json",
            published_document(requests[node_id], run_directory, published_directory),
        )
        _write_json(
            node_directory / "runtime-result.json",
            published_document(
                runtime_results[node_id], run_directory, published_directory
            ),
        )
        operator = operators[node_id]
        node_evidence[node_id] = {
            "operator": {
                "id": operator.id,
                "version": operator.version,
                "runtime": operator.runtime,
                "implementationRevision": operator.implementation_revision,
            },
            "requestPath": str(
                published_directory / "nodes" / node_id / "request.json"
            ),
            "runtimeResultPath": str(
                published_directory / "nodes" / node_id / "runtime-result.json"
            ),
        }
    summary = {
        "schemaVersion": "real-case-workflow-run.v1",
        "status": "PASS",
        "caseId": case_id,
        "sourceGroup": case["sourceGroup"],
        "workflowPath": str(workflow_path.relative_to(repository_root)),
        "workflow": {
            "id": workflow["workflowId"],
            "version": workflow["version"],
            "definitionChecksum": compiled["definitionChecksum"],
            "sourceSha256": "sha256:"
            + hashlib.sha256(_canonical_json(workflow)).hexdigest(),
        },
        "operatorCatalogDigest": catalog.digest,
        "inputs": input_evidence,
        "parameters": case["parameters"],
        "scenarioAssumptions": case.get("scenarioAssumptions"),
        "classificationPolicy": case.get("classificationPolicy"),
        "runtime": runtime_evidence,
        "nodes": node_evidence,
        "runDirectory": str(published_directory),
        "outputs": evidence,
    }
    _write_json(run_directory / "summary.json", summary)
    return {
        "status": "PASS",
        "summaryPath": str(published_directory / "summary.json"),
        "summarySha256": f"sha256:{_sha256(run_directory / 'summary.json')}",
        "outputs": selected_outputs,
    }


def run_real_cases(
    *,
    repository_root: Path,
    catalog_directory: Path,
    manifest_path: Path,
    case_id: str,
    source_root: Path,
    source_cache: Path,
    run_directory: Path,
    python_executable: str | Path,
    image_id: str,
    runtime_evidence: dict[str, Any],
    task_command: Sequence[str],
) -> dict[str, Any]:
    repository_root = Path(repository_root).resolve()
    source_root = Path(source_root).resolve()
    run_directory = Path(run_directory)
    python = Path(python_executable)
    if (
        not repository_root.is_dir()
        or not source_root.is_dir()
        or not python.is_absolute()
        or not python.is_file()
    ):
        raise RealCaseRefusal("REAL_CASE_ENVIRONMENT_INVALID")
    if (
        not run_directory.is_absolute()
        or run_directory.exists()
        or run_directory.is_symlink()
        or not run_directory.parent.is_dir()
        or run_directory.parent.is_symlink()
    ):
        raise RealCaseRefusal("REAL_CASE_OUTPUT_INVALID")
    manifest = _load_manifest(Path(manifest_path))
    selected = _select_cases(manifest, case_id)
    source_files, source_evidence = _resolve_sources(
        manifest, selected, source_root, Path(source_cache)
    )
    staging = Path(
        tempfile.mkdtemp(prefix=f".{run_directory.name}.", dir=run_directory.parent)
    )
    try:
        selected_groups = {manifest["cases"][item]["sourceGroup"] for item in selected}
        preparation: dict[str, Any] = {}
        if "local-groundwater-v2" in selected_groups:
            preparation["local-groundwater-v2"] = _prepare_local(
                repository_root,
                python,
                source_files["local-groundwater-v2"],
                staging,
            )
        if "usgs-mrva-2024" in selected_groups:
            preparation["usgs-mrva-2024"] = _prepare_mrva(
                repository_root,
                python,
                image_id,
                source_files["usgs-mrva-2024"],
                staging,
            )
        if "odnr-franklin-drastic" in selected_groups:
            preparation["odnr-franklin-drastic"] = _prepare_odnr(
                repository_root,
                python,
                source_files["odnr-franklin-drastic"],
                staging,
            )
        results: dict[str, Any] = {}
        validations: dict[str, Any] = {}
        for selected_id in selected:
            case = manifest["cases"][selected_id]
            result = _execute_case(
                repository_root=repository_root,
                catalog_directory=Path(catalog_directory),
                case_id=selected_id,
                case=case,
                staging_root=staging,
                published_root=run_directory,
                python=python,
                runtime_evidence=runtime_evidence,
                task_command=task_command,
            )
            validation = case.get("validation")
            if validation == "official-potentiometric-raster":
                validation_path = staging / "cases" / selected_id / "validation.json"
                _worker(
                    python,
                    repository_root,
                    "validate-mrva",
                    result["outputs"]["kriging-prediction"]["path"],
                    staging
                    / "prepared/usgs-mrva-2024/official-potentiometric-head-feet.tif",
                    validation_path,
                )
                validations[selected_id] = _read_object(
                    validation_path, "REAL_CASE_VALIDATION_INVALID"
                )
                validations[selected_id]["path"] = str(
                    run_directory / "cases" / selected_id / "validation.json"
                )
            elif validation == "published-gv-index":
                validation_path = staging / "cases" / selected_id / "validation.json"
                _worker(
                    python,
                    repository_root,
                    "validate-drastic",
                    result["outputs"]["drastic-index"]["path"],
                    staging / "prepared/odnr-franklin-drastic/published-gv-index.tif",
                    validation_path,
                )
                validations[selected_id] = _read_object(
                    validation_path, "REAL_CASE_VALIDATION_INVALID"
                )
                validations[selected_id]["path"] = str(
                    run_directory / "cases" / selected_id / "validation.json"
                )
            results[selected_id] = {
                key: value for key, value in result.items() if key != "outputs"
            }
        summary = {
            "schemaVersion": "real-case-run.v1",
            "status": "PASS",
            "selectedCases": selected,
            "manifestPath": str(manifest_path),
            "manifestSha256": f"sha256:{_sha256(Path(manifest_path))}",
            "sourceRoot": str(source_root),
            "sourceGroups": source_evidence,
            "preparation": preparation,
            "cases": results,
            "validations": validations,
            "runtime": runtime_evidence,
            "defaultRuntimeMutation": "NONE",
            "runDirectory": str(run_directory),
        }
        _write_json(staging / "summary.json", summary)
        summary_sha256 = f"sha256:{_sha256(staging / 'summary.json')}"
        staging.rename(run_directory)
        return {
            "schemaVersion": "real-case-run-result.v1",
            "status": "PASS",
            "selectedCases": selected,
            "runDirectory": str(run_directory),
            "summaryPath": str(run_directory / "summary.json"),
            "summarySha256": summary_sha256,
            "validations": validations,
        }
    finally:
        if staging.exists():
            shutil.rmtree(staging)
