from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import logging
from typing import Any

from jsonschema import ValidationError as JsonSchemaError, validate

from ..errors import AgentRunError
from ..settings import ExecutableModelDefinition
from .evidence import collect_turn_evidence
from .models import AgentRun
from .provider_arguments import normalize_model_tool_arguments, omit_unset_field_arguments


_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class PreparedAction:
    tool_call_id: str
    position: int
    tool: str
    model_id: str
    model_version: str
    model_checksum: str
    workflow_id: str
    workflow_version: str
    workflow_checksum: str
    plan_revision: int
    arguments_json: str
    schema_checksum: str
    fingerprint: str
    input_references_json: str = "{}"


@dataclass(frozen=True)
class PreparedInterruption:
    actions: tuple[PreparedAction, ...]
    state_payload: dict[str, object]
    preparations: tuple[dict[str, object], ...]


class ResultProcessingError(ValueError):
    pass


class _PreparedArgumentsMismatchError(ValueError):
    def __init__(self, paths: tuple[str, ...]):
        super().__init__("TASK_PREPARATION_REQUIRED")
        self.paths = paths


class _InputReferenceError(ValueError):
    def __init__(self, parameter: str):
        super().__init__("DATA_VERSION_REFERENCE_REQUIRED")
        self.parameter = parameter


def _argument_difference_paths(
    expected: object, actual: object, path: str = "",
) -> tuple[str, ...]:
    """Describe argument drift by JSON pointer without retaining values."""
    if isinstance(expected, dict) and isinstance(actual, dict):
        differences: list[str] = []
        for key in sorted(set(expected) | set(actual)):
            escaped = str(key).replace("~", "~0").replace("/", "~1")
            child = f"{path}/{escaped}"
            if key not in expected or key not in actual:
                differences.append(child)
            else:
                differences.extend(
                    _argument_difference_paths(expected[key], actual[key], child)
                )
        return tuple(differences)
    if isinstance(expected, list) and isinstance(actual, list):
        differences = []
        for index in range(max(len(expected), len(actual))):
            child = f"{path}/{index}"
            if index >= len(expected) or index >= len(actual):
                differences.append(child)
            else:
                differences.extend(
                    _argument_difference_paths(expected[index], actual[index], child)
                )
        return tuple(differences)
    return () if expected == actual else (path or "/",)


def _processing_diagnostics(error: Exception) -> dict[str, object]:
    if isinstance(error, JsonSchemaError):
        path = "/" + "/".join(
            str(item).replace("~", "~0").replace("/", "~1")
            for item in error.absolute_path
        )
        return {
            "reason": "MODEL_ARGUMENTS_INVALID",
            "issues": [{"path": path, "type": error.validator or "schema"}],
        }
    if isinstance(error, _PreparedArgumentsMismatchError):
        return {
            "reason": "TASK_PREPARATION_REQUIRED",
            "issues": [
                {"path": path, "type": "prepared_argument_mismatch"}
                for path in error.paths[:32]
            ],
        }
    if isinstance(error, _InputReferenceError):
        escaped = error.parameter.replace("~", "~0").replace("/", "~1")
        return {
            "reason": "DATA_VERSION_REFERENCE_REQUIRED",
            "issues": [{"path": f"/{escaped}", "type": "data_version_reference"}],
        }
    reason = str(error)
    if reason not in {
        "AGENT_MODEL_OUTPUT_INVALID",
        "AGENT_PLAN_ACTION_MISMATCH",
        "DATA_VERSION_REFERENCE_REQUIRED",
        "TASK_PREPARATION_REQUIRED",
    }:
        reason = "AGENT_MODEL_OUTPUT_INVALID"
    return {"reason": reason}


def _select_prepared_arguments(
    arguments: dict[str, object],
    prepared: list[tuple[dict[str, object], str]],
    *,
    provider: str,
) -> tuple[int, dict[str, object], str]:
    """Bind an approval action to the READY preparation that authorized it.

    MiniMax can rewrite a few fields while reproducing a very large executable
    tool call.  When this turn contains exactly one READY preparation for the
    target, that validated snapshot is unambiguous and remains the authority for
    confirmation.  Multiple candidate preparations still require an exact call
    match so the Kernel never guesses which proposal the user should approve.
    """
    match = next(
        (
            position for position, (candidate, _selection_policy)
            in enumerate(prepared)
            if candidate == arguments
        ),
        None,
    )
    if match is not None:
        candidate, selection_policy = prepared[match]
        return match, candidate, selection_policy
    if provider == "minimax" and len(prepared) == 1:
        candidate, selection_policy = prepared[0]
        _LOGGER.info(
            "bound MiniMax executable-model call to its single READY preparation"
        )
        return 0, candidate, selection_policy
    candidates = [
        _argument_difference_paths(candidate, arguments)
        for candidate, _selection_policy in prepared
    ]
    paths = min(candidates, key=len) if candidates else ("/",)
    raise _PreparedArgumentsMismatchError(paths)


