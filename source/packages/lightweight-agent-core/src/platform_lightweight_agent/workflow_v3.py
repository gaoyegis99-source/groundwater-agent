"""Optional AnyIO execution core for v3 and its explicit v4 evolution.

The portable specification and the package root remain dependency free.  This
module imports AnyIO, jsonschema, and referencing only after an application
explicitly uses the v3 workflow surface.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import time
from collections import OrderedDict
from collections.abc import Callable, Mapping, Sequence
from contextlib import AsyncExitStack
from dataclasses import dataclass
from functools import lru_cache
from threading import RLock
from typing import Any
from uuid import uuid4

from .engine import (
    _action_id,
    _exception_code,
    _journal_events,
    _journal_get,
    _journal_status,
)
from .journal import (
    BACKUP_RECOVERY_STATUS,
    InMemoryRunJournal,
    RunConflictError,
    RunJournalBusyError,
    RunSnapshot,
    SQLiteRunJournal,
    _CellCommitHead,
    _CellDeltaUnavailableError,
)
from .persistent_state import CellStateEdit, PersistentCellState
from .runtime_contracts import (
    ActionAuthorization,
    AgentContext,
    AgentDriverFailure,
    CancellationToken,
    ToolExecutionFailedError,
    ToolExecutionRejectedError,
    ToolExecutionRequest,
    ToolExecutionResult,
    ToolExecutionStoppedError,
    ToolExecutionUncertainError,
    WorkflowRunError,
)
from .runtime_services import WorkflowRuntimePort
from .state_edits import ExplicitStateMutation, StateEdit
from .spec import (
    ASYNC_SCHEMA_VERSIONS,
    V4_SCHEMA_VERSION,
    AgentBudget,
    AgentDecision,
    AgentToolSpec,
    ApprovalDecision,
    JsonValue,
    StepKind,
    StepSpec,
    ToolReference,
    ValueBinding,
    WorkflowSpec,
    WorkflowValidationError,
    canonical_json,
    contains_secret_name,
    json_copy,
)
from .workflow_transitions import (
    classify_action_closure,
    classify_action_settlement,
    classify_action_uncertainty,
    merge_cancel_intent,
)

_TERMINAL = frozenset({"SUCCEEDED", "BLOCKED", "CANCELLED"})
_UNKNOWN_ACTION_STATES = frozenset({"DISPATCHED", "RECONCILING"})
_MISSING = object()
_EXECUTION_LEASE_SECONDS = 30.0
_EXECUTION_HEARTBEAT_SECONDS = 5.0
_CONTROL_POLL_SECONDS = 0.05
_DEFAULT_RECOVERY_QUERY_TIMEOUT_SECONDS = 5.0
_DEFAULT_RECOVERY_ROUND_TIMEOUT_SECONDS = 15.0
_RECOVERY_RETRY_SECONDS = 5.0
_RECOVERY_UNSUPPORTED_RETRY_SECONDS = 300.0
_PREFLIGHT_CACHE_SIZE = 128
_PREFLIGHT_CACHE_LOCK = RLock()
_PREFLIGHT_COMPILER_VERSION = "v6-preflight-v2"
_PREFLIGHT_CACHE: OrderedDict[tuple[object, ...], dict[str, Any]] = OrderedDict()
_PREFLIGHT_CACHE_HITS = 0
_PREFLIGHT_CACHE_MISSES = 0


class _TransitionAbortedError(RuntimeError):
    """An atomic transition lost a race to a newer persisted intent."""


class _WaitingDecisionConsumedError(RuntimeError):
    """An approval was decided while an executor was settling its branch."""


class _ModelRequestCancelledError(RuntimeError):
    """A queued or active model request observed durable cancellation."""


class _RecoveryQueryCancelledError(RuntimeError):
    """A result-query was cooperatively cancelled before it produced a fact."""


@dataclass(frozen=True)
class _RecoveryObservation:
    """A bounded, credential-free observation from one recovery query."""

    kind: str
    query_attempted: bool
    error_code: str | None = None
    result: ToolExecutionResult | None = None


def _recovery_limits(workflow: WorkflowSpec) -> tuple[float, float]:
    """Return durable override values or conservative recovery defaults."""

    policy = workflow.execution_policy
    assert policy is not None
    query_timeout = (
        policy.recovery_query_timeout_seconds
        if policy.recovery_query_timeout_seconds is not None
        else _DEFAULT_RECOVERY_QUERY_TIMEOUT_SECONDS
    )
    round_timeout = (
        policy.recovery_round_timeout_seconds
        if policy.recovery_round_timeout_seconds is not None
        else _DEFAULT_RECOVERY_ROUND_TIMEOUT_SECONDS
    )
    return float(query_timeout), float(round_timeout)


def _optional_dependencies(
    schema_version: str | None = None,
) -> tuple[Any, Any, Any, Any]:
    try:
        import anyio
        from jsonschema.validators import validator_for
        from referencing import Registry, Resource

        if schema_version == V4_SCHEMA_VERSION:
            import immutables  # noqa: F401  # fail before creating a durable run
    except ImportError as error:
        raise RuntimeError(
            "Install platform-lightweight-agent-core[workflow] to use v3/v4 workflows"
        ) from error
    return anyio, validator_for, Registry, Resource


def _compile_schema(schema: Mapping[str, JsonValue]) -> Any:
    # Many nodes in one workflow share an identical strict contract. Compile
    # that JSON Schema once by value; the workflow-level preflight cache still
    # owns visibility and binding checks for each distinct definition.
    return _compile_schema_json(canonical_json(schema))


@lru_cache(maxsize=128)
def _compile_schema_json(serialized: str) -> Any:
    _, validator_for, registry_type, resource_type = _optional_dependencies()
    detached = json.loads(serialized)
    validator_type = validator_for(detached)
    validator_type.check_schema(detached)
    resolver = (
        registry_type()
        .with_resource(
            "urn:platform-agent:v3-schema", resource_type.from_contents(detached)
        )
        .resolver("urn:platform-agent:v3-schema")
    )

    schema_maps = {
        "$defs",
        "definitions",
        "dependentSchemas",
        "patternProperties",
        "properties",
    }
    schema_sequences = {"allOf", "anyOf", "oneOf", "prefixItems"}
    schema_values = {
        "additionalProperties",
        "contains",
        "contentSchema",
        "else",
        "if",
        "items",
        "not",
        "propertyNames",
        "then",
        "unevaluatedItems",
        "unevaluatedProperties",
    }

    def visit(node: object) -> None:
        if not isinstance(node, Mapping):
            return
        for key, child in node.items():
            if key in {"$ref", "$dynamicRef"} and isinstance(child, str):
                resolver.lookup(child)
            elif key in schema_maps and isinstance(child, Mapping):
                for nested in child.values():
                    visit(nested)
            elif key in schema_sequences and isinstance(child, list):
                for nested in child:
                    visit(nested)
            elif key in schema_values:
                if isinstance(child, Mapping):
                    visit(child)
                elif isinstance(child, list):
                    for nested in child:
                        visit(nested)

    visit(detached)
    return validator_type(detached)


def _validation_issue(validator: Any, value: object) -> tuple[str, str] | None:
    errors = sorted(
        validator.iter_errors(value),
        key=lambda item: tuple(str(part) for part in item.absolute_path),
    )
    if not errors:
        return None
    error = errors[0]
    tokens = [
        str(part).replace("~", "~0").replace("/", "~1") for part in error.absolute_path
    ]
    return ("/" + "/".join(tokens) if tokens else "", str(error.validator))


def _mark_model_usage_unknown(
    state: dict[str, Any], *, step_key: str | None = None
) -> None:
    """Close dispatched model calls conservatively after an interrupted boundary."""

    agents = state.get("agents")
    if not isinstance(agents, Mapping):
        return
    for key, current in agents.items():
        if step_key is not None and key != step_key:
            continue
        if not isinstance(current, dict):
            continue
        invocations = current.get("model_invocations")
        if not isinstance(invocations, Mapping):
            continue
        changed = False
        for invocation in invocations.values():
            if (
                isinstance(invocation, dict)
                and invocation.get("status") == "DISPATCHED"
            ):
                invocation["status"] = "USAGE_UNKNOWN"
                changed = True
        usage = current.get("usage")
        if changed and isinstance(usage, dict):
            usage["tokens_unknown"] = True
            usage["model_requests_unknown"] = True
            usage["transport_attempts_unknown"] = True


def _mark_model_request_not_dispatched(
    state: dict[str, Any], step_key: str, invocation_id: str | None
) -> None:
    if invocation_id is None:
        return
    current = state["agents"][step_key]
    invocation = current["model_invocations"].get(invocation_id)
    if isinstance(invocation, dict) and invocation.get("status") == "RESERVED":
        invocation["status"] = "NOT_DISPATCHED"


def _tokens(pointer: str) -> tuple[str, ...]:
    if pointer == "":
        return ()
    return tuple(
        token.replace("~1", "/").replace("~0", "~") for token in pointer[1:].split("/")
    )


def _pointer(value: object, pointer: str) -> object:
    current = value
    for token in _tokens(pointer):
        if isinstance(current, Mapping) and token in current:
            current = current[token]
        elif (
            isinstance(current, list) and token.isdigit() and int(token) < len(current)
        ):
            current = current[int(token)]
        else:
            return _MISSING
    return current


def _set_pointer(document: dict[str, Any], pointer: str, value: object) -> None:
    tokens = _tokens(pointer)
    if not tokens:
        raise WorkflowRunError("v3 bindings cannot replace the document root")
    current: Any = document
    for index, token in enumerate(tokens):
        final = index == len(tokens) - 1
        next_is_index = not final and tokens[index + 1].isdigit()
        if isinstance(current, dict):
            if final:
                current[token] = json_copy(value)  # type: ignore[arg-type]
                return
            expected = list if next_is_index else dict
            existing = current.get(token)
            if existing is None:
                existing = expected()
                current[token] = existing
            if not isinstance(existing, expected):
                raise WorkflowRunError("binding targets overlap at runtime")
            current = existing
        elif isinstance(current, list) and token.isdigit():
            position = int(token)
            while len(current) <= position:
                current.append(None)
            if final:
                current[position] = json_copy(value)  # type: ignore[arg-type]
                return
            expected = list if next_is_index else dict
            existing = current[position]
            if existing is None:
                existing = expected()
                current[position] = existing
            if not isinstance(existing, expected):
                raise WorkflowRunError("binding targets overlap at runtime")
            current = existing
        else:
            raise WorkflowRunError("binding target cannot be materialized")


def _resolve_local_ref(
    root: Mapping[str, JsonValue], node: Mapping[str, Any]
) -> Mapping[str, Any]:
    seen: set[str] = set()
    current: Mapping[str, Any] = node
    while isinstance(current.get("$ref"), str):
        reference = str(current["$ref"])
        if reference in seen or not reference.startswith("#"):
            break
        seen.add(reference)
        target = _pointer(root, reference[1:])
        if not isinstance(target, Mapping):
            break
        current = target
    return current


def _schema_at(
    root: Mapping[str, JsonValue], pointer: str, *, label: str
) -> Mapping[str, Any]:
    current: Mapping[str, Any] = root
    for token in _tokens(pointer):
        current = _resolve_local_ref(root, current)
        declared = current.get("type")
        if declared == "object" or (
            isinstance(declared, list) and "object" in declared
        ):
            properties = current.get("properties")
            if not isinstance(properties, Mapping) or not isinstance(
                properties.get(token), Mapping
            ):
                raise WorkflowValidationError(f"{label} points to an unknown field")
            current = properties[token]
        elif declared == "array" or (
            isinstance(declared, list) and "array" in declared
        ):
            if not token.isdigit() or not isinstance(current.get("items"), Mapping):
                raise WorkflowValidationError(f"{label} points to an unknown item")
            current = current["items"]
        else:
            raise WorkflowValidationError(f"{label} traverses a non-container value")
    return _resolve_local_ref(root, current)


def _schema_types(schema: Mapping[str, Any]) -> frozenset[str] | None:
    declared = schema.get("type")
    if isinstance(declared, str):
        return frozenset({declared})
    if isinstance(declared, list) and all(isinstance(item, str) for item in declared):
        return frozenset(declared)
    return None


def _literal_types(value: object) -> frozenset[str]:
    if value is None:
        return frozenset({"null"})
    if isinstance(value, bool):
        return frozenset({"boolean"})
    if isinstance(value, int):
        return frozenset({"integer", "number"})
    if isinstance(value, float):
        return frozenset({"number"})
    if isinstance(value, str):
        return frozenset({"string"})
    if isinstance(value, list):
        return frozenset({"array"})
    return frozenset({"object"})


def _compatible(source: Mapping[str, Any], target: Mapping[str, Any]) -> bool:
    source_types = _schema_types(source)
    target_types = _schema_types(target)
    if source_types is None or target_types is None:
        return True
    return bool(source_types & target_types) or (
        "integer" in source_types and "number" in target_types
    )


def _required_pointers(
    root: Mapping[str, JsonValue],
    node: Mapping[str, Any] | None = None,
    prefix: str = "",
    visiting: frozenset[int] = frozenset(),
) -> set[str]:
    current = _resolve_local_ref(root, node or root)
    identity = id(current)
    if identity in visiting:
        return set()
    visiting = visiting | {identity}
    required = current.get("required", [])
    properties = current.get("properties", {})
    result: set[str] = set()
    if not isinstance(required, list) or not isinstance(properties, Mapping):
        return result
    for name in required:
        if not isinstance(name, str):
            continue
        escaped = name.replace("~", "~0").replace("/", "~1")
        path = f"{prefix}/{escaped}"
        result.add(path)
        child = properties.get(name)
        if isinstance(child, Mapping):
            result.update(_required_pointers(root, child, path, visiting))
    return result


def _binding_covers(binding: str, required: str) -> bool:
    return (
        binding == required
        or binding.startswith(required + "/")
        or required.startswith(binding + "/")
    )


class _Compiler:
    def __init__(
        self, workflow: WorkflowSpec, catalog: Mapping[tuple[str, str], WorkflowSpec]
    ) -> None:
        self.workflow = workflow
        self.catalog = catalog
        self.validators: dict[str, Any] = {}
        self._visiting: set[tuple[str, str]] = set()

    def compile(self) -> dict[str, Any]:
        if self.workflow.schema_version not in ASYNC_SCHEMA_VERSIONS:
            raise WorkflowValidationError(
                "async preflight requires a v3 or v4 workflow"
            )
        assert self.workflow.input_schema is not None
        assert self.workflow.output_schema is not None
        self._compile_workflow(self.workflow)
        return self.validators

    def _validator(self, key: str, schema: Mapping[str, JsonValue]) -> Any:
        try:
            validator = _compile_schema(schema)
        except Exception as error:
            raise WorkflowValidationError(
                f"v3 schema {key} is not a valid local Draft 2020-12 schema"
            ) from error
        self.validators[key] = validator
        return validator

    def _compile_workflow(self, workflow: WorkflowSpec) -> None:
        identity = (workflow.workflow_id, workflow.version)
        if identity in self._visiting:
            raise WorkflowValidationError("recursive v3 subworkflows are not supported")
        self._visiting.add(identity)
        assert workflow.input_schema is not None
        assert workflow.output_schema is not None
        self._validator(f"workflow:{identity}:input", workflow.input_schema)
        self._validator(f"workflow:{identity}:output", workflow.output_schema)
        visible, _ = self._compile_sequence(
            workflow.steps,
            {},
            workflow.input_schema,
            workflow.input_schema,
            workflow,
        )
        self._compile_bindings(
            workflow.output_bindings,
            workflow.output_schema,
            visible,
            workflow.input_schema,
            workflow.input_schema,
            "workflow output",
        )
        self._visiting.remove(identity)

    def _compile_sequence(
        self,
        steps: Sequence[StepSpec],
        incoming: Mapping[str, Mapping[str, JsonValue]],
        workflow_input_schema: Mapping[str, JsonValue],
        parent_input_schema: Mapping[str, JsonValue],
        workflow: WorkflowSpec,
    ) -> tuple[dict[str, Mapping[str, JsonValue]], Mapping[str, JsonValue] | None]:
        visible = dict(incoming)
        tail: Mapping[str, JsonValue] | None = None
        for step in steps:
            contract = step.contract
            if contract is None:
                raise WorkflowValidationError(f"v3 step {step.step_id} has no contract")
            self._validator(
                f"step:{workflow.workflow_id}:{workflow.version}:{step.step_id}:input",
                contract.input_schema,
            )
            self._validator(
                f"step:{workflow.workflow_id}:{workflow.version}:{step.step_id}:output",
                contract.output_schema,
            )
            self._compile_bindings(
                contract.bindings,
                contract.input_schema,
                visible,
                workflow_input_schema,
                parent_input_schema,
                f"step {step.step_id}",
            )
            for tool in (
                AgentToolSpec.from_mapping(item)
                for item in step.config.get("tools", [])
                if isinstance(item, Mapping) and "name" in item
            ):
                input_validator = self._validator(
                    f"agent-tool:{workflow.workflow_id}:{workflow.version}:{step.step_id}:{tool.name}:input",
                    tool.input_schema,
                )
                output_validator = self._validator(
                    f"agent-tool:{workflow.workflow_id}:{workflow.version}:{step.step_id}:{tool.name}:output",
                    tool.output_schema,
                )
                for example in tool.examples:
                    if _validation_issue(input_validator, example.arguments):
                        raise WorkflowValidationError(
                            f"Agent tool example for {tool.name} has invalid arguments"
                        )
                    if example.output is not None and _validation_issue(
                        output_validator, example.output
                    ):
                        raise WorkflowValidationError(
                            f"Agent tool example for {tool.name} has invalid output"
                        )
            if step.kind is StepKind.CONDITION:
                _schema_at(
                    contract.input_schema,
                    str(step.config["path"]),
                    label=f"condition {step.step_id} path",
                )
                for branch in step.children:
                    _, branch_tail = self._compile_sequence(
                        branch,
                        visible,
                        workflow_input_schema,
                        contract.input_schema,
                        workflow,
                    )
                    if branch_tail is not None and not _compatible(
                        branch_tail, contract.output_schema
                    ):
                        raise WorkflowValidationError(
                            f"condition {step.step_id} branch output type is incompatible"
                        )
            elif step.kind is StepKind.PARALLEL:
                output_properties = contract.output_schema.get("properties", {})
                required = contract.output_schema.get("required", [])
                if not isinstance(output_properties, Mapping) or not isinstance(
                    required, list
                ):
                    raise WorkflowValidationError(
                        f"parallel {step.step_id} output must declare branch properties"
                    )
                if set(step.branch_names) - set(output_properties) or not set(
                    step.branch_names
                ) <= set(required):
                    raise WorkflowValidationError(
                        f"parallel {step.step_id} output must require every named branch"
                    )
                for name, branch in zip(step.branch_names, step.children, strict=True):
                    _, branch_tail = self._compile_sequence(
                        branch,
                        visible,
                        workflow_input_schema,
                        contract.input_schema,
                        workflow,
                    )
                    target = output_properties[name]
                    if (
                        not isinstance(target, Mapping)
                        or branch_tail is None
                        or not _compatible(branch_tail, target)
                    ):
                        raise WorkflowValidationError(
                            f"parallel {step.step_id} branch {name} output is incompatible"
                        )
            elif step.kind is StepKind.SUBWORKFLOW:
                reference = (
                    str(step.config["workflow_id"]),
                    str(step.config["version"]),
                )
                child = self.catalog.get(reference)
                if child is None:
                    raise WorkflowValidationError("subworkflow is not registered")
                if child.schema_version != self.workflow.schema_version:
                    raise WorkflowValidationError(
                        "async subworkflows must use the parent schema version"
                    )
                if canonical_json(contract.input_schema) != canonical_json(
                    child.input_schema
                ) or canonical_json(contract.output_schema) != canonical_json(
                    child.output_schema
                ):
                    raise WorkflowValidationError(
                        "subworkflow node contract must equal the referenced v3 contract"
                    )
                self._compile_workflow(child)
            visible[step.step_id] = contract.output_schema
            tail = contract.output_schema
        return visible, tail

    def _compile_bindings(
        self,
        bindings: Mapping[str, ValueBinding],
        target_root: Mapping[str, JsonValue],
        visible: Mapping[str, Mapping[str, JsonValue]],
        workflow_input: Mapping[str, JsonValue],
        parent_input: Mapping[str, JsonValue],
        label: str,
    ) -> None:
        for target_pointer, binding in bindings.items():
            target = _schema_at(
                target_root, target_pointer, label=f"{label} binding target"
            )
            if binding.source == "literal":
                source_types = _literal_types(binding.value)
                target_types = _schema_types(target)
                if target_types is not None and not (
                    source_types & target_types
                    or ("integer" in source_types and "number" in target_types)
                ):
                    raise WorkflowValidationError(
                        f"{label} has an explicitly incompatible literal binding"
                    )
                continue
            if binding.source == "workflow_input":
                source_root = workflow_input
            elif binding.source == "parent_input":
                source_root = parent_input
            else:
                assert binding.step_id is not None
                source_root = visible.get(binding.step_id)
                if source_root is None:
                    raise WorkflowValidationError(
                        f"{label} references a step that is not definitely complete and visible"
                    )
            source = _schema_at(
                source_root,
                binding.pointer,
                label=f"{label} binding source",
            )
            if not _compatible(source, target):
                raise WorkflowValidationError(
                    f"{label} has explicitly incompatible source and target types"
                )
        required = _required_pointers(target_root)
        missing = sorted(
            path
            for path in required
            if not any(_binding_covers(target, path) for target in bindings)
        )
        if missing:
            raise WorkflowValidationError(
                f"{label} does not bind required input {missing[0]}"
            )


def preflight_v3(
    workflow: WorkflowSpec, *, workflows: Sequence[WorkflowSpec] = ()
) -> dict[str, Any]:
    """Compile every v3 schema and binding before a run snapshot exists."""

    catalog = {(item.workflow_id, item.version): item for item in workflows}
    catalog[(workflow.workflow_id, workflow.version)] = workflow
    # Schema compilation dominated the no-op execution profile for immutable
    # definitions.  Key by the content digest, not object identity, so a
    # reconstructed snapshot or a separate embedding shares the same safely
    # compiled validators.  The small bounded cache retains no run values.
    # Compilation includes the chosen root's validators and visibility rules,
    # not merely the directory catalogue.  Keep the compiler protocol in the
    # key too, so a semantic compiler change cannot read an older entry.
    cache_key = (
        _PREFLIGHT_COMPILER_VERSION,
        workflow.workflow_id,
        workflow.version,
        workflow.definition_digest,
        tuple(
            sorted(
                (item.workflow_id, item.version, item.definition_digest)
                for item in catalog.values()
            )
        ),
    )
    global _PREFLIGHT_CACHE_HITS, _PREFLIGHT_CACHE_MISSES
    with _PREFLIGHT_CACHE_LOCK:
        cached = _PREFLIGHT_CACHE.get(cache_key)
        if cached is not None:
            _PREFLIGHT_CACHE_HITS += 1
            _PREFLIGHT_CACHE.move_to_end(cache_key)
            return dict(cached)
        _PREFLIGHT_CACHE_MISSES += 1
    compiled = _Compiler(workflow, catalog).compile()
    with _PREFLIGHT_CACHE_LOCK:
        _PREFLIGHT_CACHE[cache_key] = dict(compiled)
        _PREFLIGHT_CACHE.move_to_end(cache_key)
        while len(_PREFLIGHT_CACHE) > _PREFLIGHT_CACHE_SIZE:
            _PREFLIGHT_CACHE.popitem(last=False)
    return dict(compiled)


def preflight_cache_metrics() -> dict[str, int]:
    """Return bounded compiler-cache counters for local performance probes.

    The metrics intentionally contain no definition IDs, schemas, run values,
    or timestamps.  They are process-local observations, never persisted in a
    snapshot or journal, and let an embedding distinguish cold schema
    compilation from a normal immutable-definition cache hit.
    """

    with _PREFLIGHT_CACHE_LOCK:
        return {
            "capacity": _PREFLIGHT_CACHE_SIZE,
            "entries": len(_PREFLIGHT_CACHE),
            "hits": _PREFLIGHT_CACHE_HITS,
            "misses": _PREFLIGHT_CACHE_MISSES,
        }


@dataclass(frozen=True)
class _Outcome:
    kind: str
    output: Mapping[str, JsonValue] | None = None


class _RetainedStateTooLargeError(RuntimeError):
    pass


def _json_size_bytes(value: object) -> int:
    return len(canonical_json(value).encode("utf-8"))


def _retained_state_limit(state: Mapping[str, Any]) -> int | None:
    v3 = state.get("v3")
    value = v3.get("max_retained_state_bytes") if isinstance(v3, Mapping) else None
    return value if isinstance(value, int) and not isinstance(value, bool) else None


class _Coordinator:
    def __init__(
        self,
        engine: WorkflowRuntimePort,
        snapshot: RunSnapshot,
        *,
        owner_id: str | None = None,
        generation: int | None = None,
    ) -> None:
        anyio, _, _, _ = _optional_dependencies()
        self.engine = engine
        self.snapshot = snapshot
        self.owner_id = owner_id
        self.generation = generation
        self._lock = anyio.Lock()
        self._read_snapshot: RunSnapshot | None = None
        self._read_state: Mapping[str, Any] | None = None
        self._cells: PersistentCellState | None = None
        self._cells_revision: int | None = None
        self._cell_head: _CellCommitHead | None = None
        self._read_head: _CellCommitHead | None = None

    def _assert_execution_owner(self, state: Mapping[str, Any]) -> None:
        if self.owner_id is None:
            return
        execution = state["v3"].get("execution")
        if (
            not isinstance(execution, Mapping)
            or execution.get("owner_id") != self.owner_id
            or execution.get("generation") != self.generation
        ):
            raise RunConflictError("v3 execution generation is no longer current")

    async def refresh(self) -> RunSnapshot:
        anyio, _, _, _ = _optional_dependencies()
        async with self._lock:
            control_method = getattr(
                self.engine.workflow_services.journal, "control", None
            )
            if callable(control_method):
                control = await anyio.to_thread.run_sync(
                    control_method, self.snapshot.run_id
                )
                current_revision = (
                    self._cell_head.revision
                    if self._cell_head is not None
                    else self.snapshot.revision
                )
                if control.revision == current_revision:
                    if self.owner_id is not None and (
                        control.execution_owner_id != self.owner_id
                        or control.execution_generation != self.generation
                    ):
                        raise RunConflictError(
                            "v3 execution generation is no longer current"
                        )
                    return self.snapshot
            self.snapshot = await anyio.to_thread.run_sync(
                self.engine.workflow_services.journal.get, self.snapshot.run_id
            )
            self._assert_execution_owner(_state(self.snapshot))
            self._cells = None
            self._cells_revision = None
            self._cell_head = None
            self._read_head = None
            self._read_snapshot = None
            self._read_state = None
            return self.snapshot

    async def state(self) -> Mapping[str, Any]:
        """Share one read view per revision; commits use separate state copies.

        Read-only orchestration previously decoded the complete canonical
        state for every check within the same revision. A commit still owns
        an independent copy under the journal CAS boundary.
        """

        snapshot = await self.refresh()
        if self._cell_head is not None:
            if self._read_head is self._cell_head and self._read_state is not None:
                return self._read_state
            state = self._cell_head.cells.view()
            self._read_head = self._cell_head
            self._read_state = state
            return state
        if snapshot._definition_schema_version() != V4_SCHEMA_VERSION:
            # Compatibility Agent contexts may expose nested workflow values
            # to a caller-owned driver. Keep their historical detached-read
            # behavior; only v4 has the neutralized, copied context boundary.
            return _state(snapshot)
        if snapshot is self._read_snapshot and self._read_state is not None:
            return self._read_state
        cells = self._cells
        if cells is None or self._cells_revision != snapshot.revision:
            cells = PersistentCellState.from_state(_state(snapshot))
            self._cells = cells
            self._cells_revision = snapshot.revision
        state = cells.view()
        self._read_snapshot = snapshot
        self._read_state = state
        return state

    async def commit(
        self,
        event_type: str,
        payload: Mapping[str, JsonValue],
        mutate: Callable[[dict[str, Any]], None] | ExplicitStateMutation | None = None,
        *,
        status: str | None = None,
        enforce_retained_budget: bool = False,
    ) -> RunSnapshot:
        anyio, _, _, _ = _optional_dependencies()
        async with self._lock:
            run_id = self.snapshot.run_id
            journal = self.engine.workflow_services.journal
            allow_fast = (
                self.owner_id is not None
                and status is None
                and (isinstance(mutate, ExplicitStateMutation) or mutate is None)
                and type(journal) in {SQLiteRunJournal, InMemoryRunJournal}
            )
            # The owner serializes local transitions already.  Use the journal
            # CAS boundary directly instead of hydrating a second full snapshot
            # for every event; if another process changed the run, reload only
            # then and apply the transition to the newer durable state.
            for _ in range(8):
                if allow_fast:
                    head = self._cell_head
                    if (
                        head is None
                        and self.snapshot._definition_schema_version()
                        == V4_SCHEMA_VERSION
                    ):
                        head = _CellCommitHead.from_snapshot(self.snapshot)
                        self._cell_head = head
                    if head is not None and head.status == "RUNNING":

                        def save_cells(head: _CellCommitHead = head) -> _CellCommitHead:
                            with self.engine.workflow_services.transition_lock:
                                state_view = head.cells.view()
                                self._assert_execution_owner(state_view)
                                edit = CellStateEdit(head.cells)
                                if isinstance(mutate, ExplicitStateMutation):
                                    mutate.apply(edit)
                                execution = state_view["v3"]["execution"]
                                if execution.get("owner_id") == self.owner_id:
                                    edit.set(
                                        ("v3", "execution", "lease_expires"),
                                        time.time() + _EXECUTION_LEASE_SECONDS,
                                    )
                                if (
                                    isinstance(mutate, ExplicitStateMutation)
                                    and not edit.changed_paths
                                ):
                                    raise WorkflowRunError(
                                        "explicit state mutation produced no writes"
                                    )
                                writes = edit.write_set()
                                limit = _retained_state_limit(state_view)
                                if (
                                    enforce_retained_budget
                                    and limit is not None
                                    and head.upper_bound_after(writes) > limit
                                ):
                                    raise _CellDeltaUnavailableError(
                                        "retained size needs an exact check"
                                    )
                                return journal._replace_cell_writes(
                                    head,
                                    writes,
                                    status=head.status,
                                    event_type=event_type,
                                    payload=payload,
                                )

                        try:
                            next_head = await anyio.to_thread.run_sync(save_cells)
                        except _CellDeltaUnavailableError:
                            # One unsupported transition uses the old exact
                            # snapshot path; do not retry this fast path in
                            # the same commit.
                            allow_fast = False
                        except RunConflictError:
                            latest = await anyio.to_thread.run_sync(journal.get, run_id)
                            self.snapshot = latest
                            self._cell_head = None
                            self._cells = None
                            self._cells_revision = None
                            self._read_head = None
                            self._read_snapshot = None
                            self._read_state = None
                            continue
                        else:
                            self._cell_head = next_head
                            self._cells = next_head.cells
                            self._cells_revision = next_head.revision
                            self._read_head = None
                            self._read_snapshot = None
                            self._read_state = None
                            self.engine.workflow_services.notify_event(run_id)
                            return self.snapshot

                if self._cell_head is not None and (
                    self._cell_head.revision != self.snapshot.revision
                ):
                    self.snapshot = await anyio.to_thread.run_sync(journal.get, run_id)
                    self._cell_head = None
                    self._cells = None
                    self._cells_revision = None
                    self._read_head = None
                    self._read_snapshot = None
                    self._read_state = None
                current = self.snapshot
                current_cells = (
                    self._cells if self._cells_revision == current.revision else None
                )

                def save(
                    current: RunSnapshot = current,
                    current_cells: PersistentCellState | None = current_cells,
                ) -> tuple[RunSnapshot, PersistentCellState | None]:
                    with self.engine.workflow_services.transition_lock:
                        defaulted_paths: set[tuple[str, ...]] = set()
                        state = _state(current, defaulted_paths=defaulted_paths)
                        self._assert_execution_owner(state)
                        if current.status in _TERMINAL:
                            raise RunConflictError(
                                "completed v3 runs cannot accept further transitions"
                            )
                        edit: StateEdit | None = None
                        if isinstance(mutate, ExplicitStateMutation):
                            edit = StateEdit(
                                state, initial_changed_paths=defaulted_paths
                            )
                            mutate.apply(edit)
                        elif mutate is None:
                            edit = StateEdit(
                                state, initial_changed_paths=defaulted_paths
                            )
                        elif mutate is not None:
                            mutate(state)
                        if self.owner_id is not None:
                            execution = state["v3"]["execution"]
                            if execution.get("owner_id") == self.owner_id:
                                lease_expires = time.time() + _EXECUTION_LEASE_SECONDS
                                if edit is not None:
                                    edit.set(
                                        ("v3", "execution", "lease_expires"),
                                        lease_expires,
                                    )
                                else:
                                    execution["lease_expires"] = lease_expires
                        if (
                            isinstance(mutate, ExplicitStateMutation)
                            and edit is not None
                            and not edit.changed_paths
                        ):
                            raise WorkflowRunError(
                                "explicit state mutation produced no writes"
                            )
                        changes: dict[str, Any] = {
                            "state": state,
                            "status": status if status is not None else current.status,
                        }
                        if edit is not None and edit.changed_paths:
                            changes["_state_write_set"] = edit.write_set()
                        updated = current.next_revision(**changes)
                        limit = _retained_state_limit(state)
                        if (
                            enforce_retained_budget
                            and limit is not None
                            and updated._retained_size_bytes() > limit
                        ):
                            raise _RetainedStateTooLargeError
                        next_cells = None
                        if current._definition_schema_version() == V4_SCHEMA_VERSION:
                            next_cells = (
                                current_cells.after_commit(
                                    state, updated._state_write_set
                                )
                                if current_cells is not None
                                else PersistentCellState.from_state(state)
                            )
                        saved = self.engine.workflow_services.journal.replace(
                            updated,
                            expected_revision=current.revision,
                            event_type=event_type,
                            payload=payload,
                        )
                        return saved, next_cells

                try:
                    self.snapshot, self._cells = await anyio.to_thread.run_sync(save)
                    self._cells_revision = (
                        self.snapshot.revision if self._cells is not None else None
                    )
                    self._cell_head = None
                    self._read_head = None
                    self._read_snapshot = None
                    self._read_state = None
                except RunConflictError:
                    latest = await anyio.to_thread.run_sync(
                        self.engine.workflow_services.journal.get, run_id
                    )
                    self.snapshot = latest
                    self._cells = None
                    self._cells_revision = None
                    self._cell_head = None
                    self._read_head = None
                    self._read_snapshot = None
                    self._read_state = None
                    if latest.revision == current.revision:
                        raise
                    continue
                self.engine.workflow_services.notify_event(run_id)
                return self.snapshot
            raise RunConflictError("v3 transition could not be merged after retries")


def _state(
    snapshot: RunSnapshot,
    *,
    defaulted_paths: set[tuple[str, ...]] | None = None,
) -> dict[str, Any]:
    # RunSnapshot owns a validated canonical mutable-state representation.
    # Decode that authority directly instead of serializing the public mapping
    # again at every event; callers mutating their detached view cannot enter a
    # transition through this path.
    state = snapshot._state_copy()
    required = {
        "values",
        "completed",
        "branches",
        "actions",
        "agents",
        "approved_steps",
        "pending",
        "failure",
        "v3",
    }
    if not isinstance(state, dict) or not required <= set(state):
        raise WorkflowRunError("run state is incompatible with the v3 kernel")
    v3 = state["v3"]
    if not isinstance(v3, dict) or not {
        "step_outputs",
        "step_states",
        "branch_states",
        "pending_approvals",
        "cancel_requested",
        "deadline",
        "node_timeouts",
        "output",
    } <= set(v3):
        raise WorkflowRunError("v3 branch state is malformed")

    def defaulted(name: str, value: JsonValue) -> Any:
        if name not in v3:
            v3[name] = value
            if defaulted_paths is not None:
                defaulted_paths.add(("v3", name))
        return v3[name]

    execution = defaulted(
        "execution", {"generation": 0, "owner_id": None, "lease_expires": None}
    )
    if (
        not isinstance(execution, dict)
        or not isinstance(execution.get("generation"), int)
        or execution.get("owner_id") is not None
        and not isinstance(execution.get("owner_id"), str)
        or execution.get("lease_expires") is not None
        and not isinstance(execution.get("lease_expires"), (int, float))
    ):
        raise WorkflowRunError("v3 execution lease is malformed")
    timeouts = defaulted("subworkflow_timeouts", {})
    if not isinstance(timeouts, dict):
        raise WorkflowRunError("v3 subworkflow timeout state is malformed")
    started = defaulted("step_started_at", {})
    if not isinstance(started, dict):
        raise WorkflowRunError("v3 step timing state is malformed")
    approval_decisions = defaulted("approval_decisions", {})
    if not isinstance(approval_decisions, dict):
        raise WorkflowRunError("v3 approval decision state is malformed")
    return state


def _approval_id(execution_id: str, kind: str, key: str) -> str:
    raw = f"{execution_id}:{kind}:{key}".encode()
    return "approval-" + hashlib.sha256(raw).hexdigest()[:40]


def _model_invocation_id(execution_id: str, key: str, request_number: int) -> str:
    raw = f"{execution_id}:model:{key}:{request_number}".encode()
    return "model-" + hashlib.sha256(raw).hexdigest()[:40]


def _failure_mutation(code: str, step_id: str | None = None) -> ExplicitStateMutation:
    failure: dict[str, JsonValue] = {"code": code}
    if step_id is not None:
        failure["step_id"] = step_id
    return ExplicitStateMutation(lambda edit: edit.set(("failure",), failure))


def _validation_failure_mutation(
    code: str, step_id: str, issue: tuple[str, str]
) -> ExplicitStateMutation:
    return ExplicitStateMutation(
        lambda edit: edit.set(
            ("failure",),
            {
                "code": code,
                "step_id": step_id,
                "path": issue[0],
                "keyword": issue[1],
            },
        )
    )


def _checked_action_result(
    action: Mapping[str, Any], result: ToolExecutionResult
) -> tuple[ToolExecutionResult, tuple[str, str] | None]:
    """Validate success output before it can enter the durable action record."""

    if not result.succeeded:
        return (
            ToolExecutionResult(
                result.action_id,
                False,
                error=result.error,
            ),
            None,
        )
    max_output_bytes = action.get("max_output_bytes")
    if (
        isinstance(max_output_bytes, int)
        and not isinstance(max_output_bytes, bool)
        and len(canonical_json(result.output).encode("utf-8")) > max_output_bytes
    ):
        return result, ("", "maxBytes")
    schema = action.get("output_schema")
    if not isinstance(schema, Mapping):
        return result, ("", "action_contract")
    try:
        issue = _validation_issue(_compile_schema(schema), result.output)
    except Exception:  # noqa: BLE001 - corrupted persisted contract fails closed
        issue = ("", "action_contract")
    return result, issue


def _action_mutex_resource(action: Mapping[str, Any]) -> str | None:
    capabilities = action.get("capabilities")
    if not isinstance(capabilities, Mapping):
        return None
    resource = capabilities.get("mutex_resource")
    if not isinstance(resource, str):
        return None
    return resource


def _release_action_resource(
    engine: WorkflowRuntimePort, action_id: str, action: Mapping[str, Any]
) -> None:
    resource = _action_mutex_resource(action)
    if resource is None:
        return
    # A missing lease is an explicit idempotent disposition from the
    # thread-safe registry.  No cross-event-loop RuntimeError is hidden here.
    engine.workflow_services.release_resource(resource, action_id)


def _acknowledge_durable_action(
    engine: WorkflowRuntimePort, action: Mapping[str, Any]
) -> None:
    """Allow an opt-in executor to bound a result after a durable transition."""

    raw_request = action.get("request")
    if not isinstance(raw_request, Mapping):
        raise WorkflowRunError("v3 action request is missing")
    acknowledge = getattr(engine, "_acknowledge_durable_result", None)
    if callable(acknowledge):
        acknowledge(ToolExecutionRequest.from_mapping(raw_request))


def _record_recovery_metadata(
    action: dict[str, Any], observation: _RecoveryObservation
) -> None:
    """Persist query progress without turning an unknown action into a fact.

    The fields deliberately describe only the query protocol.  They neither
    retain a rejected external payload nor act as a retry scheduler: callers
    may use ``next_query_at`` as a hint, while an explicit resume still gets a
    full reconciliation attempt.
    """

    existing = action.get("recovery")
    recovery = dict(existing) if isinstance(existing, Mapping) else {}
    count = recovery.get("query_count", 0)
    if not isinstance(count, int) or isinstance(count, bool) or count < 0:
        count = 0
    now = time.time()
    if observation.query_attempted:
        count += 1
    recovery["query_count"] = count
    recovery["latest_result"] = observation.kind
    recovery["last_query_at"] = now
    if observation.kind == "resolved":
        recovery["next_query_at"] = None
    else:
        delay = (
            _RECOVERY_UNSUPPORTED_RETRY_SECONDS
            if observation.kind == "unsupported"
            else _RECOVERY_RETRY_SECONDS
        )
        recovery["next_query_at"] = now + delay
    if observation.error_code is None:
        recovery.pop("latest_error_code", None)
    else:
        recovery["latest_error_code"] = observation.error_code
    action["recovery"] = recovery


async def _record_checked_action_result(
    coordinator: _Coordinator,
    action_id: str,
    result: ToolExecutionResult,
    *,
    event_type: str = "ACTION_RESOLVED",
    recovery_observation: _RecoveryObservation | None = None,
) -> tuple[_Outcome, ToolExecutionResult | None]:
    state = await coordinator.state()
    action = state["actions"].get(action_id)
    if not isinstance(action, Mapping):
        raise WorkflowRunError("v3 action is missing")
    checked, issue = _checked_action_result(action, result)
    candidate = checked.to_mapping()
    settlement = classify_action_settlement(
        action.get("status"), action.get("result"), candidate
    )
    if settlement.disposition in {"idempotent", "conflict"}:
        return await _settled_action_after_race(coordinator, action_id, candidate)
    if settlement.disposition != "accept":
        raise RunConflictError("action can no longer accept an execution result")
    if issue is not None:
        code = (
            (
                "AGENT_TOOL_OUTPUT_TOO_LARGE"
                if action.get("source") == "agent"
                else "STEP_OUTPUT_TOO_LARGE"
            )
            if issue[1] == "maxBytes"
            else str(action.get("output_error_code", "STEP_OUTPUT_INVALID"))
        )

        def reject(edit: StateEdit) -> None:
            current = edit.read(("actions", action_id))
            assert isinstance(current, dict)
            if current.get("status") == "RESOLVED":
                raise _TransitionAbortedError
            if current.get("status") not in _UNKNOWN_ACTION_STATES:
                raise RunConflictError(
                    "action can no longer accept an execution result"
                )
            current["status"] = "OUTPUT_INVALID"
            if recovery_observation is not None:
                _record_recovery_metadata(current, recovery_observation)
            edit.set(("actions", action_id), current)
            edit.set(
                ("failure",),
                {
                    "code": code,
                    "step_id": str(action["step_key"]),
                    "action_id": action_id,
                    "path": issue[0],
                    "keyword": issue[1],
                },
            )

        try:
            await coordinator.commit(
                code,
                {
                    "step_id": str(action["step_key"]),
                    "action_id": action_id,
                    "path": issue[0],
                    "keyword": issue[1],
                },
                ExplicitStateMutation(reject),
            )
        except _TransitionAbortedError:
            return await _settled_action_after_race(coordinator, action_id, candidate)
        _acknowledge_durable_action(coordinator.engine, action)
        _release_action_resource(coordinator.engine, action_id, action)
        return _Outcome("failed"), None

    def resolved(edit: StateEdit) -> None:
        current = edit.read(("actions", action_id))
        assert isinstance(current, dict)
        if current.get("status") == "RESOLVED":
            raise _TransitionAbortedError
        if current.get("status") not in _UNKNOWN_ACTION_STATES:
            raise RunConflictError("action can no longer accept an execution result")
        current["status"] = "RESOLVED"
        current["result"] = candidate
        if recovery_observation is not None:
            _record_recovery_metadata(current, recovery_observation)
        edit.set(("actions", action_id), current)

    try:
        await coordinator.commit(
            event_type,
            {"action_id": action_id, "succeeded": checked.succeeded},
            ExplicitStateMutation(resolved),
            enforce_retained_budget=True,
        )
    except _RetainedStateTooLargeError:

        def reject_retained(edit: StateEdit) -> None:
            current = edit.read(("actions", action_id))
            assert isinstance(current, dict)
            if current.get("status") not in _UNKNOWN_ACTION_STATES:
                raise RunConflictError(
                    "action can no longer accept an execution result"
                )
            current["status"] = "OUTPUT_INVALID"
            if recovery_observation is not None:
                _record_recovery_metadata(current, recovery_observation)
            edit.set(("actions", action_id), current)
            edit.set(
                ("failure",),
                {
                    "code": "RUN_RETAINED_STATE_TOO_LARGE",
                    "step_id": str(action["step_key"]),
                    "action_id": action_id,
                    "path": "",
                    "keyword": "maxRunBytes",
                },
            )

        await coordinator.commit(
            "RUN_RETAINED_STATE_TOO_LARGE",
            {
                "step_id": str(action["step_key"]),
                "action_id": action_id,
                "path": "",
                "keyword": "maxRunBytes",
            },
            ExplicitStateMutation(reject_retained),
        )
        _acknowledge_durable_action(coordinator.engine, action)
        _release_action_resource(coordinator.engine, action_id, action)
        return _Outcome("failed"), None
    except _TransitionAbortedError:
        return await _settled_action_after_race(coordinator, action_id, candidate)
    _acknowledge_durable_action(coordinator.engine, action)
    _release_action_resource(coordinator.engine, action_id, action)
    return _Outcome("done"), checked


async def _settled_action_after_race(
    coordinator: _Coordinator,
    action_id: str,
    candidate: Mapping[str, JsonValue],
) -> tuple[_Outcome, ToolExecutionResult | None]:
    state = await coordinator.state()
    action = state["actions"].get(action_id)
    if not isinstance(action, Mapping) or action.get("status") != "RESOLVED":
        raise RunConflictError("action changed before its result was committed")
    _acknowledge_durable_action(coordinator.engine, action)
    _release_action_resource(coordinator.engine, action_id, action)
    persisted = ToolExecutionResult.from_mapping(action["result"])
    settlement = classify_action_settlement(
        action.get("status"), action.get("result"), candidate
    )
    if settlement.disposition == "conflict":
        if coordinator.snapshot.status in _TERMINAL:
            raise RunConflictError(
                "a completed action already has a different confirmed result"
            )
        await coordinator.commit(
            "ACTION_RESULT_CONFLICT",
            {"action_id": action_id, "error_code": "ACTION_RESULT_CONFLICT"},
        )
    return _Outcome("done"), persisted


async def _record_action_uncertainty(
    coordinator: _Coordinator,
    action_id: str,
    payload: Mapping[str, JsonValue],
    *,
    recovery_observation: _RecoveryObservation | None = None,
) -> tuple[_Outcome, ToolExecutionResult | None]:
    """Record uncertainty only while an Action fact is still open."""

    state = await coordinator.state()
    action = state["actions"].get(action_id)
    if not isinstance(action, Mapping):
        raise WorkflowRunError("v3 action is missing")
    transition = classify_action_uncertainty(action.get("status"), action.get("result"))
    if transition.disposition == "settled":
        persisted = ToolExecutionResult.from_mapping(transition.result or {})
        _release_action_resource(coordinator.engine, action_id, action)
        try:
            await coordinator.commit(
                "LATE_ACTION_ERROR_IGNORED",
                {"action_id": action_id, **dict(payload)},
            )
        except RunConflictError:
            pass
        return _Outcome("done"), persisted
    if transition.disposition == "closed":
        raise RunConflictError("action can no longer become unknown")

    def mark_unknown(edit: StateEdit) -> None:
        current = edit.read(("actions", action_id))
        assert isinstance(current, dict)
        latest = classify_action_uncertainty(
            current.get("status"), current.get("result")
        )
        if latest.disposition == "settled":
            raise _TransitionAbortedError
        if latest.disposition != "mark_unknown":
            raise RunConflictError("action can no longer become unknown")
        current["status"] = "RECONCILING"
        if recovery_observation is not None:
            _record_recovery_metadata(current, recovery_observation)
        edit.set(("actions", action_id), current)

    try:
        await coordinator.commit(
            "ACTION_OUTCOME_UNKNOWN",
            {"action_id": action_id, **dict(payload)},
            ExplicitStateMutation(mark_unknown),
        )
    except _TransitionAbortedError:
        return await _record_action_uncertainty(
            coordinator,
            action_id,
            payload,
            recovery_observation=recovery_observation,
        )
    return _Outcome("reconciling"), None


async def _record_action_closure(
    coordinator: _Coordinator,
    action_id: str,
    *,
    status: str,
    event_type: str,
    payload: Mapping[str, JsonValue],
    outcome_kind: str,
    failure_code: str | None = None,
) -> tuple[_Outcome, ToolExecutionResult | None]:
    """Commit a stop/rejection only if no confirmed result won the race."""

    state = await coordinator.state()
    action = state["actions"].get(action_id)
    if not isinstance(action, Mapping):
        raise WorkflowRunError("v3 action is missing")
    transition = classify_action_closure(action.get("status"), action.get("result"))
    if transition.disposition == "settled":
        persisted = ToolExecutionResult.from_mapping(transition.result or {})
        _release_action_resource(coordinator.engine, action_id, action)
        try:
            await coordinator.commit(
                "LATE_ACTION_CLOSURE_IGNORED",
                {"action_id": action_id, **dict(payload)},
            )
        except RunConflictError:
            pass
        return _Outcome("done"), persisted
    if transition.disposition == "closed":
        raise RunConflictError("action can no longer be closed")

    step_key = str(action["step_key"])

    def close(edit: StateEdit) -> None:
        current = edit.read(("actions", action_id))
        assert isinstance(current, dict)
        latest = classify_action_closure(current.get("status"), current.get("result"))
        if latest.disposition == "settled":
            raise _TransitionAbortedError
        if latest.disposition != "close":
            raise RunConflictError("action can no longer be closed")
        current["status"] = status
        edit.set(("actions", action_id), current)
        if failure_code is not None and edit.read(("failure",)) is None:
            edit.set(
                ("failure",),
                {
                    "code": failure_code,
                    "step_id": step_key,
                    "action_id": action_id,
                },
            )

    try:
        await coordinator.commit(
            event_type,
            {"action_id": action_id, **dict(payload)},
            ExplicitStateMutation(close),
        )
    except _TransitionAbortedError:
        return await _record_action_closure(
            coordinator,
            action_id,
            status=status,
            event_type=event_type,
            payload=payload,
            outcome_kind=outcome_kind,
            failure_code=failure_code,
        )
    _release_action_resource(coordinator.engine, action_id, action)
    return _Outcome(outcome_kind), None


async def run_v3(
    engine: WorkflowRuntimePort,
    workflow: WorkflowSpec,
    inputs: Mapping[str, JsonValue],
    *,
    run_id: str | None,
) -> RunSnapshot:
    anyio, _, _, _ = _optional_dependencies(workflow.schema_version)
    values = json_copy(dict(inputs))
    if contains_secret_name(values):
        raise WorkflowValidationError(
            "workflow inputs must use credential references, not credentials"
        )
    validators = preflight_v3(
        workflow, workflows=tuple(engine.workflow_services.workflows.values())
    )
    assert workflow.input_schema is not None
    issue = _validation_issue(
        validators[f"workflow:{(workflow.workflow_id, workflow.version)}:input"],
        values,
    )
    if issue is not None:
        raise WorkflowValidationError(
            f"workflow input failed schema validation at {issue[0] or '/'} ({issue[1]})"
        )
    _validate_async_drivers(engine, workflow)
    engine.workflow_services.validate_drivers(workflow)
    run_id = run_id or f"run-{uuid4().hex}"
    now = time.time()
    assert workflow.execution_policy is not None
    deadline = (
        now + workflow.execution_policy.run_timeout_seconds
        if workflow.execution_policy.run_timeout_seconds is not None
        else None
    )
    state: dict[str, JsonValue] = {
        "values": {},
        "completed": [],
        "branches": {},
        "actions": {},
        "agents": {},
        "approved_steps": [],
        "pending": None,
        "failure": None,
        "v3": {
            "step_outputs": {},
            "step_states": {},
            "branch_states": {},
            "pending_approvals": {},
            "approval_decisions": {},
            "cancel_requested": False,
            "max_retained_state_bytes": (
                workflow.execution_policy.max_retained_state_bytes
            ),
            "deadline": deadline,
            "node_timeouts": {},
            "subworkflow_timeouts": {},
            "step_started_at": {},
            "execution": {
                "generation": 0,
                "owner_id": None,
                "lease_expires": None,
            },
            "output": None,
        },
    }
    snapshot = RunSnapshot(
        run_id=run_id,
        workflow=workflow.to_mapping(),
        inputs=values,
        state=state,
        status="RUNNING",
        driver_bindings=engine.workflow_services.driver_bindings(workflow),
        execution_id=f"execution-{uuid4().hex}",
        execution_manifest=engine.workflow_services.execution_manifest(workflow),
    )
    retained_limit = workflow.execution_policy.max_retained_state_bytes
    if retained_limit is not None and snapshot._retained_size_bytes() > retained_limit:
        raise WorkflowValidationError("RUN_RETAINED_STATE_TOO_LARGE")

    def create() -> RunSnapshot:
        with engine.workflow_services.transition_lock:
            return engine.workflow_services.journal.create(
                snapshot,
                "RUN_STARTED",
                {
                    "workflow_id": workflow.workflow_id,
                    "version": workflow.version,
                    "definition_digest": workflow.definition_digest,
                },
            )

    saved = await anyio.to_thread.run_sync(create)
    engine.workflow_services.notify_event(run_id)
    return await _drive(engine, saved, workflow, validators)


async def resume_v3(
    engine: WorkflowRuntimePort,
    snapshot: RunSnapshot,
    approvals: Sequence[object],
    *,
    advance: bool = True,
) -> RunSnapshot:
    if snapshot.status == BACKUP_RECOVERY_STATUS:
        raise WorkflowRunError("BACKUP_RECOVERY_REQUIRED")
    workflow = WorkflowSpec.from_mapping(snapshot.workflow)
    _optional_dependencies(workflow.schema_version)
    engine.workflow_services.verify_driver_bindings(snapshot)
    frozen_workflows = engine.workflow_services.manifest_workflows(snapshot)
    validators = preflight_v3(workflow, workflows=tuple(frozen_workflows.values()))
    _validate_async_drivers(engine, workflow, workflows=frozen_workflows)
    engine.workflow_services.validate_drivers(workflow, workflows=frozen_workflows)
    if snapshot.status in _TERMINAL:
        return snapshot
    decisions: list[ApprovalDecision] = []
    for decision in approvals:
        if not isinstance(decision, ApprovalDecision):
            raise WorkflowRunError("v3/v4 approvals must be ApprovalDecision values")
        decisions.append(decision)
    if len({item.approval_id for item in decisions}) != len(decisions):
        raise WorkflowRunError("an approval_id may be decided only once per resume")
    coordinator = _Coordinator(engine, snapshot)
    if decisions:
        state = await coordinator.state()
        pending = state["v3"]["pending_approvals"]
        recorded = state["v3"]["approval_decisions"]
        if not isinstance(pending, Mapping):
            raise WorkflowRunError("pending approval state is malformed")
        if not isinstance(recorded, Mapping):
            raise WorkflowRunError("approval decision state is malformed")
        new_decisions: list[ApprovalDecision] = []
        for decision in decisions:
            if decision.approval_id in pending:
                new_decisions.append(decision)
                continue
            previous = recorded.get(decision.approval_id)
            if isinstance(previous, bool) and previous == decision.approved:
                continue
            if isinstance(previous, bool):
                raise WorkflowRunError("approval_id already has another decision")
            raise WorkflowRunError("approval_id does not identify a pending approval")

        def apply(edit: StateEdit) -> None:
            for decision in new_decisions:
                item = edit.read(("v3", "pending_approvals", decision.approval_id))
                if not isinstance(item, dict):
                    raise RunConflictError("approval changed before its decision")
                edit.remove(("v3", "pending_approvals", decision.approval_id))
                edit.set(
                    ("v3", "approval_decisions", decision.approval_id),
                    decision.approved,
                )
                if decision.approved:
                    if item["kind"] == "ACTION":
                        edit.set(("actions", item["action_id"], "status"), "READY")
                    else:
                        edit.add_sorted_member(("approved_steps",), item["step_key"])
                elif edit.read(("failure",)) is None:
                    edit.set(
                        ("failure",),
                        {
                            "code": "APPROVAL_REJECTED",
                            "approval_id": decision.approval_id,
                            "step_id": item["step_key"],
                        },
                    )

        if new_decisions:
            snapshot = await coordinator.commit(
                "APPROVALS_RECORDED",
                {
                    "approval_ids": [item.approval_id for item in new_decisions],
                    "approved": [item.approved for item in new_decisions],
                },
                ExplicitStateMutation(apply),
                status="RUNNING",
            )
        else:
            snapshot = coordinator.snapshot
        latest = await coordinator.state()
        if _execution_is_active(latest):
            # Decision submission is a control-plane operation.  The current
            # persisted executor consumes it; a second scheduler must not race
            # it merely because an approval arrived mid-flight.
            return coordinator.snapshot
    elif snapshot.status == "WAITING_APPROVAL":
        waiting_state = await coordinator.state()
        deadline = waiting_state["v3"]["deadline"]
        if waiting_state["v3"]["pending_approvals"] and (
            deadline is None or float(deadline) > time.time()
        ):
            return coordinator.snapshot
    if not advance:
        # Approval submission is deliberately a small durable control-plane
        # transition.  A host may enqueue (or an already active owner may
        # consume) execution separately without making the control caller wait
        # behind model/tool capacity.
        return coordinator.snapshot
    return await _drive(engine, coordinator.snapshot, workflow, validators)


async def request_cancel_v3(
    engine: WorkflowRuntimePort,
    run_id: str,
    *,
    reason: str,
    expected_execution_id: str | None = None,
) -> RunSnapshot:
    anyio, _, _, _ = _optional_dependencies()
    if not isinstance(reason, str) or not reason:
        raise WorkflowRunError("cancellation reason is required")

    def save() -> RunSnapshot:
        with engine.workflow_services.transition_lock:
            current = _journal_get(
                engine.workflow_services.journal, run_id, expected_execution_id
            )
            workflow = WorkflowSpec.from_mapping(current.workflow)
            if workflow.schema_version not in ASYNC_SCHEMA_VERSIONS:
                raise WorkflowRunError(
                    "request_cancel() is available only for v3/v4 workflows"
                )
            _optional_dependencies(workflow.schema_version)
            if current.status in _TERMINAL:
                return current
            defaulted_paths: set[tuple[str, ...]] = set()
            state = _state(current, defaulted_paths=defaulted_paths)
            edit = StateEdit(state, initial_changed_paths=defaulted_paths)
            edit.set(
                ("v3", "cancel_requested"),
                merge_cancel_intent(edit.read(("v3", "cancel_requested"))),
            )
            updated = current.next_revision(
                state=state,
                status="CANCEL_REQUESTED",
                _state_write_set=edit.write_set(),
            )
            return engine.workflow_services.journal.replace(
                updated,
                expected_revision=current.revision,
                event_type="CANCELLATION_REQUESTED",
                payload={"reason_code": "CANCELLED_BY_CALLER"},
            )

    snapshot = await anyio.to_thread.run_sync(save)
    engine.workflow_services.notify_event(run_id)
    with engine.workflow_services.active_lock:
        token = engine.workflow_services.active_tokens.get(run_id)
    if token is not None:
        token.request()
    return snapshot


async def reconcile_v3(
    engine: WorkflowRuntimePort,
    snapshot: RunSnapshot,
    action_id: str,
    result: ToolExecutionResult,
    *,
    advance: bool = True,
) -> RunSnapshot:
    if snapshot.status == BACKUP_RECOVERY_STATUS:
        raise WorkflowRunError("BACKUP_RECOVERY_REQUIRED")
    workflow = WorkflowSpec.from_mapping(snapshot.workflow)
    _optional_dependencies(workflow.schema_version)
    engine.workflow_services.verify_driver_bindings(snapshot)
    frozen_workflows = engine.workflow_services.manifest_workflows(snapshot)
    validators = preflight_v3(workflow, workflows=tuple(frozen_workflows.values()))
    if result.action_id != action_id:
        raise WorkflowRunError("reconciled result refers to another action")
    coordinator = _Coordinator(engine, snapshot)
    state = await coordinator.state()
    action = state["actions"].get(action_id)
    if (
        not isinstance(action, Mapping)
        or action.get("status") not in _UNKNOWN_ACTION_STATES
    ):
        raise WorkflowRunError("only an unknown v3 action can be reconciled")
    request = ToolExecutionRequest.from_mapping(action["request"])
    if request.action_id != result.action_id:
        raise WorkflowRunError("journal action binding is invalid")

    outcome, _ = await _record_checked_action_result(
        coordinator,
        action_id,
        result,
        event_type="ACTION_RECONCILED",
    )
    if outcome.kind != "done":
        return await _finalize(coordinator, outcome)
    latest_state = await coordinator.state()
    if _execution_is_active(latest_state):
        # This is a control-plane transition.  The persisted executor will
        # observe the resolved action and finish the run without a second
        # scheduler advancing the same definition.
        return coordinator.snapshot
    # Once every previously unknown action has a durable result, the run is
    # again eligible for the journal's ordinary bounded continuation scan.
    # This is deliberately a status-only transition: the result itself was
    # already committed above, and a host need not retain an unbounded
    # in-memory notification list merely to resume it later.
    if coordinator.snapshot.status == "RECONCILING" and not any(
        isinstance(candidate, Mapping)
        and candidate.get("status") in _UNKNOWN_ACTION_STATES
        for candidate in latest_state["actions"].values()
    ):
        await coordinator.commit(
            "RECONCILIATION_READY",
            {"action_id": action_id},
            status="RUNNING",
        )
    if not advance:
        return coordinator.snapshot
    return await _drive(engine, coordinator.snapshot, workflow, validators)


async def stream_v3_events(
    engine: WorkflowRuntimePort,
    run_id: str,
    *,
    after: int = 0,
    expected_execution_id: str | None = None,
):
    anyio, _, _, _ = _optional_dependencies()
    if not isinstance(after, int) or isinstance(after, bool) or after < 0:
        raise WorkflowRunError("event cursor must be a non-negative integer")
    cursor = after
    page_size = 128
    idle_delay = 0.05
    # Validate before registering a watcher.  A reused ``run_id`` then fails
    # as an execution mismatch instead of leaving a stale subscriber asleep.
    await anyio.to_thread.run_sync(
        lambda: _journal_status(
            engine.workflow_services.journal, run_id, expected_execution_id
        )
    )
    send, receive = anyio.create_memory_object_stream(2)
    with engine.workflow_services.active_lock:
        engine.workflow_services.event_watchers.setdefault(run_id, []).append(send)
    try:
        async with send, receive:
            while True:
                events = await anyio.to_thread.run_sync(
                    lambda cursor=cursor: _journal_events(
                        engine.workflow_services.journal,
                        run_id,
                        after=cursor,
                        limit=page_size,
                        expected_execution_id=expected_execution_id,
                    )
                )
                for event in events:
                    cursor = event.sequence
                    yield event
                if events:
                    idle_delay = 0.05
                    if len(events) == page_size:
                        continue
                status = await anyio.to_thread.run_sync(
                    lambda: _journal_status(
                        engine.workflow_services.journal, run_id, expected_execution_id
                    )
                )
                if status.status in _TERMINAL and cursor >= status.last_sequence:
                    return
                with anyio.move_on_after(idle_delay) as scope:
                    await receive.receive()
                if scope.cancel_called:
                    idle_delay = min(idle_delay * 2, 1.0)
                else:
                    idle_delay = 0.05
    finally:
        with engine.workflow_services.active_lock:
            watchers = engine.workflow_services.event_watchers.get(run_id, [])
            if send in watchers:
                watchers.remove(send)
            if not watchers:
                engine.workflow_services.event_watchers.pop(run_id, None)


def _validate_async_drivers(
    engine: WorkflowRuntimePort,
    workflow: WorkflowSpec,
    visiting: set[tuple[str, str]] | None = None,
    *,
    workflows: Mapping[tuple[str, str], WorkflowSpec] | None = None,
) -> None:
    catalog = engine.workflow_services.workflows if workflows is None else workflows
    visiting = set() if visiting is None else visiting
    identity = (workflow.workflow_id, workflow.version)
    if identity in visiting:
        return
    visiting.add(identity)
    for step in _walk(workflow.steps):
        if step.kind is StepKind.AGENT:
            driver = engine.workflow_services.drivers.get(str(step.config["driver"]))
            if driver is None or not callable(getattr(driver, "decide_async", None)):
                raise WorkflowValidationError(
                    "v3 requires an AsyncAgentDriver; synchronous custom drivers support only v1/v2"
                )
        elif step.kind is StepKind.SUBWORKFLOW:
            child = catalog.get(
                (str(step.config["workflow_id"]), str(step.config["version"]))
            )
            if child is not None:
                _validate_async_drivers(engine, child, visiting, workflows=catalog)
    visiting.remove(identity)


def _walk(steps: Sequence[StepSpec]):
    for step in steps:
        yield step
        for branch in step.children:
            yield from _walk(branch)


def _execution_is_active(state: Mapping[str, Any]) -> bool:
    execution = state["v3"].get("execution")
    return bool(
        isinstance(execution, Mapping)
        and isinstance(execution.get("owner_id"), str)
        and isinstance(execution.get("lease_expires"), (int, float))
        and float(execution["lease_expires"]) > time.time()
    )


async def _claim_execution(
    engine: WorkflowRuntimePort, snapshot: RunSnapshot
) -> tuple[RunSnapshot, str, int]:
    anyio, _, _, _ = _optional_dependencies()
    owner_id = f"executor-{uuid4().hex}"
    for _ in range(8):

        def claim() -> tuple[RunSnapshot, int]:
            with engine.workflow_services.transition_lock:
                current = engine.workflow_services.journal.get(snapshot.run_id)
                if current.status in _TERMINAL:
                    raise RunConflictError("completed v3 runs cannot be executed")
                if current.status == BACKUP_RECOVERY_STATUS:
                    raise WorkflowRunError("BACKUP_RECOVERY_REQUIRED")
                defaulted_paths: set[tuple[str, ...]] = set()
                state = _state(current, defaulted_paths=defaulted_paths)
                if _execution_is_active(state):
                    raise WorkflowRunError(
                        "v3 run already has an active persisted executor"
                    )
                edit = StateEdit(state, initial_changed_paths=defaulted_paths)
                execution = edit.read(("v3", "execution"))
                assert isinstance(execution, dict)
                generation = int(execution["generation"]) + 1
                execution.update(
                    {
                        "generation": generation,
                        "owner_id": owner_id,
                        "lease_expires": time.time() + _EXECUTION_LEASE_SECONDS,
                    }
                )
                edit.set(("v3", "execution"), execution)
                updated = current.next_revision(
                    state=state, _state_write_set=edit.write_set()
                )
                saved = engine.workflow_services.journal.replace(
                    updated,
                    expected_revision=current.revision,
                    event_type="EXECUTOR_CLAIMED",
                    payload={"generation": generation},
                )
                return saved, generation

        try:
            saved, generation = await anyio.to_thread.run_sync(claim)
        except RunConflictError:
            await anyio.sleep(0)
            continue
        engine.workflow_services.notify_event(snapshot.run_id)
        return saved, owner_id, generation
    raise RunConflictError("could not claim the current v3 execution generation")


async def _monitor_persisted_control(
    coordinator: _Coordinator,
    token: CancellationToken,
    done: Any,
) -> None:
    anyio, _, _, _ = _optional_dependencies()
    next_heartbeat = time.monotonic() + _EXECUTION_HEARTBEAT_SECONDS
    consecutive_busy_reads = 0
    while not done.is_set():
        with anyio.move_on_after(_CONTROL_POLL_SECONDS):
            await done.wait()
        if done.is_set():
            return
        try:
            control_method = getattr(
                coordinator.engine.workflow_services.journal, "control", None
            )
            if callable(control_method):
                control = await anyio.to_thread.run_sync(
                    control_method, coordinator.snapshot.run_id
                )
                if (
                    control.execution_owner_id != coordinator.owner_id
                    or control.execution_generation != coordinator.generation
                ):
                    token.request()
                    return
                cancel_requested = control.cancel_requested
            else:
                state = await coordinator.state()
                cancel_requested = bool(state["v3"]["cancel_requested"])
        except RunConflictError:
            token.request()
            return
        except RunJournalBusyError:
            # SQLite already waited at its busy timeout. A single long writer
            # must not abort an otherwise healthy execution, but repeated
            # unreadable control must not hide cancellation or lost ownership.
            consecutive_busy_reads += 1
            if consecutive_busy_reads <= 2:
                continue
            raise
        consecutive_busy_reads = 0
        if cancel_requested:
            token.request()
        if time.monotonic() >= next_heartbeat:
            try:
                await coordinator.commit(
                    "EXECUTOR_HEARTBEAT",
                    {"generation": int(coordinator.generation or 0)},
                )
            except RunConflictError:
                # Finalization releases the owner before _drive sets done.
                # A heartbeat already in flight may observe that release.
                # Lost ownership during active work likewise stops this monitor.
                token.request()
                return
            next_heartbeat = time.monotonic() + _EXECUTION_HEARTBEAT_SECONDS


async def _release_execution(coordinator: _Coordinator, event_type: str) -> None:
    def release(edit: StateEdit) -> None:
        if edit.read(("v3", "execution", "owner_id")) != coordinator.owner_id:
            raise RunConflictError("v3 execution generation is no longer current")
        edit.set(("v3", "execution", "owner_id"), None)
        edit.set(("v3", "execution", "lease_expires"), None)

    await coordinator.commit(
        event_type,
        {"generation": int(coordinator.generation or 0)},
        ExplicitStateMutation(release),
    )


async def _drive(
    engine: WorkflowRuntimePort,
    snapshot: RunSnapshot,
    workflow: WorkflowSpec,
    validators: Mapping[str, Any],
) -> RunSnapshot:
    anyio, _, _, _ = _optional_dependencies()
    with engine.workflow_services.active_lock:
        if snapshot.run_id in engine.workflow_services.active_tokens:
            raise WorkflowRunError("v3 run is already active in this process")
    claimed, owner_id, generation = await _claim_execution(engine, snapshot)
    coordinator = _Coordinator(
        engine, claimed, owner_id=owner_id, generation=generation
    )
    token = CancellationToken()
    with engine.workflow_services.active_lock:
        engine.workflow_services.active_tokens[claimed.run_id] = token
    done = anyio.Event()
    try:
        async with anyio.create_task_group() as group:
            group.start_soon(_monitor_persisted_control, coordinator, token, done)
            try:
                result = await _drive_owned(
                    engine, coordinator, workflow, validators, token
                )
            finally:
                done.set()
        return result
    except BaseException:
        try:
            await _release_execution(coordinator, "EXECUTOR_ABORTED")
        except (RunConflictError, WorkflowRunError):
            pass
        raise
    finally:
        with engine.workflow_services.active_lock:
            if engine.workflow_services.active_tokens.get(claimed.run_id) is token:
                del engine.workflow_services.active_tokens[claimed.run_id]


async def _drive_owned(
    engine: WorkflowRuntimePort,
    coordinator: _Coordinator,
    workflow: WorkflowSpec,
    validators: Mapping[str, Any],
    token: CancellationToken,
) -> RunSnapshot:
    anyio, _, _, _ = _optional_dependencies()
    state = await coordinator.state()
    # A persisted cancellation stops new work, not result observation for an
    # Action that was already dispatched.  A resumed CANCEL_REQUESTED run gets
    # a fresh query token so its durable cancellation intent cannot suppress
    # reconciliation forever.  Conversely, a cancellation that arrives while
    # a live recovery round is in progress still interrupts that round.
    recovery_token = CancellationToken() if state["v3"]["cancel_requested"] else token
    reconciled = await _reconcile_dispatched(
        engine, coordinator, workflow, recovery_token
    )
    if reconciled is not None:
        return await _finalize(coordinator, reconciled)
    state = await coordinator.state()
    if state["failure"] is not None:
        return await _finalize(coordinator, _Outcome("failed"))
    if state["v3"]["cancel_requested"]:
        return await _finalize(coordinator, _Outcome("cancelled"))
    deadline = state["v3"]["deadline"]
    remaining = None if deadline is None else float(deadline) - time.time()
    if remaining is not None and remaining <= 0:

        def run_expired(state: dict[str, Any]) -> None:
            _mark_model_usage_unknown(state)
            _failure_mutation("RUN_TIMEOUT")(state)

        await coordinator.commit(
            "RUN_TIMEOUT",
            {"error_code": "RUN_TIMEOUT"},
            run_expired,
        )
        return await _finalize(coordinator, _Outcome("failed"))
    assert workflow.execution_policy is not None
    limiter = anyio.CapacityLimiter(workflow.execution_policy.max_concurrency)
    try:
        if remaining is None:
            outcome, visible = await _execute_sequence(
                engine,
                coordinator,
                workflow,
                workflow.steps,
                "",
                {},
                coordinator.snapshot.inputs,
                coordinator.snapshot.inputs,
                token,
                limiter,
                (),
                validators,
            )
        else:
            with anyio.fail_after(remaining):
                outcome, visible = await _execute_sequence(
                    engine,
                    coordinator,
                    workflow,
                    workflow.steps,
                    "",
                    {},
                    coordinator.snapshot.inputs,
                    coordinator.snapshot.inputs,
                    token,
                    limiter,
                    (),
                    validators,
                )
    except TimeoutError:
        token.request()

        def run_timed_out(state: dict[str, Any]) -> None:
            _mark_model_usage_unknown(state)
            _failure_mutation("RUN_TIMEOUT")(state)

        await coordinator.commit(
            "RUN_TIMEOUT",
            {"error_code": "RUN_TIMEOUT"},
            run_timed_out,
        )
        return await _finalize(coordinator, _Outcome("failed"))
    if outcome.kind == "done":
        try:
            output = await _resolve_bindings(
                coordinator,
                workflow.output_bindings,
                visible,
                coordinator.snapshot.inputs,
                coordinator.snapshot.inputs,
            )
        except WorkflowRunError:
            await coordinator.commit(
                "WORKFLOW_OUTPUT_BINDING_INVALID",
                {"error_code": "WORKFLOW_OUTPUT_INVALID"},
                _failure_mutation("WORKFLOW_OUTPUT_INVALID"),
            )
            return await _finalize(coordinator, _Outcome("failed"))
        validator = validators[
            f"workflow:{(workflow.workflow_id, workflow.version)}:output"
        ]
        issue = _validation_issue(validator, output)
        if issue is not None:
            await coordinator.commit(
                "WORKFLOW_OUTPUT_INVALID",
                {"path": issue[0], "keyword": issue[1]},
                _validation_failure_mutation(
                    "WORKFLOW_OUTPUT_INVALID", workflow.workflow_id, issue
                ),
            )
            return await _finalize(coordinator, _Outcome("failed"))

        def save_output(edit: StateEdit) -> None:
            edit.set(("v3", "output"), output)
            edit.set(("values",), output)

        try:
            await coordinator.commit(
                "WORKFLOW_OUTPUT_ACCEPTED",
                {},
                ExplicitStateMutation(save_output),
                enforce_retained_budget=True,
            )
        except _RetainedStateTooLargeError:
            await coordinator.commit(
                "RUN_RETAINED_STATE_TOO_LARGE",
                {"path": "", "keyword": "maxRunBytes"},
                _validation_failure_mutation(
                    "RUN_RETAINED_STATE_TOO_LARGE",
                    workflow.workflow_id,
                    ("", "maxRunBytes"),
                ),
            )
            return await _finalize(coordinator, _Outcome("failed"))
    try:
        return await _finalize(coordinator, outcome)
    except _WaitingDecisionConsumedError:
        return await _drive_owned(engine, coordinator, workflow, validators, token)


async def _reconcile_dispatched(
    engine: WorkflowRuntimePort,
    coordinator: _Coordinator,
    workflow: WorkflowSpec,
    token: CancellationToken,
) -> _Outcome | None:
    """Query every open action once without allowing one query to starve peers.

    A reconciliation query is observational: it must never redispatch an
    Action.  Queries run independently so an unavailable provider cannot hide
    a later action whose result is available.  Both limits apply even after a
    run deadline, because an already-dispatched side effect still needs a
    bounded opportunity to become known.
    """

    anyio, _, _, _ = _optional_dependencies()
    state = await coordinator.state()
    unresolved = sorted(
        (
            (action_id, action)
            for action_id, action in state["actions"].items()
            if isinstance(action, Mapping)
            and action.get("status") in _UNKNOWN_ACTION_STATES
        ),
        key=lambda item: item[0],
    )
    if not unresolved:
        return None
    query_timeout, round_timeout = _recovery_limits(workflow)
    observations: dict[str, _RecoveryObservation] = {}
    started: set[str] = set()
    completed = anyio.Event()
    finished = 0

    async def query_one(action_id: str, action: Mapping[str, Any]) -> None:
        nonlocal finished
        started.add(action_id)
        try:
            capabilities = action.get("capabilities", {})
            if workflow.schema_version == V4_SCHEMA_VERSION and (
                not isinstance(capabilities, Mapping)
                or capabilities.get("supports_result_query") is not True
            ):
                observations[action_id] = _RecoveryObservation(
                    "unsupported", False, "RESULT_QUERY_UNSUPPORTED"
                )
                return
            request = ToolExecutionRequest.from_mapping(action["request"])
            try:
                with anyio.fail_after(query_timeout):
                    result = await _call_reconciliation_query(
                        engine.workflow_services.executor, request, token
                    )
            except TimeoutError:
                observations[action_id] = _RecoveryObservation(
                    "timeout", True, "RESULT_QUERY_TIMEOUT"
                )
                return
            except _RecoveryQueryCancelledError:
                observations[action_id] = _RecoveryObservation("cancelled", True)
                return
            except ToolExecutionRejectedError as error:
                observations[action_id] = _RecoveryObservation(
                    "error", True, _exception_code("TOOL_RECONCILIATION", error)
                )
                return
            except Exception as error:  # noqa: BLE001 - stable error fact only
                observations[action_id] = _RecoveryObservation(
                    "error", True, _exception_code("TOOL_RECONCILIATION", error)
                )
                return
            if result is None:
                observations[action_id] = _RecoveryObservation("unknown", True)
            elif (
                not isinstance(result, ToolExecutionResult)
                or result.action_id != action_id
            ):
                observations[action_id] = _RecoveryObservation(
                    "invalid", True, "INVALID_RECONCILIATION_RESULT"
                )
            else:
                observations[action_id] = _RecoveryObservation(
                    "resolved", True, result=result
                )
        finally:
            finished += 1
            if finished == len(unresolved):
                completed.set()

    with anyio.move_on_after(round_timeout) as round_scope:
        async with anyio.create_task_group() as group:
            for action_id, action in unresolved:
                group.start_soon(query_one, action_id, action)
            await completed.wait()
    round_timed_out = round_scope.cancel_called
    for action_id, _action in unresolved:
        if action_id not in observations:
            observations[action_id] = _RecoveryObservation(
                "round_timeout" if round_timed_out else "cancelled",
                action_id in started,
                "RECOVERY_ROUND_TIMEOUT" if round_timed_out else None,
            )

    # Commits remain serialized through the coordinator.  This makes journal
    # ordering deterministic while the external reads above remain concurrent.
    for action_id, _action in unresolved:
        observation = observations[action_id]
        if observation.result is not None:
            await _record_checked_action_result(
                coordinator,
                action_id,
                observation.result,
                event_type="ACTION_RECONCILED",
                recovery_observation=observation,
            )
            continue
        payload: dict[str, JsonValue] = {
            "recovery_result": observation.kind,
        }
        if observation.error_code is not None:
            payload["error_code"] = observation.error_code
        await _record_action_uncertainty(
            coordinator,
            action_id,
            payload,
            recovery_observation=observation,
        )

    latest = await coordinator.state()
    if any(
        isinstance(action, Mapping) and action.get("status") in _UNKNOWN_ACTION_STATES
        for action in latest["actions"].values()
    ):
        return _Outcome("reconciling")
    return None


async def _call_reconciliation_query(
    executor: Any, request: ToolExecutionRequest, token: CancellationToken
) -> ToolExecutionResult | None:
    """Race a pure result query against durable cancellation.

    Sync queries use AnyIO's worker adapter with abandonment so a blocked
    third-party query cannot hold the scheduler.  The Action remains unknown;
    a late query result is deliberately discarded instead of being mistaken
    for a new tool execution.
    """

    anyio, _, _, _ = _optional_dependencies()
    if token.cancelled:
        raise _RecoveryQueryCancelledError
    send, receive = anyio.create_memory_object_stream(2)

    async def query() -> None:
        try:
            method = getattr(executor, "reconcile_async", None)
            if callable(method):
                value = method(request)
                if not inspect.isawaitable(value):
                    raise ToolExecutionUncertainError(
                        "async reconciliation returned without awaiting"
                    )
                result = await value
            else:
                result = await anyio.to_thread.run_sync(
                    executor.reconcile, request, abandon_on_cancel=True
                )
            send.send_nowait(("result", result))
        except BaseException as error:  # noqa: BLE001 - forward query failure
            try:
                send.send_nowait(("error", error))
            except (
                anyio.BrokenResourceError,
                anyio.ClosedResourceError,
                anyio.WouldBlock,
            ):
                pass

    async def watch_cancel() -> None:
        await token.wait()
        try:
            send.send_nowait(("cancel", None))
        except (
            anyio.BrokenResourceError,
            anyio.ClosedResourceError,
            anyio.WouldBlock,
        ):
            pass

    async with send, receive, anyio.create_task_group() as group:
        group.start_soon(query)
        group.start_soon(watch_cancel)
        kind, value = await receive.receive()
        group.cancel_scope.cancel()
    if kind == "result":
        return value
    if kind == "error":
        raise value
    raise _RecoveryQueryCancelledError


async def _execute_sequence(
    engine: WorkflowRuntimePort,
    coordinator: _Coordinator,
    workflow: WorkflowSpec,
    steps: Sequence[StepSpec],
    prefix: str,
    incoming_visible: Mapping[str, str],
    workflow_input: Mapping[str, JsonValue],
    parent_input: Mapping[str, JsonValue],
    token: CancellationToken,
    limiter: Any,
    local_limiters: tuple[Any, ...],
    validators: Mapping[str, Any],
) -> tuple[_Outcome, dict[str, str]]:
    visible = dict(incoming_visible)
    tail: Mapping[str, JsonValue] = {}
    for step in steps:
        key = f"{prefix}{step.step_id}"
        state = await coordinator.state()
        if state["v3"]["cancel_requested"]:
            token.request()
            return _Outcome("cancelled"), visible
        if key in state["completed"]:
            output = state["v3"]["step_outputs"].get(key)
            if not isinstance(output, Mapping):
                raise WorkflowRunError("completed v3 step has no valid output")
            visible[step.step_id] = key
            tail = output
            continue
        outcome = await _execute_step(
            engine,
            coordinator,
            workflow,
            step,
            key,
            visible,
            workflow_input,
            parent_input,
            token,
            limiter,
            local_limiters,
            validators,
        )
        if outcome.kind != "done":
            return outcome, visible
        assert outcome.output is not None
        visible[step.step_id] = key
        tail = outcome.output
    return _Outcome("done", tail), visible


async def _execute_step(
    engine: WorkflowRuntimePort,
    coordinator: _Coordinator,
    workflow: WorkflowSpec,
    step: StepSpec,
    key: str,
    visible: Mapping[str, str],
    workflow_input: Mapping[str, JsonValue],
    parent_input: Mapping[str, JsonValue],
    token: CancellationToken,
    limiter: Any,
    local_limiters: tuple[Any, ...],
    validators: Mapping[str, Any],
) -> _Outcome:
    anyio, _, _, _ = _optional_dependencies()
    assert step.contract is not None
    try:
        node_input = await _resolve_bindings(
            coordinator,
            step.contract.bindings,
            visible,
            workflow_input,
            parent_input,
        )
    except WorkflowRunError:
        await coordinator.commit(
            "STEP_INPUT_BINDING_INVALID",
            {"step_id": key, "error_code": "STEP_INPUT_INVALID"},
            _failure_mutation("STEP_INPUT_INVALID", key),
        )
        return _Outcome("failed")
    if (
        step.contract.max_input_bytes is not None
        and _json_size_bytes(node_input) > step.contract.max_input_bytes
    ):
        await coordinator.commit(
            "STEP_INPUT_TOO_LARGE",
            {"step_id": key, "path": "", "keyword": "maxBytes"},
            _validation_failure_mutation("STEP_INPUT_TOO_LARGE", key, ("", "maxBytes")),
        )
        return _Outcome("failed")
    input_validator = validators[
        f"step:{workflow.workflow_id}:{workflow.version}:{step.step_id}:input"
    ]
    issue = _validation_issue(input_validator, node_input)
    if issue is not None:
        await coordinator.commit(
            "STEP_INPUT_INVALID",
            {"step_id": key, "path": issue[0], "keyword": issue[1]},
            _validation_failure_mutation("STEP_INPUT_INVALID", key, issue),
        )
        return _Outcome("failed")

    state = await coordinator.state()
    saved_timeout = state["v3"]["node_timeouts"].get(key)
    timeout_seconds = step.contract.timeout_seconds
    if isinstance(saved_timeout, Mapping):
        saved_deadline = saved_timeout.get("deadline")
        saved_remaining = saved_timeout.get("remaining")
        if isinstance(saved_deadline, (int, float)):
            timeout_seconds = max(0.0, float(saved_deadline) - time.time())
        elif isinstance(saved_remaining, (int, float)):
            timeout_seconds = float(saved_remaining)
    node_deadline = (
        time.time() + timeout_seconds if timeout_seconds is not None else None
    )

    def started(edit: StateEdit) -> None:
        edit.set(("v3", "step_states", key), "RUNNING")
        if edit.read(("v3", "step_started_at", key), default=_MISSING) is _MISSING:
            edit.set(("v3", "step_started_at", key), time.time())
        if timeout_seconds is not None:
            edit.set(
                ("v3", "node_timeouts", key),
                {"remaining": timeout_seconds, "deadline": node_deadline},
            )

    await coordinator.commit(
        "STEP_STARTED", {"step_id": key}, ExplicitStateMutation(started)
    )
    step_token = token.child()
    try:
        if timeout_seconds is None:
            outcome = await _execute_step_body(
                engine,
                coordinator,
                workflow,
                step,
                key,
                visible,
                workflow_input,
                node_input,
                step_token,
                limiter,
                local_limiters,
                validators,
            )
        else:
            with anyio.fail_after(max(timeout_seconds, 0.000001)):
                outcome = await _execute_step_body(
                    engine,
                    coordinator,
                    workflow,
                    step,
                    key,
                    visible,
                    workflow_input,
                    node_input,
                    step_token,
                    limiter,
                    local_limiters,
                    validators,
                )
    except TimeoutError:
        # AnyIO cannot stop a Python worker thread.  Request cooperative stop
        # on the exact token injected into a synchronous ToolContext, while the
        # already-dispatched action remains fail-closed until reconciled.
        step_token.request()
        state = await coordinator.state()
        unknown = any(
            isinstance(action, Mapping)
            and action.get("step_key") == key
            and action.get("status") in _UNKNOWN_ACTION_STATES
            for action in state["actions"].values()
        )

        def step_timed_out(state: dict[str, Any]) -> None:
            _mark_model_usage_unknown(state, step_key=key)
            _failure_mutation("STEP_TIMEOUT", key)(state)

        await coordinator.commit(
            "STEP_TIMEOUT",
            {"step_id": key, "error_code": "STEP_TIMEOUT"},
            step_timed_out,
        )
        return _Outcome("reconciling" if unknown else "failed")
    if outcome.kind == "waiting" and node_deadline is not None:

        def pause_timeout(edit: StateEdit) -> None:
            edit.set(
                ("v3", "node_timeouts", key),
                {
                    "remaining": max(0.000001, node_deadline - time.time()),
                    "deadline": None,
                },
            )

        await coordinator.commit(
            "STEP_TIMEOUT_PAUSED",
            {"step_id": key},
            ExplicitStateMutation(pause_timeout),
        )
    if outcome.kind != "done":
        return outcome
    output = dict(outcome.output or {})
    if (
        step.contract.max_output_bytes is not None
        and _json_size_bytes(output) > step.contract.max_output_bytes
    ):
        await coordinator.commit(
            "STEP_OUTPUT_TOO_LARGE",
            {"step_id": key, "path": "", "keyword": "maxBytes"},
            _validation_failure_mutation(
                "STEP_OUTPUT_TOO_LARGE", key, ("", "maxBytes")
            ),
        )
        return _Outcome("failed")
    latest = await coordinator.state()
    if key in latest["completed"]:
        persisted = latest["v3"]["step_outputs"].get(key)
        if not isinstance(persisted, Mapping) or dict(persisted) != output:
            raise RunConflictError(
                "completed v3 step output differs from the accepted result"
            )
        return _Outcome("done", dict(persisted))
    output_validator = validators[
        f"step:{workflow.workflow_id}:{workflow.version}:{step.step_id}:output"
    ]
    issue = _validation_issue(output_validator, output)
    if issue is not None:
        await coordinator.commit(
            "STEP_OUTPUT_INVALID",
            {"step_id": key, "path": issue[0], "keyword": issue[1]},
            _validation_failure_mutation("STEP_OUTPUT_INVALID", key, issue),
        )
        return _Outcome("failed")

    def completed(edit: StateEdit) -> None:
        edit.add_sorted_member(("completed",), key)
        edit.set(("v3", "step_outputs", key), output)
        edit.set(("v3", "step_states", key), "COMPLETED")
        edit.remove(("v3", "node_timeouts", key), missing_ok=True)
        edit.remove(("v3", "step_started_at", key), missing_ok=True)

    started_at = latest["v3"]["step_started_at"].get(key)
    duration = (
        max(0.0, time.time() - float(started_at))
        if isinstance(started_at, (int, float))
        else None
    )
    try:
        await coordinator.commit(
            "STEP_COMPLETED",
            {"step_id": key, "duration_seconds": duration},
            ExplicitStateMutation(completed),
            enforce_retained_budget=True,
        )
    except _RetainedStateTooLargeError:
        await coordinator.commit(
            "RUN_RETAINED_STATE_TOO_LARGE",
            {"step_id": key, "path": "", "keyword": "maxRunBytes"},
            _validation_failure_mutation(
                "RUN_RETAINED_STATE_TOO_LARGE", key, ("", "maxRunBytes")
            ),
        )
        return _Outcome("failed")
    return _Outcome("done", output)


async def _execute_step_body(
    engine: WorkflowRuntimePort,
    coordinator: _Coordinator,
    workflow: WorkflowSpec,
    step: StepSpec,
    key: str,
    visible: Mapping[str, str],
    workflow_input: Mapping[str, JsonValue],
    node_input: Mapping[str, JsonValue],
    token: CancellationToken,
    limiter: Any,
    local_limiters: tuple[Any, ...],
    validators: Mapping[str, Any],
) -> _Outcome:
    if step.kind is StepKind.TOOL:
        return await _execute_tool_step(
            engine,
            coordinator,
            workflow,
            step,
            key,
            node_input,
            token,
            limiter,
            local_limiters,
        )
    if step.kind is StepKind.AGENT:
        return await _execute_agent_step(
            engine,
            coordinator,
            workflow,
            step,
            key,
            node_input,
            visible,
            token,
            limiter,
            local_limiters,
            validators,
        )
    if step.kind is StepKind.APPROVAL:
        return await _execute_approval_step(coordinator, step, key)
    if step.kind is StepKind.CONDITION:
        condition_value = _pointer(node_input, str(step.config["path"]))
        if condition_value is _MISSING:
            await coordinator.commit(
                "CONDITION_INPUT_MISSING",
                {"step_id": key, "error_code": "STEP_INPUT_INVALID"},
                _failure_mutation("STEP_INPUT_INVALID", key),
            )
            return _Outcome("failed")
        selected = condition_value == step.config["equals"]
        branch_index = 0 if selected else 1
        branch_name = "then" if selected else "otherwise"

        def choose(edit: StateEdit) -> None:
            edit.set(("v3", "branch_states", key), {"selected": branch_name})

        await coordinator.commit(
            "CONDITION_SELECTED",
            {"step_id": key, "branch": branch_name},
            ExplicitStateMutation(choose),
        )
        outcome, _ = await _execute_sequence(
            engine,
            coordinator,
            workflow,
            step.children[branch_index],
            f"{key}/{branch_name}/",
            dict(visible),
            workflow_input,
            json_copy(node_input),
            token,
            limiter,
            local_limiters,
            validators,
        )
        if outcome.kind != "done":
            return outcome
        return _Outcome("done", outcome.output or {})
    if step.kind is StepKind.PARALLEL:
        return await _execute_parallel(
            engine,
            coordinator,
            workflow,
            step,
            key,
            visible,
            workflow_input,
            node_input,
            token,
            limiter,
            local_limiters,
            validators,
        )
    if step.kind is StepKind.SUBWORKFLOW:
        anyio, _, _, _ = _optional_dependencies()
        child = engine.workflow_services.manifest_workflows(coordinator.snapshot)[
            (str(step.config["workflow_id"]), str(step.config["version"]))
        ]
        assert child.execution_policy is not None
        child_limiter = anyio.CapacityLimiter(child.execution_policy.max_concurrency)
        state = await coordinator.state()
        timeout_state = state["v3"]["subworkflow_timeouts"].get(key)
        child_deadline: float | None = None
        if isinstance(timeout_state, Mapping) and isinstance(
            timeout_state.get("deadline"), (int, float)
        ):
            child_deadline = float(timeout_state["deadline"])
        elif child.execution_policy.run_timeout_seconds is not None:
            child_deadline = time.time() + child.execution_policy.run_timeout_seconds

            def start_child(edit: StateEdit) -> None:
                edit.set(
                    ("v3", "subworkflow_timeouts", key),
                    {"deadline": child_deadline},
                )

            await coordinator.commit(
                "SUBWORKFLOW_STARTED",
                {
                    "step_id": key,
                    "definition_digest": child.definition_digest,
                },
                ExplicitStateMutation(start_child),
            )
        remaining = None if child_deadline is None else child_deadline - time.time()
        if remaining is not None and remaining <= 0:
            await coordinator.commit(
                "SUBWORKFLOW_TIMEOUT",
                {"step_id": key, "error_code": "SUBWORKFLOW_TIMEOUT"},
                _failure_mutation("SUBWORKFLOW_TIMEOUT", key),
            )
            return _Outcome("failed")
        try:
            if remaining is None:
                outcome, child_visible = await _execute_sequence(
                    engine,
                    coordinator,
                    child,
                    child.steps,
                    f"{key}/subworkflow/",
                    {},
                    json_copy(node_input),
                    json_copy(node_input),
                    token,
                    limiter,
                    (*local_limiters, child_limiter),
                    validators,
                )
            else:
                with anyio.fail_after(remaining):
                    outcome, child_visible = await _execute_sequence(
                        engine,
                        coordinator,
                        child,
                        child.steps,
                        f"{key}/subworkflow/",
                        {},
                        json_copy(node_input),
                        json_copy(node_input),
                        token,
                        limiter,
                        (*local_limiters, child_limiter),
                        validators,
                    )
        except TimeoutError:
            latest = await coordinator.state()
            prefix = f"{key}/subworkflow/"
            unknown = any(
                isinstance(action, Mapping)
                and str(action.get("step_key", "")).startswith(prefix)
                and action.get("status") in _UNKNOWN_ACTION_STATES
                for action in latest["actions"].values()
            )
            await coordinator.commit(
                "SUBWORKFLOW_TIMEOUT",
                {"step_id": key, "error_code": "SUBWORKFLOW_TIMEOUT"},
                _failure_mutation("SUBWORKFLOW_TIMEOUT", key),
            )
            return _Outcome("reconciling" if unknown else "failed")
        if outcome.kind != "done":
            return outcome
        try:
            output = await _resolve_bindings(
                coordinator,
                child.output_bindings,
                child_visible,
                node_input,
                node_input,
            )
        except WorkflowRunError:
            await coordinator.commit(
                "SUBWORKFLOW_OUTPUT_INVALID",
                {"step_id": key, "error_code": "STEP_OUTPUT_INVALID"},
                _failure_mutation("STEP_OUTPUT_INVALID", key),
            )
            return _Outcome("failed")

        if child_deadline is not None:

            def finish_child(edit: StateEdit) -> None:
                edit.remove(("v3", "subworkflow_timeouts", key), missing_ok=True)

            await coordinator.commit(
                "SUBWORKFLOW_FINISHED",
                {"step_id": key},
                ExplicitStateMutation(finish_child),
            )
        return _Outcome("done", output)
    await coordinator.commit(
        "INVALID_V3_STEP", {"step_id": key}, _failure_mutation("INVALID_V3_STEP", key)
    )
    return _Outcome("failed")


async def _execute_parallel(
    engine: WorkflowRuntimePort,
    coordinator: _Coordinator,
    workflow: WorkflowSpec,
    step: StepSpec,
    key: str,
    visible: Mapping[str, str],
    workflow_input: Mapping[str, JsonValue],
    node_input: Mapping[str, JsonValue],
    token: CancellationToken,
    limiter: Any,
    ancestor_limiters: tuple[Any, ...],
    validators: Mapping[str, Any],
) -> _Outcome:
    anyio, _, _, _ = _optional_dependencies()
    local_limit = int(step.config.get("max_concurrency", len(step.children)))
    local_limiter = anyio.CapacityLimiter(local_limit)
    outcomes: list[_Outcome | None] = [None] * len(step.children)
    failure_policy = str(step.config.get("failure_policy", "fail_fast"))

    def fan_out(edit: StateEdit) -> None:
        existing = edit.read(("v3", "branch_states", key), default=None)
        if existing is None:
            existing = {}
        if not isinstance(existing, dict):
            raise WorkflowRunError("parallel branch state is malformed")
        for name in step.branch_names:
            existing.setdefault(name, "RUNNING")
        edit.set(("v3", "branch_states", key), existing)

    await coordinator.commit(
        "PARALLEL_FAN_OUT",
        {"step_id": key, "branches": list(step.branch_names)},
        ExplicitStateMutation(fan_out),
    )
    async with anyio.create_task_group() as group:

        async def run_branch(index: int) -> None:
            name = step.branch_names[index]
            try:
                outcome, _ = await _execute_sequence(
                    engine,
                    coordinator,
                    workflow,
                    step.children[index],
                    f"{key}/{name}/",
                    dict(visible),
                    workflow_input,
                    json_copy(node_input),
                    token,
                    limiter,
                    (*ancestor_limiters, local_limiter),
                    validators,
                )
                outcomes[index] = outcome

                def branch_state(edit: StateEdit) -> None:
                    edit.set(("v3", "branch_states", key, name), outcome.kind.upper())

                await coordinator.commit(
                    "PARALLEL_BRANCH_SETTLED",
                    {"step_id": key, "branch": name, "outcome": outcome.kind},
                    ExplicitStateMutation(branch_state),
                )
                if failure_policy == "fail_fast" and outcome.kind in {
                    "failed",
                    "reconciling",
                    "cancelled",
                }:
                    group.cancel_scope.cancel()
            except anyio.get_cancelled_exc_class():
                if outcomes[index] is None:
                    outcomes[index] = _Outcome("aborted")

        for index in range(len(step.children)):
            group.start_soon(run_branch, index)

    concrete = [item or _Outcome("aborted") for item in outcomes]
    if any(item.kind == "reconciling" for item in concrete):
        return _Outcome("reconciling")
    if any(item.kind == "cancelled" for item in concrete):
        return _Outcome("cancelled")
    if any(item.kind == "failed" for item in concrete):
        return _Outcome("failed")
    if any(item.kind == "waiting" for item in concrete):
        return _Outcome("waiting")
    if any(item.kind == "aborted" for item in concrete):
        return _Outcome("failed")
    join: dict[str, JsonValue] = {}
    for name, outcome in zip(step.branch_names, concrete, strict=True):
        join[name] = dict(outcome.output or {})
    return _Outcome("done", join)


async def _execute_approval_step(
    coordinator: _Coordinator, step: StepSpec, key: str
) -> _Outcome:
    state = await coordinator.state()
    if key in state["approved_steps"]:
        return _Outcome("done", {"approved": True})
    approval_id = _approval_id(coordinator.snapshot.execution_id, "step", key)
    pending = state["v3"]["pending_approvals"]
    if approval_id not in pending:

        def request(edit: StateEdit) -> None:
            edit.set(
                ("v3", "pending_approvals", approval_id),
                {
                    "kind": "STEP",
                    "step_key": key,
                    "prompt": step.config["prompt"],
                },
            )

        await coordinator.commit(
            "APPROVAL_REQUESTED",
            {
                "approval_id": approval_id,
                "step_id": key,
                "prompt": step.config["prompt"],
            },
            ExplicitStateMutation(request),
        )
    return _Outcome("waiting")


async def _execute_tool_step(
    engine: WorkflowRuntimePort,
    coordinator: _Coordinator,
    workflow: WorkflowSpec,
    step: StepSpec,
    key: str,
    node_input: Mapping[str, JsonValue],
    token: CancellationToken,
    limiter: Any,
    local_limiters: tuple[Any, ...],
) -> _Outcome:
    action_id = _action_id(coordinator.snapshot.execution_id, key, 0)
    state = await coordinator.state()
    if action_id not in state["actions"]:
        request = ToolExecutionRequest(
            action_id,
            # The v3 contract binds the complete validated node input directly.
            ToolReference.from_mapping(step.config["tool"]),
            dict(node_input),
        )
        approval_required = bool(step.config.get("approval_required", True))
        approval_id = _approval_id(
            coordinator.snapshot.execution_id, "action", action_id
        )

        def propose(edit: StateEdit) -> None:
            edit.set(
                ("actions", action_id),
                {
                    "request": request.to_mapping(),
                    "status": "PENDING_APPROVAL" if approval_required else "READY",
                    "step_key": key,
                    "source": "tool",
                    "output_schema": dict(step.contract.output_schema),
                    "output_error_code": "STEP_OUTPUT_INVALID",
                    "max_output_bytes": step.contract.max_output_bytes,
                    "capabilities": dict(step.config.get("capabilities", {})),
                },
            )
            if approval_required:
                edit.set(
                    ("v3", "pending_approvals", approval_id),
                    {
                        "kind": "ACTION",
                        "approval_id": approval_id,
                        "action_id": action_id,
                        "step_key": key,
                    },
                )

        try:
            await coordinator.commit(
                "ACTION_PROPOSED" if approval_required else "ACTION_READY",
                {
                    "step_id": key,
                    "action_id": action_id,
                    "approval_id": approval_id if approval_required else None,
                    "tool_id": request.tool.tool_id,
                },
                ExplicitStateMutation(propose),
                enforce_retained_budget=True,
            )
        except _RetainedStateTooLargeError:
            await coordinator.commit(
                "RUN_RETAINED_STATE_TOO_LARGE",
                {"step_id": key, "path": "", "keyword": "maxRunBytes"},
                _validation_failure_mutation(
                    "RUN_RETAINED_STATE_TOO_LARGE", key, ("", "maxRunBytes")
                ),
            )
            return _Outcome("failed")
        if approval_required:
            return _Outcome("waiting")
    outcome, result = await _advance_action(
        engine,
        coordinator,
        workflow,
        action_id,
        token,
        limiter,
        local_limiters,
    )
    if outcome.kind != "done":
        return outcome
    assert result is not None
    if not result.succeeded:
        await coordinator.commit(
            "TOOL_FAILED",
            {"step_id": key, "action_id": action_id, "error_code": result.error},
            _failure_mutation("TOOL_FAILED", key),
        )
        return _Outcome("failed")
    return _Outcome("done", result.output or {})


async def _advance_action(
    engine: WorkflowRuntimePort,
    coordinator: _Coordinator,
    workflow: WorkflowSpec,
    action_id: str,
    token: CancellationToken,
    limiter: Any,
    local_limiters: tuple[Any, ...],
) -> tuple[_Outcome, ToolExecutionResult | None]:
    state = await coordinator.state()
    action = state["actions"].get(action_id)
    if not isinstance(action, Mapping):
        raise WorkflowRunError("v3 action is missing")
    status = action.get("status")
    if status == "PENDING_APPROVAL":
        return _Outcome("waiting"), None
    if status in _UNKNOWN_ACTION_STATES:
        return _Outcome("reconciling"), None
    if status == "RESOLVED":
        return _Outcome("done"), ToolExecutionResult.from_mapping(action["result"])
    if status != "READY":
        raise WorkflowRunError("v3 action has an unknown status")
    request = ToolExecutionRequest.from_mapping(action["request"])
    if token.cancelled:
        return _Outcome("cancelled"), None
    queue_started = time.perf_counter()
    async with AsyncExitStack() as stack:
        await stack.enter_async_context(limiter)
        for local in local_limiters:
            await stack.enter_async_context(local)
        resource = _action_mutex_resource(action)
        resource_handed_off = False
        if resource is not None:
            await engine.workflow_services.acquire_resource(resource, action_id)

            def release_pre_dispatch_resource() -> None:
                if not resource_handed_off:
                    _release_action_resource(engine, action_id, action)

            # Any cancellation/error while acquiring a later host capacity
            # token is still pre-dispatch.  It must not strand this local
            # resource lease merely because no executor call was reached.
            stack.callback(release_pre_dispatch_resource)
        # A host may impose one more process-wide tool bound.  Reserve it
        # here, after every workflow/resource queue and before the final
        # authorization check.  This is deliberately a core hook rather than
        # a post-authorization executor decorator: a revoked grant must never
        # survive a hidden capacity wait.
        try:
            await engine.workflow_services.reserve_tool_dispatch(request)
        except Exception as error:  # noqa: BLE001 - host admission is fail-closed
            _release_action_resource(engine, action_id, action)
            await coordinator.commit(
                "ACTION_ADMISSION_ERROR",
                {
                    "action_id": action_id,
                    "error_code": _exception_code("ACTION_ADMISSION", error),
                },
                _failure_mutation("ACTION_ADMISSION_ERROR", str(action["step_key"])),
            )
            return _Outcome("failed"), None
        # Started calls retain a host lease themselves until their actual
        # async operation or abandoned worker thread ends.  The callback only
        # releases a reservation that never reached that boundary.
        stack.push_async_callback(
            engine.workflow_services.release_tool_dispatch, request
        )
        queue_seconds = max(0.0, time.perf_counter() - queue_started)
        if token.cancelled:
            _release_action_resource(engine, action_id, action)
            return _Outcome("cancelled"), None
        # Authorization is intentionally the last check after queueing.  A
        # grant observed before a capacity wait must not survive revocation.
        try:
            authorization = engine.workflow_services.policy.authorize(
                request,
                run_id=coordinator.snapshot.run_id,
                workflow=workflow,
                step_id=str(action["step_key"]),
            )
        except Exception as error:  # noqa: BLE001
            _release_action_resource(engine, action_id, action)
            await coordinator.commit(
                "ACTION_AUTHORIZATION_ERROR",
                {
                    "action_id": action_id,
                    "error_code": _exception_code("ACTION_AUTHORIZATION", error),
                },
                _failure_mutation(
                    "ACTION_AUTHORIZATION_ERROR", str(action["step_key"])
                ),
            )
            return _Outcome("failed"), None
        if (
            not isinstance(authorization, ActionAuthorization)
            or not authorization.allowed
        ):
            _release_action_resource(engine, action_id, action)
            await coordinator.commit(
                "ACTION_DENIED",
                {
                    "action_id": action_id,
                    "reason_code": authorization.reason
                    if isinstance(authorization, ActionAuthorization)
                    else "INVALID_POLICY_RESULT",
                },
                _failure_mutation("ACTION_DENIED", str(action["step_key"])),
            )
            return _Outcome("failed"), None

        def dispatched(edit: StateEdit) -> None:
            if edit.read(("v3", "cancel_requested")):
                raise _TransitionAbortedError
            if edit.read(("actions", action_id, "status")) != "READY":
                raise _TransitionAbortedError
            edit.set(("actions", action_id, "status"), "DISPATCHED")

        try:
            await coordinator.commit(
                "ACTION_DISPATCHED",
                {
                    "step_id": action["step_key"],
                    "action_id": action_id,
                    "queue_seconds": queue_seconds,
                },
                ExplicitStateMutation(dispatched),
            )
            resource_handed_off = True
        except _TransitionAbortedError:
            latest = await coordinator.state()
            if latest["v3"]["cancel_requested"]:
                _release_action_resource(engine, action_id, action)
                token.request()
                return _Outcome("cancelled"), None
            latest_action = latest["actions"].get(action_id)
            if (
                isinstance(latest_action, Mapping)
                and latest_action.get("status") in _UNKNOWN_ACTION_STATES
            ):
                return _Outcome("reconciling"), None
            raise WorkflowRunError("action changed before dispatch") from None
        if token.cancelled:
            return await _record_action_closure(
                coordinator,
                action_id,
                status="STOPPED",
                event_type="ACTION_STOPPED_BEFORE_CALL",
                payload={},
                outcome_kind="cancelled",
            )
        try:
            result = await _call_executor(
                engine.workflow_services.executor, request, token
            )
        except ToolExecutionStoppedError:
            anyio, _, _, _ = _optional_dependencies()
            with anyio.CancelScope(shield=True):
                return await _record_action_closure(
                    coordinator,
                    action_id,
                    status="STOPPED",
                    event_type="ACTION_STOPPED",
                    payload={},
                    outcome_kind="cancelled" if token.cancelled else "failed",
                    failure_code=(
                        None if token.cancelled else "TOOL_EXECUTION_STOPPED"
                    ),
                )
        except ToolExecutionRejectedError as error:
            return await _record_action_closure(
                coordinator,
                action_id,
                status="REJECTED",
                event_type="TOOL_REJECTED",
                payload={"error_code": error.code},
                outcome_kind="failed",
                failure_code=error.code,
            )
        except ToolExecutionFailedError as error:
            result = ToolExecutionResult(action_id, False, error=error.code)
        except BaseException as error:  # noqa: BLE001 - cancellation is unknown
            anyio, _, _, _ = _optional_dependencies()
            with anyio.CancelScope(shield=True):
                uncertain = await _record_action_uncertainty(
                    coordinator,
                    action_id,
                    {
                        "error_code": _exception_code("TOOL", error),
                    },
                )
            if isinstance(error, anyio.get_cancelled_exc_class()):
                # A step timeout or branch cancellation must reach a sync
                # ToolContext even though Python cannot terminate its worker.
                # Preserve the unknown Action fact, then let the enclosing
                # AnyIO scope classify the timeout/cancellation itself.
                token.request()
                raise
            return uncertain
    if not isinstance(result, ToolExecutionResult) or result.action_id != action_id:
        return await _record_action_uncertainty(
            coordinator, action_id, {"error_code": "INVALID_TOOL_RESULT"}
        )

    return await _record_checked_action_result(coordinator, action_id, result)


async def _call_executor(
    executor: Any, request: ToolExecutionRequest, token: CancellationToken
) -> ToolExecutionResult:
    anyio, _, _, _ = _optional_dependencies()
    # A mixed registry exposes native async capability only for the exact
    # registered async tool.  Keeping this separate from ``execute_async``
    # lets a host retain physical-worker accounting for synchronous siblings.
    native = getattr(executor, "native_execute_async", None)
    if callable(native):
        native_result = native(request, token)
        if native_result is not None:
            if not inspect.isawaitable(native_result):
                raise ToolExecutionUncertainError(
                    "native async executor returned without awaiting"
                )
            return await native_result
    method = getattr(executor, "execute_async", None)
    if callable(method):
        result = method(request, token)
        if not inspect.isawaitable(result):
            raise ToolExecutionUncertainError(
                "async executor returned without awaiting"
            )
        return await result

    send, receive = anyio.create_memory_object_stream(1)

    async def execute_sync() -> None:
        try:
            cancellable = getattr(executor, "execute_with_cancellation", None)
            call = cancellable if callable(cancellable) else executor.execute
            result = (
                await anyio.to_thread.run_sync(
                    call, request, token, abandon_on_cancel=True
                )
                if callable(cancellable)
                else await anyio.to_thread.run_sync(
                    call, request, abandon_on_cancel=True
                )
            )
            await send.send(("result", result))
        except BaseException as error:  # noqa: BLE001
            await send.send(("error", error))

    async def watch_cancel() -> None:
        await token.wait()
        await send.send(("cancel", None))

    async with send, receive, anyio.create_task_group() as group:
        group.start_soon(execute_sync)
        group.start_soon(watch_cancel)
        kind, value = await receive.receive()
        group.cancel_scope.cancel()
    if kind == "result":
        return value
    if kind == "error":
        raise value
    raise ToolExecutionUncertainError(
        "synchronous tool cancellation after dispatch has an unknown result"
    )


async def _call_agent_driver(
    driver: Any, context: AgentContext, token: CancellationToken
) -> AgentDecision:
    """Run a native async model request inside the existing AnyIO task core."""

    anyio, _, _, _ = _optional_dependencies()
    if token.cancelled:
        raise _ModelRequestCancelledError
    send, receive = anyio.create_memory_object_stream(2)

    async def invoke() -> None:
        try:
            decision = await driver.decide_async(context)
            send.send_nowait(("result", decision))
        except BaseException as error:  # noqa: BLE001 - forward SDK cancellation/failure
            kind = (
                "cancel"
                if isinstance(error, anyio.get_cancelled_exc_class())
                else "error"
            )
            try:
                send.send_nowait((kind, error))
            except (
                anyio.BrokenResourceError,
                anyio.ClosedResourceError,
                anyio.WouldBlock,
            ):
                pass

    async def watch_cancel() -> None:
        await token.wait()
        try:
            send.send_nowait(("cancel", None))
        except (
            anyio.BrokenResourceError,
            anyio.ClosedResourceError,
            anyio.WouldBlock,
        ):
            pass

    async with send, receive, anyio.create_task_group() as group:
        group.start_soon(invoke)
        group.start_soon(watch_cancel)
        kind, value = await receive.receive()
        group.cancel_scope.cancel()
    if kind == "result":
        return value
    if kind == "error":
        raise value
    raise _ModelRequestCancelledError


async def _execute_agent_step(
    engine: WorkflowRuntimePort,
    coordinator: _Coordinator,
    workflow: WorkflowSpec,
    step: StepSpec,
    key: str,
    node_input: Mapping[str, JsonValue],
    visible: Mapping[str, str],
    token: CancellationToken,
    limiter: Any,
    local_limiters: tuple[Any, ...],
    validators: Mapping[str, Any],
) -> _Outcome:
    anyio, _, _, _ = _optional_dependencies()
    tool_specs = tuple(
        AgentToolSpec.from_mapping(item) for item in step.config.get("tools", [])
    )
    budget = (
        AgentBudget.from_mapping(step.config["budget"])
        if workflow.schema_version == V4_SCHEMA_VERSION
        else None
    )
    by_identity = {
        (item.tool.tool_id, item.tool.version, item.tool.package_digest): item
        for item in tool_specs
    }
    state = await coordinator.state()
    agent_state = state["agents"].get(key)
    if not isinstance(agent_state, Mapping):

        def initialize(edit: StateEdit) -> None:
            edit.set(
                ("agents", key),
                {
                    "turns": 0,
                    "actions": [],
                    "history": [],
                    "driver_continuation": None,
                    "model_invocations": {},
                    "usage": {
                        # ``turns`` below counts accepted agent decisions.  The
                        # counters here separately track SDK model requests and
                        # lower-level transport attempts when a driver can prove
                        # them; unknown never means zero.
                        "model_requests": 0,
                        "driver_invocations": 0,
                        "model_requests_unknown": False,
                        "transport_attempts": 0,
                        "transport_attempts_unknown": False,
                        "tool_calls": 0,
                        "context_bytes": 0,
                        "total_tokens": 0,
                        "tokens_unknown": False,
                    },
                },
            )

        await coordinator.commit(
            "AGENT_STARTED", {"step_id": key}, ExplicitStateMutation(initialize)
        )
        state = await coordinator.state()
        agent_state = state["agents"][key]
    invocations = agent_state.get("model_invocations")
    if invocations is None:

        def add_invocation_ledger(edit: StateEdit) -> None:
            current = edit.read(("agents", key))
            assert isinstance(current, dict)
            edit.set(
                ("agents", key, "model_invocations"),
                current.get("model_invocations", {}),
            )

        await coordinator.commit(
            "AGENT_INVOCATION_LEDGER_INITIALIZED",
            {"step_id": key},
            ExplicitStateMutation(add_invocation_ledger),
        )
        state = await coordinator.state()
        agent_state = state["agents"][key]
        invocations = agent_state["model_invocations"]
    if not isinstance(invocations, dict):
        raise WorkflowRunError("Agent model invocation state is malformed")
    dispatched_invocations = [
        invocation_id
        for invocation_id, invocation in invocations.items()
        if isinstance(invocation, Mapping) and invocation.get("status") == "DISPATCHED"
    ]
    if dispatched_invocations:

        def mark_unknown_usage(edit: StateEdit) -> None:
            for invocation_id in dispatched_invocations:
                path = ("agents", key, "model_invocations", invocation_id)
                invocation = edit.read(path, default=None)
                if (
                    isinstance(invocation, dict)
                    and invocation.get("status") == "DISPATCHED"
                ):
                    edit.set((*path, "status"), "USAGE_UNKNOWN")
            edit.set(("agents", key, "usage", "tokens_unknown"), True)
            edit.set(("agents", key, "usage", "model_requests_unknown"), True)
            edit.set(("agents", key, "usage", "transport_attempts_unknown"), True)
            # A dispatched model request may have completed and incurred cost
            # before the host crashed. Repeating it is unsafe even when this
            # older workflow has no explicit token budget.
            edit.set(("failure",), {"code": "AGENT_USAGE_UNKNOWN", "step_id": key})

        await coordinator.commit(
            "AGENT_REQUEST_USAGE_UNKNOWN",
            {"step_id": key, "invocation_ids": dispatched_invocations},
            ExplicitStateMutation(mark_unknown_usage),
        )
        return _Outcome("failed")
    action_ids = list(agent_state.get("actions", []))
    if action_ids:
        settled: list[tuple[_Outcome, ToolExecutionResult | None] | None] = [
            None
        ] * len(action_ids)
        async with anyio.create_task_group() as group:

            async def run_action(index: int, action_id: str) -> None:
                settled[index] = await _advance_action(
                    engine,
                    coordinator,
                    workflow,
                    action_id,
                    token,
                    limiter,
                    local_limiters,
                )

            for index, action_id in enumerate(action_ids):
                group.start_soon(run_action, index, action_id)
        outcomes = [item for item in settled if item is not None]
        if any(item[0].kind == "reconciling" for item in outcomes):
            return _Outcome("reconciling")
        if any(item[0].kind == "cancelled" for item in outcomes):
            return _Outcome("cancelled")
        if any(item[0].kind == "failed" for item in outcomes):
            return _Outcome("failed")
        if any(item[0].kind == "waiting" for item in outcomes):
            return _Outcome("waiting")
        latest = await coordinator.state()
        for action_id in action_ids:
            action = latest["actions"][action_id]
            request = ToolExecutionRequest.from_mapping(action["request"])
            result = ToolExecutionResult.from_mapping(action["result"])
            identity = (
                request.tool.tool_id,
                request.tool.version,
                request.tool.package_digest,
            )
            contract = by_identity.get(identity)
            if contract is None:
                await coordinator.commit(
                    "AGENT_TOOL_SCOPE_DENIED",
                    {"step_id": key},
                    _failure_mutation("AGENT_TOOL_SCOPE_DENIED", key),
                )
                return _Outcome("failed")
            if result.succeeded:
                validator = validators[
                    f"agent-tool:{workflow.workflow_id}:{workflow.version}:{step.step_id}:{contract.name}:output"
                ]
                issue = _validation_issue(validator, result.output)
                if issue is not None:
                    await coordinator.commit(
                        "AGENT_TOOL_OUTPUT_INVALID",
                        {"step_id": key, "path": issue[0], "keyword": issue[1]},
                        _validation_failure_mutation(
                            "AGENT_TOOL_OUTPUT_INVALID", key, issue
                        ),
                    )
                    return _Outcome("failed")
                if (
                    budget is not None
                    and len(canonical_json(result.output).encode("utf-8"))
                    > budget.max_tool_output_bytes
                ):
                    await coordinator.commit(
                        "AGENT_TOOL_OUTPUT_TOO_LARGE",
                        {"step_id": key, "action_id": action_id},
                        _failure_mutation("AGENT_TOOL_OUTPUT_TOO_LARGE", key),
                    )
                    return _Outcome("failed")

        def observe(edit: StateEdit) -> None:
            # The action record is the one durable copy of a potentially large
            # tool result. Agent history stores only stable references and is
            # materialized into the public neutral shape for each model call.
            history = edit.read(("agents", key, "history"))
            assert isinstance(history, list)
            edit.set(
                ("agents", key, "history"),
                [
                    *history,
                    {"action_ids": list(action_ids)},
                ],
            )
            edit.set(("agents", key, "actions"), [])

        await coordinator.commit(
            "AGENT_TOOL_RESULTS_OBSERVED",
            {"step_id": key, "action_ids": action_ids},
            ExplicitStateMutation(observe),
        )
        state = await coordinator.state()
        agent_state = state["agents"][key]
    turns = agent_state.get("turns")
    if not isinstance(turns, int) or turns >= int(step.config.get("max_turns", 1)):
        await coordinator.commit(
            "AGENT_TURN_LIMIT",
            {"step_id": key},
            _failure_mutation("AGENT_TURN_LIMIT", key),
        )
        return _Outcome("failed")
    usage_state = agent_state.get("usage", {})
    if not isinstance(usage_state, Mapping):
        raise WorkflowRunError("Agent usage state is malformed")
    invocations = agent_state.get("model_invocations", {})
    if not isinstance(invocations, Mapping):
        raise WorkflowRunError("Agent model invocation state is malformed")
    reserved_invocations = [
        (invocation_id, invocation)
        for invocation_id, invocation in invocations.items()
        if isinstance(invocation_id, str)
        and isinstance(invocation, Mapping)
        and invocation.get("status") == "RESERVED"
    ]
    if len(reserved_invocations) > 1:
        raise WorkflowRunError("Agent has multiple reserved model invocations")
    reserved_invocation = reserved_invocations[0] if reserved_invocations else None
    model_requests = int(usage_state.get("model_requests", 0))
    tool_call_count = int(usage_state.get("tool_calls", 0))
    requests_unknown = bool(usage_state.get("model_requests_unknown"))
    if budget is not None and (
        (
            (
                model_requests >= budget.max_model_requests
                and reserved_invocation is None
            )
            or requests_unknown
        )
        or (
            budget.max_total_tokens is not None
            and (
                bool(usage_state.get("tokens_unknown"))
                or int(usage_state.get("total_tokens", 0)) >= budget.max_total_tokens
            )
        )
    ):
        code = (
            "AGENT_USAGE_UNKNOWN"
            if requests_unknown or bool(usage_state.get("tokens_unknown"))
            else "AGENT_BUDGET_EXCEEDED"
        )
        await coordinator.commit(code, {"step_id": key}, _failure_mutation(code, key))
        return _Outcome("failed")
    driver = engine.workflow_services.drivers[str(step.config["driver"])]
    state = await coordinator.state()
    visible_values = (
        {}
        if workflow.schema_version == V4_SCHEMA_VERSION
        else {
            step_id: state["v3"]["step_outputs"][path]
            for step_id, path in visible.items()
            if path in state["v3"]["step_outputs"]
        }
    )
    context = AgentContext(
        run_id=coordinator.snapshot.run_id,
        workflow_id=workflow.workflow_id,
        workflow_version=workflow.version,
        step_id=key,
        turn=turns + 1,
        inputs=coordinator.snapshot.inputs,
        step_input=node_input,
        values=visible_values,
        prior_tool_results=_materialize_agent_history(state, agent_state, by_identity),
        allowed_tools=tuple(item.tool for item in tool_specs),
        workflow_schema_version=workflow.schema_version,
        tool_specs=tool_specs,
        output_schema=(
            step.contract.output_schema
            if workflow.schema_version == V4_SCHEMA_VERSION
            else None
        ),
        budget=budget,
        model_requests_used=model_requests,
        total_tokens_used=int(usage_state.get("total_tokens", 0)),
        driver_continuation=_agent_driver_continuation(agent_state),
        deferred_tool_results=_materialize_agent_deferred_results(
            state, agent_state, by_identity
        ),
        tool_calls_remaining=(
            max(0, budget.max_tool_calls - tool_call_count)
            if budget is not None
            else None
        ),
        definition_workflow_id=workflow.workflow_id,
        definition_workflow_version=workflow.version,
        definition_step_id=step.step_id,
    )
    effective_binding = getattr(driver, "effective_execution_binding", None)
    driver_binding = (
        effective_binding(context)
        if callable(effective_binding)
        else engine.workflow_services.driver_execution_binding(
            str(step.config["driver"])
        )
    )
    if not isinstance(driver_binding, Mapping):
        raise WorkflowRunError("Agent driver execution binding is malformed")
    model_binding_context = {
        name: driver_binding[name]
        for name in ("provider", "model", "instructions")
        if name in driver_binding
    }
    context_bytes = len(
        canonical_json(
            {
                "input": context.step_input,
                "values": context.values,
                "history": context.prior_tool_results,
                "tools": [
                    {
                        "name": item.name,
                        "description": item.model_description,
                        "input_schema": item.input_schema,
                        "output_schema": item.output_schema,
                    }
                    for item in (
                        context.tool_specs
                        if context.tool_calls_remaining is None
                        or context.tool_calls_remaining > 0
                        else ()
                    )
                ],
                "output_schema": context.output_schema,
                "model_binding": model_binding_context,
                "driver_continuation": context.driver_continuation,
                "deferred_tool_results": context.deferred_tool_results,
            }
        ).encode("utf-8")
    )
    if budget is not None and context_bytes > budget.max_context_bytes:
        await coordinator.commit(
            "AGENT_CONTEXT_LIMIT",
            {"step_id": key, "context_bytes": context_bytes},
            _failure_mutation("AGENT_CONTEXT_LIMIT", key),
        )
        return _Outcome("failed")

    invocation_id: str | None = None
    request_number = model_requests + 1
    if reserved_invocation is not None:
        invocation_id, reserved = reserved_invocation
        reserved_request = reserved.get("request")
        reserved_context = reserved.get("context_bytes")
        if (
            not isinstance(reserved_request, int)
            or reserved_request < 1
            or reserved_context != context_bytes
        ):
            await coordinator.commit(
                "AGENT_REQUEST_STATE_INVALID",
                {"step_id": key},
                _failure_mutation("AGENT_REQUEST_STATE_INVALID", key),
            )
            return _Outcome("failed")
        request_number = reserved_request
    else:
        invocation_id = _model_invocation_id(
            coordinator.snapshot.execution_id, key, request_number
        )

        def reserve_request(edit: StateEdit) -> None:
            current_usage = edit.read(("agents", key, "usage"))
            assert isinstance(current_usage, dict)
            edit.set(
                ("agents", key, "usage", "context_bytes"),
                (int(current_usage.get("context_bytes", 0)) + context_bytes),
            )
            edit.set(
                ("agents", key, "model_invocations", invocation_id),
                {
                    "request": request_number,
                    "request_limit": (
                        budget.max_model_requests - model_requests
                        if budget is not None
                        else None
                    ),
                    "turn": turns + 1,
                    "context_bytes": context_bytes,
                    "status": "RESERVED",
                },
            )

        await coordinator.commit(
            "AGENT_REQUEST_RESERVED",
            {
                "step_id": key,
                "invocation_id": invocation_id,
                "request": request_number,
                "context_bytes": context_bytes,
            },
            ExplicitStateMutation(reserve_request),
        )

    def mark_invocation_unknown(
        state: dict[str, Any], *, model_request_count: int | None = None
    ) -> None:
        if invocation_id is None:
            return
        current = state["agents"][key]
        invocation = current["model_invocations"].get(invocation_id)
        usage = current["usage"]
        if isinstance(invocation, dict):
            if invocation.get("status") in {"RESERVED", "DISPATCHED"}:
                usage["driver_invocations"] = (
                    int(usage.get("driver_invocations", 0)) + 1
                )
            if model_request_count is None:
                invocation["status"] = "USAGE_UNKNOWN"
            else:
                invocation["status"] = "REQUEST_LIMIT_REACHED"
                invocation["request_count"] = model_request_count
        if model_request_count is None:
            usage["model_requests_unknown"] = True
        else:
            usage["model_requests"] = (
                int(usage.get("model_requests", 0)) + model_request_count
            )
        usage["transport_attempts_unknown"] = True
        usage["tokens_unknown"] = True

    try:
        async with AsyncExitStack() as stack:
            await stack.enter_async_context(limiter)
            for local in local_limiters:
                await stack.enter_async_context(local)
            latest = await coordinator.state()
            run_deadline = latest["v3"].get("deadline")
            timeout_state = latest["v3"]["node_timeouts"].get(key)
            node_deadline = (
                timeout_state.get("deadline")
                if isinstance(timeout_state, Mapping)
                else None
            )
            expired = any(
                isinstance(deadline, (int, float)) and float(deadline) <= time.time()
                for deadline in (run_deadline, node_deadline)
            )
            if token.cancelled or latest["v3"]["cancel_requested"]:
                token.request()
                if invocation_id is not None:

                    def abandon(state: dict[str, Any]) -> None:
                        _mark_model_request_not_dispatched(state, key, invocation_id)

                    await coordinator.commit(
                        "AGENT_REQUEST_NOT_DISPATCHED",
                        {"step_id": key, "invocation_id": invocation_id},
                        abandon,
                    )
                return _Outcome("cancelled")
            if expired:
                now = time.time()
                run_expired = (
                    isinstance(run_deadline, (int, float))
                    and float(run_deadline) <= now
                )
                error_code = "RUN_TIMEOUT" if run_expired else "STEP_TIMEOUT"

                def request_expired(state: dict[str, Any]) -> None:
                    _mark_model_request_not_dispatched(state, key, invocation_id)
                    _failure_mutation(error_code, None if run_expired else key)(state)

                await coordinator.commit(
                    error_code,
                    {
                        "step_id": key,
                        "invocation_id": invocation_id,
                        "error_code": error_code,
                    },
                    request_expired,
                )
                return _Outcome("failed")
            if invocation_id is not None:

                def dispatched(edit: StateEdit) -> None:
                    if edit.read(("v3", "cancel_requested")):
                        raise _TransitionAbortedError
                    invocation = edit.read(
                        ("agents", key, "model_invocations", invocation_id),
                        default=None,
                    )
                    if (
                        not isinstance(invocation, dict)
                        or invocation.get("status") != "RESERVED"
                    ):
                        raise _TransitionAbortedError
                    edit.set(
                        ("agents", key, "model_invocations", invocation_id, "status"),
                        "DISPATCHED",
                    )

                try:
                    await coordinator.commit(
                        "AGENT_REQUEST_DISPATCHED",
                        {"step_id": key, "invocation_id": invocation_id},
                        ExplicitStateMutation(dispatched),
                    )
                except _TransitionAbortedError:
                    latest = await coordinator.state()
                    if latest["v3"]["cancel_requested"]:
                        token.request()
                        return _Outcome("cancelled")
                    raise WorkflowRunError(
                        "Agent request changed before dispatch"
                    ) from None
            decision = await _call_agent_driver(driver, context, token)
    except _ModelRequestCancelledError:

        def cancelled_request(state: dict[str, Any]) -> None:
            mark_invocation_unknown(state)

        with anyio.CancelScope(shield=True):
            await coordinator.commit(
                "AGENT_REQUEST_CANCELLED",
                {
                    "step_id": key,
                    "invocation_id": invocation_id,
                    "usage": "unknown",
                },
                cancelled_request,
            )
        return _Outcome("cancelled")
    except AgentDriverFailure as error:
        failure_code = error.code
        known_request_count = error.model_request_count

        def driver_failure(state: dict[str, Any], code: str = failure_code) -> None:
            mark_invocation_unknown(state, model_request_count=known_request_count)
            _failure_mutation(code, key)(state)

        await coordinator.commit(failure_code, {"step_id": key}, driver_failure)
        return _Outcome("failed")
    except Exception as error:  # noqa: BLE001

        def driver_error(state: dict[str, Any]) -> None:
            mark_invocation_unknown(state)
            _failure_mutation("AGENT_DRIVER_ERROR", key)(state)

        await coordinator.commit(
            "AGENT_DRIVER_ERROR",
            {"step_id": key, "error_code": _exception_code("AGENT_DRIVER", error)},
            driver_error,
        )
        return _Outcome("failed")
    if not isinstance(decision, AgentDecision):

        def invalid_decision(state: dict[str, Any]) -> None:
            mark_invocation_unknown(state)
            _failure_mutation("AGENT_DECISION_INVALID", key)(state)

        await coordinator.commit(
            "AGENT_DECISION_INVALID",
            {"step_id": key},
            invalid_decision,
        )
        return _Outcome("failed")
    observed_total_tokens = None
    observed_model_requests = None
    observed_transport_attempts = None
    if decision.usage is not None:
        candidate_tokens = decision.usage.get("total_tokens")
        if (
            isinstance(candidate_tokens, int)
            and not isinstance(candidate_tokens, bool)
            and candidate_tokens >= 0
        ):
            observed_total_tokens = candidate_tokens
        candidate_requests = decision.usage.get("request_count")
        if (
            isinstance(candidate_requests, int)
            and not isinstance(candidate_requests, bool)
            and candidate_requests >= 1
        ):
            observed_model_requests = candidate_requests
        candidate_transport_attempts = decision.usage.get("transport_attempts")
        if (
            isinstance(candidate_transport_attempts, int)
            and not isinstance(candidate_transport_attempts, bool)
            and candidate_transport_attempts >= 0
        ):
            observed_transport_attempts = candidate_transport_attempts
    # A custom driver with no SDK telemetry is still one decision invocation.
    # Count that conservatively for forward progress, while making the missing
    # provider request count explicit in the durable audit record.
    accounted_model_requests = max(1, observed_model_requests or 0)

    def observe_usage(current: dict[str, Any]) -> None:
        durable = current["usage"]
        invocation = current["model_invocations"].get(invocation_id)
        durable["driver_invocations"] = int(durable.get("driver_invocations", 0)) + 1
        durable["model_requests"] = (
            int(durable.get("model_requests", 0)) + accounted_model_requests
        )
        if observed_model_requests is None:
            durable["model_requests_unknown"] = True
        if observed_transport_attempts is None:
            durable["transport_attempts_unknown"] = True
        else:
            durable["transport_attempts"] = (
                int(durable.get("transport_attempts", 0)) + observed_transport_attempts
            )
        if observed_total_tokens is None:
            durable["tokens_unknown"] = True
            if isinstance(invocation, dict):
                invocation["status"] = "USAGE_ESTIMATED"
                invocation["request_count"] = accounted_model_requests
        else:
            durable["total_tokens"] = (
                int(durable.get("total_tokens", 0)) + observed_total_tokens
            )
            if isinstance(invocation, dict):
                invocation["status"] = "USAGE_CONFIRMED"
                invocation["total_tokens"] = observed_total_tokens
                invocation["request_count"] = accounted_model_requests

    def observe_usage_edit(edit: StateEdit) -> None:
        usage = edit.read(("agents", key, "usage"))
        assert isinstance(usage, dict)
        invocation_path = ("agents", key, "model_invocations", invocation_id)
        invocation = (
            edit.read(invocation_path, default=None)
            if invocation_id is not None
            else None
        )
        current = {
            "usage": usage,
            "model_invocations": (
                {invocation_id: invocation} if invocation_id is not None else {}
            ),
        }
        observe_usage(current)
        edit.set(("agents", key, "usage"), current["usage"])
        if isinstance(invocation, dict):
            edit.set(invocation_path, current["model_invocations"][invocation_id])

    resulting_model_requests = int(usage_state.get("model_requests", 0)) + (
        accounted_model_requests
    )
    resulting_tokens = int(usage_state.get("total_tokens", 0)) + int(
        observed_total_tokens or 0
    )
    if budget is not None and resulting_model_requests > budget.max_model_requests:

        def request_overrun(state: dict[str, Any]) -> None:
            observe_usage(state["agents"][key])
            _failure_mutation("AGENT_BUDGET_EXCEEDED", key)(state)

        await coordinator.commit(
            "AGENT_BUDGET_EXCEEDED",
            {
                "step_id": key,
                "model_requests": resulting_model_requests,
            },
            request_overrun,
        )
        return _Outcome("failed")
    if (
        budget is not None
        and budget.max_total_tokens is not None
        and observed_total_tokens is not None
        and resulting_tokens > budget.max_total_tokens
    ):

        def token_overrun(state: dict[str, Any]) -> None:
            observe_usage(state["agents"][key])
            _failure_mutation("AGENT_BUDGET_EXCEEDED", key)(state)

        await coordinator.commit(
            "AGENT_BUDGET_EXCEEDED",
            {"step_id": key},
            token_overrun,
        )
        return _Outcome("failed")
    if (
        budget is not None
        and budget.max_total_tokens is not None
        and observed_total_tokens is None
    ):

        def usage_unknown(state: dict[str, Any]) -> None:
            observe_usage(state["agents"][key])
            _failure_mutation("AGENT_USAGE_UNKNOWN", key)(state)

        await coordinator.commit(
            "AGENT_USAGE_UNKNOWN",
            {"step_id": key},
            usage_unknown,
        )
        return _Outcome("failed")
    if not decision.tool_calls:
        if workflow.schema_version == V4_SCHEMA_VERSION:
            if decision.output is None:

                def missing_output(state: dict[str, Any]) -> None:
                    observe_usage(state["agents"][key])
                    _failure_mutation("AGENT_MODEL_OUTPUT_INVALID", key)(state)

                await coordinator.commit(
                    "AGENT_MODEL_OUTPUT_INVALID",
                    {"step_id": key},
                    missing_output,
                )
                return _Outcome("failed")
            output = dict(decision.output)
        else:
            if decision.output is not None:

                def unexpected_output(state: dict[str, Any]) -> None:
                    observe_usage(state["agents"][key])
                    _failure_mutation("AGENT_MODEL_OUTPUT_INVALID", key)(state)

                await coordinator.commit(
                    "AGENT_MODEL_OUTPUT_INVALID",
                    {"step_id": key},
                    unexpected_output,
                )
                return _Outcome("failed")
            output = {"message": decision.message}
        if (
            step.contract.max_output_bytes is not None
            and _json_size_bytes(output) > step.contract.max_output_bytes
        ):

            def oversized_output(state: dict[str, Any]) -> None:
                observe_usage(state["agents"][key])
                _validation_failure_mutation(
                    "STEP_OUTPUT_TOO_LARGE", key, ("", "maxBytes")
                )(state)

            await coordinator.commit(
                "STEP_OUTPUT_TOO_LARGE",
                {"step_id": key, "path": "", "keyword": "maxBytes"},
                oversized_output,
            )
            return _Outcome("failed")
        output_validator = validators[
            f"step:{workflow.workflow_id}:{workflow.version}:{step.step_id}:output"
        ]
        issue = _validation_issue(output_validator, output)
        if issue is not None:

            def invalid_output(state: dict[str, Any]) -> None:
                observe_usage(state["agents"][key])
                _validation_failure_mutation("STEP_OUTPUT_INVALID", key, issue)(state)

            await coordinator.commit(
                "STEP_OUTPUT_INVALID",
                {"step_id": key, "path": issue[0], "keyword": issue[1]},
                invalid_output,
            )
            return _Outcome("failed")

        def reply(edit: StateEdit) -> None:
            edit.set(("agents", key, "turns"), turns + 1)
            edit.set(("agents", key, "final_output"), output)
            edit.set(("agents", key, "message"), decision.message)
            # Native message JSON is a resume aid, not permanent SDK state.
            # Durable action records retain the model call IDs needed for
            # audit after the final output has closed the Agent step.
            edit.set(("agents", key, "driver_continuation"), None)
            observe_usage_edit(edit)
            edit.add_sorted_member(("completed",), key)
            edit.set(("v3", "step_outputs", key), output)
            edit.set(("v3", "step_states", key), "COMPLETED")
            edit.remove(("v3", "node_timeouts", key), missing_ok=True)
            edit.remove(("v3", "step_started_at", key), missing_ok=True)

        accepted_state = await coordinator.state()
        accepted_started = accepted_state["v3"]["step_started_at"].get(key)
        try:
            await coordinator.commit(
                "AGENT_REPLY_ACCEPTED",
                {
                    "step_id": key,
                    "turn": turns + 1,
                    "duration_seconds": (
                        max(0.0, time.time() - float(accepted_started))
                        if isinstance(accepted_started, (int, float))
                        else None
                    ),
                },
                ExplicitStateMutation(reply),
                enforce_retained_budget=True,
            )
        except _RetainedStateTooLargeError:

            def retained_too_large(state: dict[str, Any]) -> None:
                observe_usage(state["agents"][key])
                _validation_failure_mutation(
                    "RUN_RETAINED_STATE_TOO_LARGE", key, ("", "maxRunBytes")
                )(state)

            await coordinator.commit(
                "RUN_RETAINED_STATE_TOO_LARGE",
                {"step_id": key, "path": "", "keyword": "maxRunBytes"},
                retained_too_large,
            )
            return _Outcome("failed")
        return _Outcome("done", output)
    if (
        budget is not None
        and tool_call_count + len(decision.tool_calls) > budget.max_tool_calls
    ):

        def tool_budget(state: dict[str, Any]) -> None:
            observe_usage(state["agents"][key])
            _failure_mutation("AGENT_TOOL_CALL_LIMIT", key)(state)

        await coordinator.commit("AGENT_TOOL_CALL_LIMIT", {"step_id": key}, tool_budget)
        return _Outcome("failed")
    created: list[tuple[str, ToolExecutionRequest, str]] = []
    for index, call in enumerate(decision.tool_calls):
        identity = (call.tool.tool_id, call.tool.version, call.tool.package_digest)
        contract = by_identity.get(identity)
        if contract is None:

            def scope_denied(state: dict[str, Any]) -> None:
                observe_usage(state["agents"][key])
                _failure_mutation("AGENT_TOOL_SCOPE_DENIED", key)(state)

            await coordinator.commit(
                "AGENT_TOOL_SCOPE_DENIED",
                {"step_id": key, "tool_id": call.tool.tool_id},
                scope_denied,
            )
            return _Outcome("failed")
        validator = validators[
            f"agent-tool:{workflow.workflow_id}:{workflow.version}:{step.step_id}:{contract.name}:input"
        ]
        issue = _validation_issue(validator, call.arguments)
        if issue is not None:

            def invalid_arguments(
                state: dict[str, Any], validation_issue: tuple[str, str] = issue
            ) -> None:
                observe_usage(state["agents"][key])
                _validation_failure_mutation(
                    "AGENT_MODEL_OUTPUT_INVALID", key, validation_issue
                )(state)

            await coordinator.commit(
                "AGENT_MODEL_OUTPUT_INVALID",
                {"step_id": key, "path": issue[0], "keyword": issue[1]},
                invalid_arguments,
            )
            return _Outcome("failed")
        action_id = _action_id(
            coordinator.snapshot.execution_id, key, (turns + 1) * 100 + index
        )
        approval_id = _approval_id(
            coordinator.snapshot.execution_id, "action", action_id
        )
        created.append(
            (
                action_id,
                ToolExecutionRequest(action_id, call.tool, dict(call.arguments)),
                approval_id,
            )
        )

    def propose(edit: StateEdit) -> None:
        edit.set(("agents", key, "turns"), turns + 1)
        edit.set(("agents", key, "actions"), [item[0] for item in created])
        edit.set(
            ("agents", key, "driver_continuation"),
            (
                json_copy(decision.continuation)
                if decision.continuation is not None
                else None
            ),
        )
        if budget is not None:
            usage = edit.read(("agents", key, "usage"))
            assert isinstance(usage, dict)
            edit.set(
                ("agents", key, "usage", "tool_calls"),
                int(usage.get("tool_calls", 0)) + len(created),
            )
        observe_usage_edit(edit)
        for index, (action_id, request, approval_id) in enumerate(created):
            edit.set(
                ("actions", action_id),
                {
                    "request": request.to_mapping(),
                    "status": "PENDING_APPROVAL",
                    "step_key": key,
                    "source": "agent",
                    "model_call_id": decision.tool_calls[index].call_id,
                    "output_schema": dict(
                        by_identity[
                            (
                                request.tool.tool_id,
                                request.tool.version,
                                request.tool.package_digest,
                            )
                        ].output_schema
                    ),
                    "output_error_code": "AGENT_TOOL_OUTPUT_INVALID",
                    "max_output_bytes": (
                        budget.max_tool_output_bytes
                        if budget is not None
                        else step.contract.max_output_bytes
                    ),
                    "capabilities": (
                        by_identity[
                            (
                                request.tool.tool_id,
                                request.tool.version,
                                request.tool.package_digest,
                            )
                        ].capabilities.to_mapping()
                        if by_identity[
                            (
                                request.tool.tool_id,
                                request.tool.version,
                                request.tool.package_digest,
                            )
                        ].capabilities
                        is not None
                        else {}
                    ),
                },
            )
            edit.set(
                ("v3", "pending_approvals", approval_id),
                {
                    "kind": "ACTION",
                    "approval_id": approval_id,
                    "action_id": action_id,
                    "step_key": key,
                },
            )

    try:
        await coordinator.commit(
            "AGENT_ACTIONS_PROPOSED",
            {
                "step_id": key,
                "turn": turns + 1,
                "action_ids": [item[0] for item in created],
                "approval_ids": [item[2] for item in created],
            },
            ExplicitStateMutation(propose),
            enforce_retained_budget=True,
        )
    except _RetainedStateTooLargeError:

        def retained_too_large(state: dict[str, Any]) -> None:
            observe_usage(state["agents"][key])
            _validation_failure_mutation(
                "RUN_RETAINED_STATE_TOO_LARGE", key, ("", "maxRunBytes")
            )(state)

        await coordinator.commit(
            "RUN_RETAINED_STATE_TOO_LARGE",
            {"step_id": key, "path": "", "keyword": "maxRunBytes"},
            retained_too_large,
        )
        return _Outcome("failed")
    return _Outcome("waiting")


def _materialize_agent_history(
    state: Mapping[str, Any],
    agent_state: Mapping[str, Any],
    by_identity: Mapping[tuple[str, str, str], AgentToolSpec],
) -> tuple[Mapping[str, JsonValue], ...]:
    raw_history = agent_state.get("history", [])
    if not isinstance(raw_history, list):
        raise WorkflowRunError("Agent history is malformed")
    materialized: list[Mapping[str, JsonValue]] = []
    actions = state.get("actions")
    if not isinstance(actions, Mapping):
        raise WorkflowRunError("Agent action history is malformed")
    for batch in raw_history:
        if not isinstance(batch, Mapping):
            raise WorkflowRunError("Agent history entry is malformed")
        legacy_results = batch.get("tool_results")
        if isinstance(legacy_results, list):
            materialized.append(json_copy(batch))
            continue
        action_ids = batch.get("action_ids")
        if not isinstance(action_ids, list) or any(
            not isinstance(action_id, str) for action_id in action_ids
        ):
            raise WorkflowRunError("Agent history references are malformed")
        results: list[dict[str, JsonValue]] = []
        for action_id in action_ids:
            action = actions.get(action_id)
            if (
                not isinstance(action, Mapping)
                or action.get("status") != "RESOLVED"
                or not isinstance(action.get("request"), Mapping)
                or not isinstance(action.get("result"), Mapping)
            ):
                raise WorkflowRunError("Agent history action is not resolved")
            request = ToolExecutionRequest.from_mapping(action["request"])
            result = ToolExecutionResult.from_mapping(action["result"])
            identity = (
                request.tool.tool_id,
                request.tool.version,
                request.tool.package_digest,
            )
            contract = by_identity.get(identity)
            if contract is None:
                raise WorkflowRunError("Agent history tool is outside frozen scope")
            results.append(
                {
                    "name": contract.name,
                    "arguments": dict(request.arguments),
                    "succeeded": result.succeeded,
                    "output": (
                        dict(result.output) if result.output is not None else None
                    ),
                    "error": result.error,
                }
            )
        materialized.append({"tool_results": results})
    return tuple(materialized)


def _agent_driver_continuation(
    agent_state: Mapping[str, Any],
) -> Mapping[str, JsonValue] | None:
    raw = agent_state.get("driver_continuation")
    if raw is None:
        return None
    if not isinstance(raw, Mapping):
        raise WorkflowRunError("Agent driver continuation is malformed")
    copied = json_copy(raw)
    if not isinstance(copied, dict):
        raise WorkflowRunError("Agent driver continuation is malformed")
    return copied


def _materialize_agent_deferred_results(
    state: Mapping[str, Any],
    agent_state: Mapping[str, Any],
    by_identity: Mapping[tuple[str, str, str], AgentToolSpec],
) -> tuple[Mapping[str, JsonValue], ...]:
    """Bind the latest model call IDs to resolved kernel actions."""

    if agent_state.get("driver_continuation") is None:
        return ()
    raw_history = agent_state.get("history", [])
    if not isinstance(raw_history, list) or not raw_history:
        raise WorkflowRunError("Agent continuation has no resolved tool history")
    latest = raw_history[-1]
    if not isinstance(latest, Mapping):
        raise WorkflowRunError("Agent history entry is malformed")
    action_ids = latest.get("action_ids")
    if not isinstance(action_ids, list) or any(
        not isinstance(action_id, str) for action_id in action_ids
    ):
        raise WorkflowRunError("Agent continuation action references are malformed")
    actions = state.get("actions")
    if not isinstance(actions, Mapping):
        raise WorkflowRunError("Agent action history is malformed")
    records: list[Mapping[str, JsonValue]] = []
    call_ids: set[str] = set()
    for action_id in action_ids:
        action = actions.get(action_id)
        if (
            not isinstance(action, Mapping)
            or action.get("status") != "RESOLVED"
            or not isinstance(action.get("request"), Mapping)
            or not isinstance(action.get("result"), Mapping)
        ):
            raise WorkflowRunError("Agent continuation action is not resolved")
        call_id = action.get("model_call_id")
        if not isinstance(call_id, str) or not call_id or call_id in call_ids:
            raise WorkflowRunError("Agent continuation call identity is malformed")
        request = ToolExecutionRequest.from_mapping(action["request"])
        result = ToolExecutionResult.from_mapping(action["result"])
        identity = (
            request.tool.tool_id,
            request.tool.version,
            request.tool.package_digest,
        )
        contract = by_identity.get(identity)
        if contract is None:
            raise WorkflowRunError("Agent continuation tool is outside frozen scope")
        records.append(
            {
                "action_id": action_id,
                "tool_call_id": call_id,
                "name": contract.name,
                "arguments": dict(request.arguments),
                "succeeded": result.succeeded,
                "output": dict(result.output) if result.output is not None else None,
                "error": result.error,
            }
        )
        call_ids.add(call_id)
    return tuple(records)


async def _resolve_bindings(
    coordinator: _Coordinator,
    bindings: Mapping[str, ValueBinding],
    visible: Mapping[str, str],
    workflow_input: Mapping[str, JsonValue],
    parent_input: Mapping[str, JsonValue],
) -> dict[str, JsonValue]:
    state = await coordinator.state()
    result: dict[str, Any] = {}
    for target, binding in bindings.items():
        if binding.source == "literal":
            value: object = binding.value
        elif binding.source == "workflow_input":
            value = _pointer(workflow_input, binding.pointer)
        elif binding.source == "parent_input":
            value = _pointer(parent_input, binding.pointer)
        else:
            assert binding.step_id is not None
            path = visible.get(binding.step_id)
            if path is None:
                raise WorkflowRunError("step output binding is not visible")
            source = state["v3"]["step_outputs"].get(path, _MISSING)
            value = _pointer(source, binding.pointer)
        if value is _MISSING:
            raise WorkflowRunError("binding source value is unavailable")
        _set_pointer(result, target, value)
    return json_copy(result)


async def _finalize(coordinator: _Coordinator, outcome: _Outcome) -> RunSnapshot:
    state = await coordinator.state()
    unknown = any(
        isinstance(action, Mapping) and action.get("status") in _UNKNOWN_ACTION_STATES
        for action in state["actions"].values()
    )
    if unknown or outcome.kind == "reconciling":
        status, event = "RECONCILING", "RUN_RECONCILING"
    elif state["v3"]["cancel_requested"] or outcome.kind == "cancelled":
        status, event = "CANCELLED", "RUN_CANCELLED"
    elif state["failure"] is not None or outcome.kind == "failed":
        status, event = "BLOCKED", "RUN_BLOCKED"
    elif state["v3"]["pending_approvals"] or outcome.kind == "waiting":
        status, event = "WAITING_APPROVAL", "RUN_WAITING_APPROVAL"
    elif outcome.kind == "done":
        status, event = "SUCCEEDED", "RUN_SUCCEEDED"
    else:
        status, event = "BLOCKED", "RUN_BLOCKED"

    def release(edit: StateEdit) -> None:
        if status == "WAITING_APPROVAL" and not edit.read(("v3", "pending_approvals")):
            raise _WaitingDecisionConsumedError
        if edit.read(("v3", "execution", "owner_id")) != coordinator.owner_id:
            raise RunConflictError("v3 execution generation is no longer current")
        edit.set(("v3", "execution", "owner_id"), None)
        edit.set(("v3", "execution", "lease_expires"), None)

    if coordinator.snapshot.status == status:
        return await coordinator.commit(
            "EXECUTOR_RELEASED",
            {"generation": int(coordinator.generation or 0)},
            ExplicitStateMutation(release),
            status=status,
        )
    return await coordinator.commit(
        event, {}, ExplicitStateMutation(release), status=status
    )
