"""Prepare a pinned v12 selection or describe its results using a local product assembly."""

import argparse
from datetime import datetime
import json
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import ValidationError

from platform_protocol_contracts.execution_v6 import DataVersionRefV2, canonical_json, sha256_bytes


def _read_platform(assembly: Path) -> dict:
    code = "WORKFLOW_PLATFORM_BUNDLE_INVALID"
    try:
        lock = json.loads((assembly / "assembly-lock.json").read_bytes())
        data = (assembly / "workflow-platform.json").read_bytes()
        document = json.loads(data)
        identity = lock["workflowPlatform"]
        if (
            document["schemaVersion"] != "assembled-workflow-platform.v1"
            or sha256_bytes(data) != identity["checksum"]
            or any(
                [item["ref"] for item in document[collection]] != identity[collection]
                for collection in ("profiles", "resultContracts")
            )
        ):
            raise ValueError(code)
    except (OSError, KeyError, TypeError, ValueError, UnicodeError) as error:
        raise ValueError(code) from error
    return document


def _profile(platform: dict, ref: dict) -> dict:
    for profile in platform["profiles"]:
        if profile["ref"] == ref:
            return profile
    raise ValueError("WORKFLOW_PROFILE_NOT_FOUND")


def _execution_identity(profile: dict) -> dict:
    model = profile["modelRef"]
    workflow = profile["workflowRef"]
    return {
        "modelId": model["id"], "modelVersion": model["version"], "modelChecksum": model["checksum"],
        "workflowId": workflow["id"], "workflowVersion": workflow["version"],
        "workflowChecksum": workflow["checksum"],
    }


def _validate_arguments(profile: dict, arguments: dict, error_code: str) -> None:
    try:
        canonical_json(arguments)
        Draft202012Validator(profile["inputSchema"], format_checker=FormatChecker()).validate(arguments)
        intervals = {}
        for requirement in profile["semanticRequirements"]:
            if requirement["kind"] != "timeRange":
                continue
            name = requirement["startParameter"]
            start = datetime.fromisoformat(arguments[name].strip())
            end = datetime.fromisoformat(arguments[requirement["endParameter"]].strip())
            if start > end:
                raise ValueError(error_code)
            intervals[name] = (start, end)
        for left, right in profile["nonOverlappingTimeRanges"]:
            left_start, left_end = intervals[left]
            right_start, right_end = intervals[right]
            if not (left_end < right_start or right_end < left_start):
                raise ValueError(error_code)
    except (ValidationError, ValueError, TypeError, KeyError, AttributeError) as error:
        raise ValueError(error_code) from error


def select_workflow_profile(
    assembly: Path, profile_ref: dict, *, role: str, inputs: dict, parameters: dict,
) -> dict:
    """Build the v12 message fragment. Kernel owns identity and execution checks."""
    profile = _profile(_read_platform(assembly), profile_ref)
    if role not in profile["visibleRoles"]:
        raise ValueError("WORKFLOW_PROFILE_ROLE_INVALID")
    roles = profile["inputRoles"]
    if not isinstance(inputs, dict) or inputs.keys() != roles.keys():
        raise ValueError("WORKFLOW_PROFILE_INPUT_INVALID")
    references, bindings, arguments, indexes = [], {}, {}, {}
    for name, specification in sorted(roles.items()):
        try:
            ref = DataVersionRefV2.from_dict(inputs[name])
        except (TypeError, ValueError) as error:
            raise ValueError("WORKFLOW_PROFILE_INPUT_INVALID") from error
        if {"mediaType": ref.media_type, "format": ref.format_id} not in specification["formatPairs"]:
            raise ValueError("WORKFLOW_PROFILE_INPUT_INVALID")
        exact = ref.as_dict()
        identity = (ref.version_id, ref.object_id)
        index = indexes.setdefault(identity, len(references))
        if index == len(references):
            references.append(exact)
        elif references[index] != exact:
            raise ValueError("WORKFLOW_PROFILE_INPUT_INVALID")
        parameter = specification["modelParameter"]
        bindings[parameter] = index
        arguments[parameter] = ref.version_id
    if not isinstance(parameters, dict) or not parameters.keys() <= set(profile["adjustableParameters"]):
        raise ValueError("WORKFLOW_PROFILE_PARAMETER_INVALID")
    resolved = {**profile["modelDefaults"], **profile["fixedParameters"], **parameters}
    arguments.update(resolved)
    _validate_arguments(profile, arguments, "WORKFLOW_PROFILE_PARAMETER_INVALID")
    return {
        "workflowSelection": {
            **_execution_identity(profile), "inputBindings": bindings, "parameters": resolved,
        },
        "inputReferences": references,
    }