def prepare_interruption(
    run: AgentRun,
    result: Any,
    models_by_tool: dict[str, ExecutableModelDefinition],
    *,
    provider: str = "deepseek",
    explicit_model_tool_request: object = None,
) -> PreparedInterruption:
    plan = run.plans[-1] if run.plans else None
    if plan is None:
        raise ResultProcessingError("AGENT_PLAN_REQUIRED")
    planned_tools = [step.tool for step in plan.steps if step.tool in models_by_tool]
    if (any(action.plan_revision == plan.revision for action in run.actions)
            or [item.name for item in result.interruptions] != planned_tools):
        raise ResultProcessingError("AGENT_PLAN_ACTION_MISMATCH")
    actions = []
    preparations = []
    try:
        evidence = collect_turn_evidence(result.to_input_list())
        prepared_arguments = {
            target_id: [
                (dict(arguments), selection_policy)
                for arguments, selection_policy in candidates
            ]
            for target_id, candidates in evidence.prepared_arguments.items()
        }
        explicitly_prepared_targets = set(evidence.prepared_targets)
        for index, interruption in enumerate(result.interruptions):
            tool_name = interruption.name
            call_id = interruption.call_id
            arguments = interruption.arguments
            if (
                tool_name not in planned_tools
                or not isinstance(call_id, str)
                or not call_id
                or not isinstance(arguments, str)
            ):
                raise ValueError("AGENT_PLAN_ACTION_MISMATCH")
            model = models_by_tool[tool_name]
            supplied = json.loads(arguments)
            if not isinstance(supplied, dict):
                raise ValueError("AGENT_MODEL_OUTPUT_INVALID")
            supplied = normalize_model_tool_arguments(
                supplied, model.input_schema, provider=provider,
            )
            supplied = omit_unset_field_arguments(supplied, model, explicit_model_tool_request)
            arguments = {**model.default_parameters, **supplied}
            selection_policy = "PINNED"
            explicitly_prepared = prepared_arguments.get(model.model_id, [])
            if model.model_id in explicitly_prepared_targets:
                match, arguments, selection_policy = _select_prepared_arguments(
                    arguments, explicitly_prepared, provider=provider,
                )
                explicitly_prepared.pop(match)
            validate(arguments, model.input_schema)
            references = {}
            for parameter in model.data_input_parameters:
                version_id = arguments.get(parameter)
                if not isinstance(version_id, str) or version_id not in evidence.data_versions:
                    raise _InputReferenceError(parameter)
                references[parameter] = evidence.data_versions[version_id]
            canonical = json.dumps(
                arguments,
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            canonical.encode("utf-8")
            fingerprint = hashlib.sha256(
                f"{run.agent_run_id}:{tool_name}:{canonical}".encode()
            ).hexdigest()
            actions.append(
                PreparedAction(
                    tool_call_id=call_id,
                    position=len(run.actions) + index,
                    tool=tool_name,
                    model_id=model.model_id,
                    model_version=model.model_version,
                    model_checksum=model.model_checksum,
                    workflow_id=model.workflow_id,
                    workflow_version=model.workflow_version,
                    workflow_checksum=model.workflow_checksum,
                    plan_revision=plan.revision,
                    arguments_json=canonical,
                    input_references_json=json.dumps(
                        {
                            name: reference.as_storage_dict()
                            for name, reference in references.items()
                        },
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                    schema_checksum=model.input_schema_checksum,
                    fingerprint=fingerprint,
                )
            )
            preparations.append({
                "target_kind": "EXECUTABLE_MODEL",
                "target_id": model.model_id,
                "target_version": model.model_version,
                "selection_policy": selection_policy,
                "status": "READY",
                "inputs": {
                    name: reference.as_storage_dict()
                    for name, reference in references.items()
                },
                "knowledge_refs": [
                    {
                        "version_id": version_id,
                        "unit_id": unit_id,
                        "checksum": checksum,
                    }
                    for version_id, unit_id, checksum in sorted(evidence.knowledge)
                ],
                "parameters": {
                    name: value for name, value in arguments.items()
                    if name not in model.data_input_parameters
                },
                "parameter_sources": {
                    name: "PROPOSED" if name in supplied else "MODEL_DEFAULT"
                    for name in arguments
                    if name not in model.data_input_parameters
                },
                "checks": [
                    {
                        "kind": "DATA_IDENTITY",
                        "input": name,
                        "status": "PASS",
                        "version_id": reference.version_id,
                        "semantic_id": reference.semantic_id,
                    }
                    for name, reference in references.items()
                ],
                "blocking_reasons": [],
            })
        state_payload = result.to_state().to_json(strict_context=True)
        json.dumps(
            state_payload, ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8")
    except (
        KeyError,
        TypeError,
        ValueError,
        UnicodeEncodeError,
        JsonSchemaError,
    ) as error:
        raise AgentRunError(
            "AGENT_MODEL_OUTPUT_INVALID",
            diagnostics=_processing_diagnostics(error),
        ) from error
    return PreparedInterruption(
        tuple(actions), state_payload, tuple(preparations)
    )