def describe_workflow_result(assembly: Path, profile_ref: dict, run: dict, publication: dict) -> dict:
    """Check a snapshot's compatibility with the supplied Profile and retain its Data references."""
    platform = _read_platform(assembly)
    profile = _profile(platform, profile_ref)
    contract = next(item for item in platform["resultContracts"] if item["ref"] == profile["resultContractRef"])
    run_id = run.get("runId")
    arguments = run.get("arguments")
    if (
        not isinstance(run_id, str) or not run_id
        or publication.get("workflowRunId") != run_id
        or any(run.get(key) != value for key, value in _execution_identity(profile).items())
        or not isinstance(arguments, dict)
    ):
        raise ValueError("WORKFLOW_PROFILE_RUN_MISMATCH")
    _validate_arguments(profile, arguments, "WORKFLOW_PROFILE_RUN_MISMATCH")
    fixed = {
        name: value for name, value in profile["modelDefaults"].items()
        if name not in profile["adjustableParameters"]
    }
    fixed.update(profile["fixedParameters"])
    if any(
        name not in arguments or canonical_json(arguments[name]) != canonical_json(value)
        for name, value in fixed.items()
    ):
        raise ValueError("WORKFLOW_PROFILE_RUN_MISMATCH")
    names = {output["name"]: output for output in contract["outputs"]}
    raw_outputs = run.get("outputs")
    if not isinstance(raw_outputs, list):
        raise ValueError("WORKFLOW_RESULT_OUTPUT_INVALID")
    references = {}
    versions = set()
    for raw in raw_outputs:
        try:
            ref = DataVersionRefV2.from_dict(raw)
        except (TypeError, ValueError) as error:
            raise ValueError("WORKFLOW_RESULT_OUTPUT_INVALID") from error
        spec = names.get(ref.output_name)
        if (
            spec is None or ref.output_name in references or ref.version_id in versions
            or (ref.media_type, ref.format_id) != (spec["mediaType"], spec["format"])
        ):
            raise ValueError("WORKFLOW_RESULT_OUTPUT_INVALID")
        references[ref.output_name] = ref.as_dict()
        versions.add(ref.version_id)
    state = publication.get("state")
    if state == "PUBLISHED" and not publication.get("publicationId"):
        raise ValueError("WORKFLOW_PROFILE_RUN_MISMATCH")
    status = "NOT_PUBLISHED"
    if state == "PUBLISHED":
        status = "COMPLETE" if run.get("status") == "SUCCEEDED" and references.keys() == names.keys() else "INCOMPLETE"
    return {
        "runId": run_id, "profileRef": profile["ref"], "contractRef": contract["ref"],
        "executionStatus": run.get("status"), "publicationState": state,
        "resultStatus": status, "businessAcceptance": "NOT_ASSESSED",
        "outputs": [
            {**spec, "dataRef": references.get(name)} for name, spec in names.items()
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--assembly", type=Path, required=True, help="Path to a content-addressed product bundle")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("list", help="List exact Profile references")
    select = commands.add_parser("select", help="Build a v12 message fragment")
    results = commands.add_parser("results", help="Describe results from authorized v12 snapshots")
    for command, help_text in (
        (select, "JSON object with role, inputs (references keyed by input role), and parameters"),
        (results, "JSON object with the run and publication snapshots"),
    ):
        command.add_argument("--id", required=True)
        command.add_argument("--version", required=True)
        command.add_argument("--checksum", required=True)
        command.add_argument("--input", type=Path, required=True, help=help_text)
    args = parser.parse_args()
    try:
        if args.command == "list":
            result = [
                {"ref": profile["ref"], "label": profile["label"]}
                for profile in _read_platform(args.assembly)["profiles"]
            ]
        else:
            ref = {"id": args.id, "version": args.version, "checksum": args.checksum}
            document = json.loads(args.input.read_bytes())
            handler = select_workflow_profile if args.command == "select" else describe_workflow_result
            result = handler(args.assembly, ref, **document)
    except (OSError, ValueError, TypeError, KeyError, UnicodeError) as error:
        parser.error(str(error))
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
