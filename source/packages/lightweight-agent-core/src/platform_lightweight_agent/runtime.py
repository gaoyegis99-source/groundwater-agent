"""Credential-free project-to-runtime assembly.

This module is the single bridge from an authoring project and a target binding
to the public workflow engine.  Optional model SDKs remain lazily imported by
their existing adapters, and resume can rebuild adapters from the frozen run
manifest instead of re-reading mutable instruction files.
"""

from __future__ import annotations

import hashlib
import importlib
import importlib.metadata
import inspect
import json
import sys
from collections.abc import Callable, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from threading import RLock
from typing import Any

from .drivers import OpenAIAgentsDriver, PydanticAIDriver
from .engine import (
    FunctionToolExecutor,
    WorkflowEngine,
)
from .host import (
    CredentialResolver,
    RuntimeAssembly,
    ToolRoute,
    assemble_runtime,
)
from .journal import RunJournal
from .runtime_contracts import AgentContext, ToolContextFactory
from .runtime_ports import ActionPolicy, ToolExecutor, ToolRegistrationExecutor
from .portability import (
    DriverBinding,
    DriverRuntimeDependency,
    PortableProject,
    PortableTarget,
    ToolDelivery,
    _driver_binding_for,
    _workflow_agent_addresses,
    agent_address_key,
    compile_project_context,
)
from .portable import _normalize_tools
from .spec import (
    AgentDecision,
    JsonValue,
    StepKind,
    ToolReference,
    WorkflowSpec,
    WorkflowValidationError,
    canonical_json,
    digest,
)

_REQUIRED_DRIVER_CAPABILITIES = frozenset(
    {"host_managed_execution", "native_tools", "structured_output"}
)
_PROJECT_IMPORT_LOCK = RLock()


@dataclass(frozen=True)
class ProjectRuntime:
    """A checked project plus the engine assembled for its target."""

    project: PortableProject
    target: PortableTarget
    workflow: WorkflowSpec
    subworkflows: tuple[WorkflowSpec, ...]
    engine: WorkflowEngine
    assembly: RuntimeAssembly


@dataclass(frozen=True)
class DriverFactory:
    """Host-authorized construction contract for one custom Agent driver.

    Discovery metadata is deliberately not consulted here.  The embedding host
    must put a factory in the mapping passed to ``load_*_runtime``; the exact
    version, capabilities and provider imports then become part of the frozen
    target binding and normal resume comparison.
    """

    name: str
    driver_version: str
    capabilities: frozenset[str]
    runtime_dependencies: tuple[DriverRuntimeDependency, ...]
    create: Callable[[DriverBinding, str], Any]

    def __post_init__(self) -> None:
        if not self.name or not self.driver_version or not callable(self.create):
            raise WorkflowValidationError("driver factory declaration is invalid")
        if any(
            not isinstance(item, DriverRuntimeDependency)
            for item in self.runtime_dependencies
        ):
            raise WorkflowValidationError("driver factory dependencies are invalid")


class _ExactToolRegistrar:
    """Narrow registration façade used while loading one release artifact.

    A PyPA entry point is merely a way to locate code.  It must not be able to
    widen the local executor catalogue while it is being loaded, so the host
    exposes only registration of the exact reference already authorized in the
    target configuration.
    """

    def __init__(
        self, executor: ToolRegistrationExecutor, reference: ToolReference
    ) -> None:
        self._executor = executor
        self._reference = reference
        self.bound = False

    def register(self, reference: ToolReference, handler: Callable[..., object]) -> None:
        if reference != self._reference:
            raise WorkflowValidationError(
                "release tool entry point attempted to register another reference"
            )
        self._executor.register(reference, handler)  # type: ignore[arg-type]
        self.bound = True


def bind_release_tool_delivery(
    executor: ToolRegistrationExecutor,
    delivery: ToolDelivery,
    *,
    artifact_manifest_path: str | Path | None = None,
) -> ToolReference:
    """Bind one explicitly authorized release wheel to a trusted registry.

    This is intentionally not plug-in discovery.  The caller supplies one
    :class:`ToolDelivery` from its trusted target configuration; before any
    entry-point code is imported, the host verifies the wheel bytes and the
    generated artifact manifest against its frozen ``ToolReference``.  The
    entry point then receives a narrowly scoped registrar which can bind only
    that exact reference.

    ``artifact_manifest_path`` exists for a host that stores sidecars away
    from the wheel.  Otherwise the host first looks for a tool-specific
    sibling manifest derived from the exact immutable reference.  This lets
    one distribution expose several independently authorized entry points.
    The legacy sibling ``tool-manifest.json`` remains supported for the
    original one-tool wheel template.
    """

    if not (
        callable(getattr(executor, "register", None))
        and callable(getattr(executor, "is_registered", None))
    ):
        raise WorkflowValidationError(
            "release delivery binding requires a trusted tool registry"
        )
    if not isinstance(delivery, ToolDelivery) or delivery.mode != "release":
        raise WorkflowValidationError("release delivery binding requires a release tool")
    assert delivery.distribution is not None
    assert delivery.entry_point is not None
    assert delivery.wheel_path is not None

    wheel = Path(delivery.wheel_path)
    if wheel.is_symlink() or not wheel.is_file():
        raise WorkflowValidationError("release tool wheel is unavailable")
    wheel_digest = "sha256:" + hashlib.sha256(wheel.read_bytes()).hexdigest()
    if wheel_digest != delivery.tool.package_digest:
        raise WorkflowValidationError(
            "release tool wheel does not match its immutable reference"
        )
    if artifact_manifest_path is not None:
        manifest_path = Path(artifact_manifest_path)
    else:
        specific_manifest = wheel.with_name(
            _release_tool_manifest_name(delivery.tool)
        )
        manifest_path = (
            specific_manifest
            if specific_manifest.exists() or specific_manifest.is_symlink()
            else wheel.with_name("tool-manifest.json")
        )
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise WorkflowValidationError("release tool artifact manifest is unavailable")
    try:
        raw_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise WorkflowValidationError("release tool artifact manifest is invalid") from error
    if not isinstance(raw_manifest, Mapping):
        raise WorkflowValidationError("release tool artifact manifest is invalid")
    normalized = _normalize_tools((raw_manifest,))[0]
    manifest_reference = ToolReference.from_mapping(
        {
            key: normalized[key]
            for key in ("tool_id", "version", "package_digest")
        }
    )
    if manifest_reference != delivery.tool:
        raise WorkflowValidationError(
            "release tool artifact manifest does not match its target binding"
        )

    try:
        distribution = importlib.metadata.distribution(delivery.distribution)
    except importlib.metadata.PackageNotFoundError as error:
        raise WorkflowValidationError("release tool distribution is not installed") from error
    if distribution.version != delivery.tool.version:
        raise WorkflowValidationError(
            "installed release tool version does not match its immutable reference"
        )
    group, entry_name = delivery.entry_point.split(":", 1)
    entries = tuple(
        item
        for item in distribution.entry_points
        if item.group == group and item.name == entry_name
    )
    if len(entries) != 1:
        raise WorkflowValidationError(
            "installed release tool has no unique declared entry point"
        )
    try:
        registrar = entries[0].load()
    except Exception as error:
        raise WorkflowValidationError("release tool entry point could not be loaded") from error
    if not callable(registrar):
        raise WorkflowValidationError("release tool entry point is not callable")
    _validate_release_tool_descriptor(registrar, delivery.tool)
    limited = _ExactToolRegistrar(executor, delivery.tool)
    try:
        _call_release_registrar(registrar, limited, delivery.tool)
    except WorkflowValidationError:
        raise
    except Exception as error:
        raise WorkflowValidationError("release tool entry point could not register") from error
    if not limited.bound or not executor.is_registered(delivery.tool):
        raise WorkflowValidationError(
            "release tool entry point did not register its verified reference"
        )
    return delivery.tool


def _validate_release_tool_descriptor(
    registrar: Callable[..., object], reference: ToolReference
) -> None:
    """Check the optional logical descriptor emitted by the shipped template."""

    module_name = getattr(registrar, "__module__", None)
    module = sys.modules.get(module_name) if isinstance(module_name, str) else None
    descriptors = getattr(module, "TOOL_DESCRIPTORS", None)
    if descriptors is not None:
        if (
            not isinstance(descriptors, Mapping)
            or descriptors.get(reference.tool_id) != reference.version
            or any(
                not isinstance(tool_id, str) or not isinstance(version, str)
                for tool_id, version in descriptors.items()
            )
        ):
            raise WorkflowValidationError(
                "release tool descriptor does not match its immutable reference"
            )
        return
    descriptor = getattr(module, "TOOL_DESCRIPTOR", None)
    if descriptor is not None and (
        not isinstance(descriptor, Mapping)
        or descriptor.get("tool_id") != reference.tool_id
        or descriptor.get("version") != reference.version
    ):
        raise WorkflowValidationError(
            "release tool descriptor does not match its immutable reference"
        )


def _release_tool_manifest_name(reference: ToolReference) -> str:
    """Return the deterministic sidecar name for one tool in a shared wheel."""

    identity = canonical_json(reference.to_mapping()).encode("utf-8")
    token = hashlib.sha256(identity).hexdigest()[:24]
    return f"tool-manifest-{token}.json"


def _call_release_registrar(
    registrar: Callable[..., object],
    limited: _ExactToolRegistrar,
    reference: ToolReference,
) -> None:
    """Call the two-argument release protocol, accepting safe legacy shape."""

    try:
        signature = inspect.signature(registrar)
        signature.bind(limited, reference)
    except (TypeError, ValueError):
        try:
            signature = inspect.signature(registrar)
            signature.bind(limited)
        except (TypeError, ValueError) as error:
            raise WorkflowValidationError(
                "release tool entry point has an unsupported registration signature"
            ) from error
        registrar(limited)
        return
    registrar(limited, reference)


class _ProjectDriver:
    """Route logical workflow drivers by step without exposing SDK state."""

    def __init__(
        self,
        logical_name: str,
        routes: Mapping[tuple[str, str, str], tuple[DriverBinding, Any]],
    ) -> None:
        self._logical_name = logical_name
        self._routes = dict(routes)
        frozen = [
            {
                "workflow_id": address[0],
                "workflow_version": address[1],
                "step_id": address[2],
                "target": binding.to_mapping(),
                "adapter": adapter.execution_binding(),
            }
            for address, (binding, adapter) in sorted(self._routes.items())
        ]
        self._binding: dict[str, JsonValue] = {
            "provider": "v6-project-router",
            "logical_driver": logical_name,
            "routes": frozen,
            "protocol": "native-tools-v2",
        }
        self.driver_version = (
            "project-router@" + digest(self._binding).split(":", 1)[1][:24]
        )
        self._binding["driver_version"] = self.driver_version

    def execution_binding(self) -> dict[str, JsonValue]:
        return dict(self._binding)

    def _adapter(self, context: AgentContext) -> Any:
        address = (
            context.definition_workflow_id or context.workflow_id,
            context.definition_workflow_version or context.workflow_version,
            context.definition_step_id or context.step_id,
        )
        route = self._routes.get(address)
        if route is None:
            raise WorkflowValidationError(
                "Agent definition node has no frozen target driver binding"
            )
        return route[1]

    def effective_execution_binding(
        self, context: AgentContext
    ) -> Mapping[str, JsonValue]:
        """Expose the selected adapter metadata for exact context budgeting."""

        return self._adapter(context).execution_binding()

    def decide(self, context: AgentContext) -> AgentDecision:
        return self._adapter(context).decide(context)

    async def decide_async(self, context: AgentContext) -> AgentDecision:
        return await self._adapter(context).decide_async(context)


def load_project_runtime(
    source: str | Path | PortableProject,
    target: PortableTarget | Mapping[str, object],
    journal: RunJournal,
    *,
    registries: Sequence[str] = (),
    executor: ToolExecutor | None = None,
    tool_context_factory: ToolContextFactory | None = None,
    policy: ActionPolicy | None = None,
    tool_routes: Sequence[ToolRoute] = (),
    credential_resolver: CredentialResolver | Callable[[str], object] | None = None,
    closeables: Sequence[object] = (),
    driver_factories: Mapping[str, DriverFactory] | None = None,
) -> ProjectRuntime:
    """Build the same checked runtime used by Python, CLI, and local Web."""

    if executor is not None and tool_context_factory is not None:
        raise WorkflowValidationError(
            "project runtime cannot combine executor and tool_context_factory"
        )
    if tool_routes and tool_context_factory is not None:
        raise WorkflowValidationError(
            "project runtime tool routes own their own tool context factory"
        )
    if executor is not None and tool_routes:
        raise WorkflowValidationError(
            "project runtime accepts either a default executor or exact tool routes"
        )
    compilation = compile_project_context(source)
    project = compilation.project
    target_value = (
        target
        if isinstance(target, PortableTarget)
        else PortableTarget.from_mapping(target)
    )
    workflow = compilation.workflow
    children = compilation.subworkflows
    tool_manifests = _project_tool_manifests(project)
    domain_lock = _project_domain_lock(project)
    _validate_target(
        workflow,
        children,
        target_value,
        tool_manifests,
        domain_lock=domain_lock,
    )
    registry_bindings = (*project.tool_registries, *registries)
    drivers = _project_drivers(
        workflow, children, project, target_value, driver_factories or {}
    )
    assembly = RuntimeAssembly(
        journal,
        executor=(
            None
            if tool_routes
            else executor
            or _executor(
                project,
                registry_bindings,
                _release_deliveries_for(workflow, children, target_value),
                context_factory=tool_context_factory,
            )
        ),
        drivers=drivers,
        workflows=children,
        policy=policy,
        runtime_binding=_runtime_binding(
            registry_bindings,
            tool_manifests,
            project.dependency_lock_path,
            domain_lock=domain_lock,
        ),
        tool_routes=tuple(tool_routes),
        credential_resolver=credential_resolver,
        closeables=tuple(closeables),
    )
    return ProjectRuntime(
        project, target_value, workflow, children, assemble_runtime(assembly), assembly
    )


def load_resume_runtime(
    source: str | Path | PortableProject,
    target: PortableTarget | Mapping[str, object],
    journal: RunJournal,
    run_id: str,
    *,
    registries: Sequence[str] = (),
    executor: ToolExecutor | None = None,
    tool_context_factory: ToolContextFactory | None = None,
    policy: ActionPolicy | None = None,
    tool_routes: Sequence[ToolRoute] = (),
    credential_resolver: CredentialResolver | Callable[[str], object] | None = None,
    closeables: Sequence[object] = (),
    driver_factories: Mapping[str, DriverFactory] | None = None,
) -> ProjectRuntime:
    """Rebuild a runtime using prompt/model values frozen in the run manifest."""

    if executor is not None and tool_context_factory is not None:
        raise WorkflowValidationError(
            "resume runtime cannot combine executor and tool_context_factory"
        )
    if tool_routes and tool_context_factory is not None:
        raise WorkflowValidationError(
            "resume runtime tool routes own their own tool context factory"
        )
    if executor is not None and tool_routes:
        raise WorkflowValidationError(
            "resume runtime accepts either a default executor or exact tool routes"
        )
    project = _resume_project(source)
    target_value = (
        target
        if isinstance(target, PortableTarget)
        else PortableTarget.from_mapping(target)
    )
    snapshot = journal.get(run_id)
    workflow = WorkflowSpec.from_mapping(snapshot.workflow)
    frozen_children = _manifest_children(snapshot.execution_manifest)
    frozen_runtime = _manifest_runtime_binding(snapshot.execution_manifest)
    frozen_registries = _manifest_registries(frozen_runtime)
    frozen_manifests = _manifest_tool_manifests(frozen_runtime)
    domain_lock = frozen_runtime.get("domain_lock")
    _validate_target(
        workflow,
        frozen_children,
        target_value,
        frozen_manifests,
        domain_lock=domain_lock if isinstance(domain_lock, Mapping) else None,
    )
    if registries and tuple(registries) != frozen_registries:
        raise WorkflowValidationError(
            "resume tool registries differ from the frozen runtime assembly"
        )
    drivers = _manifest_drivers(
        snapshot.execution_manifest, target_value, driver_factories or {}
    )
    assembly = RuntimeAssembly(
        journal,
        executor=(
            None
            if tool_routes
            else executor
            or _executor(
                project,
                frozen_registries,
                _release_deliveries_for(workflow, frozen_children, target_value),
                context_factory=tool_context_factory,
            )
        ),
        drivers=drivers,
        workflows=frozen_children,
        policy=policy,
        runtime_binding=frozen_runtime,
        tool_routes=tuple(tool_routes),
        credential_resolver=credential_resolver,
        closeables=tuple(closeables),
    )
    return ProjectRuntime(
        workflow=workflow,
        project=project,
        target=target_value,
        subworkflows=frozen_children,
        engine=assemble_runtime(assembly),
        assembly=assembly,
    )


def _resume_project(source: str | Path | PortableProject) -> PortableProject:
    """Resolve only the host code root; run semantics come from the manifest."""

    if isinstance(source, PortableProject):
        root = source.root
    else:
        candidate = Path(source)
        root = candidate.parent if candidate.name == "pyproject.toml" else candidate
    if root.is_symlink() or not root.is_dir():
        raise WorkflowValidationError("resume project root must be a real directory")
    resolved = root.resolve()
    return PortableProject(
        root=resolved,
        kind="workflow",
        workflow_path=None,
        tools_path=None,
        agent_instruction_paths={},
        subworkflow_paths={},
        files=(),
        dependency_lock_path=resolved / "pylock.toml",
    )


def _runtime_binding(
    registries: Sequence[str],
    tool_manifests: Sequence[Mapping[str, JsonValue]],
    dependency_lock: Path,
    *,
    domain_lock: Mapping[str, JsonValue] | None = None,
) -> dict[str, JsonValue]:
    result: dict[str, JsonValue] = {
        "tool_registries": list(registries),
        "tool_manifests": json.loads(json.dumps(tool_manifests)),
        "dependency_lock_sha256": "sha256:"
        + hashlib.sha256(dependency_lock.read_bytes()).hexdigest(),
    }
    if domain_lock is not None:
        # Parsing the lock before it becomes a frozen runtime fact gives a
        # normal project a clear authoring error rather than a later resume
        # incompatibility.  The decoder is stdlib-only.
        from .domain import DomainLock

        result["domain_lock"] = DomainLock.from_mapping(domain_lock).to_mapping()
    return result


def _manifest_runtime_binding(
    manifest: Mapping[str, JsonValue],
) -> Mapping[str, JsonValue]:
    raw = manifest.get("runtime_binding")
    required = {"tool_registries", "tool_manifests"}
    optional = {"dependency_lock_sha256", "domain_lock"}
    if (
        not isinstance(raw, Mapping)
        or not required <= set(raw)
        or set(raw) - required - optional
        or (
            raw.get("dependency_lock_sha256") is not None
            and not isinstance(raw.get("dependency_lock_sha256"), str)
        )
    ):
        raise WorkflowValidationError("run runtime binding is malformed")
    if "domain_lock" in raw:
        from .domain import DomainLock

        DomainLock.from_mapping(raw["domain_lock"])
    return raw


def _project_domain_lock(
    project: PortableProject,
) -> Mapping[str, JsonValue] | None:
    if project.domain_lock_path is None:
        return None
    from .domain import load_domain_lock

    return load_domain_lock(project.domain_lock_path).to_mapping()


def _manifest_registries(binding: Mapping[str, JsonValue]) -> tuple[str, ...]:
    raw = binding.get("tool_registries", [])
    if not isinstance(raw, list) or any(
        not isinstance(item, str) or item.count(":") != 1 for item in raw
    ):
        raise WorkflowValidationError("run tool registry bindings are malformed")
    return tuple(raw)


def _manifest_tool_manifests(
    binding: Mapping[str, JsonValue],
) -> tuple[Mapping[str, JsonValue], ...]:
    raw = binding.get("tool_manifests")
    if not isinstance(raw, list) or any(not isinstance(item, Mapping) for item in raw):
        raise WorkflowValidationError("run tool manifests are malformed")
    return tuple(_normalize_tools(raw))


def _read_workflow(path: Path) -> WorkflowSpec:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise WorkflowValidationError(
            f"workflow definition {path.name} is invalid"
        ) from error
    return WorkflowSpec.from_mapping(raw)


def _project_tool_manifests(
    project: PortableProject,
) -> tuple[Mapping[str, JsonValue], ...]:
    if project.tools_path is None:
        raise WorkflowValidationError("workflow project has no tool catalogue")
    try:
        raw = json.loads(project.tools_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise WorkflowValidationError("project tool catalogue is invalid") from error
    if not isinstance(raw, list) or any(not isinstance(item, Mapping) for item in raw):
        raise WorkflowValidationError("project tools must be a list of objects")
    return tuple(_normalize_tools(raw))


def _walk(workflow: WorkflowSpec):
    pending = list(reversed(workflow.steps))
    while pending:
        step = pending.pop()
        yield step
        for branch in reversed(step.children):
            pending.extend(reversed(branch))


def _all_workflows(
    workflow: WorkflowSpec, children: Sequence[WorkflowSpec]
) -> tuple[WorkflowSpec, ...]:
    return (workflow, *children)


def _validate_target(
    workflow: WorkflowSpec,
    children: Sequence[WorkflowSpec],
    target: PortableTarget,
    tool_manifests: Sequence[Mapping[str, JsonValue]] = (),
    *,
    domain_lock: Mapping[str, JsonValue] | None = None,
) -> None:
    if target.target_kind != "v6":
        raise WorkflowValidationError(
            "only a compatible V6 target can execute a project"
        )
    trusted = {
        (tool.tool_id, tool.version, tool.package_digest)
        for tool in target.trusted_tools
    }
    child_catalog = {(item.workflow_id, item.version): item for item in children}
    all_workflows = _all_workflows(workflow, children)
    agent_addresses = _workflow_agent_addresses(all_workflows)
    for manifest in tool_manifests:
        permissions = manifest.get("permissions", [])
        if (
            not isinstance(permissions, list)
            or not set(permissions) <= target.allowed_permissions
        ):
            raise WorkflowValidationError(
                "target does not grant every required tool permission"
            )
        credential_ref = manifest.get("credential_ref")
        if (
            isinstance(credential_ref, str)
            and credential_ref not in target.credential_bindings
        ):
            raise WorkflowValidationError(
                "target has not bound a required credential reference"
            )
    for current in all_workflows:
        for step in _walk(current):
            if step.kind is StepKind.TOOL:
                raw = step.config["tool"]
                identity = (
                    str(raw["tool_id"]),
                    str(raw["version"]),
                    str(raw["package_digest"]),
                )
                if identity not in trusted:
                    raise WorkflowValidationError(
                        f"target does not trust exact tool used by step {step.step_id}"
                    )
            elif step.kind is StepKind.AGENT:
                address = (current.workflow_id, current.version, step.step_id)
                binding = _driver_binding_for(target, address, agent_addresses)
                if binding is None:
                    raise WorkflowValidationError(
                        "target has no driver binding for Agent definition "
                        + agent_address_key(*address)
                    )
                if not _REQUIRED_DRIVER_CAPABILITIES <= binding.capabilities:
                    raise WorkflowValidationError(
                        "target driver lacks required capabilities for "
                        + agent_address_key(*address)
                    )
                for contract in step.config.get("tools", []):
                    tool = contract.get("tool", contract)
                    identity = (
                        str(tool["tool_id"]),
                        str(tool["version"]),
                        str(tool["package_digest"]),
                    )
                    if identity not in trusted:
                        raise WorkflowValidationError(
                            f"target does not trust Agent tool for step {step.step_id}"
                        )
            elif step.kind is StepKind.SUBWORKFLOW:
                key = (str(step.config["workflow_id"]), str(step.config["version"]))
                child = child_catalog.get(key)
                if child is None:
                    raise WorkflowValidationError(
                        "project subworkflow definition is missing"
                    )
                binding = target.subworkflow_bindings.get(f"{key[0]}@{key[1]}")
                if (
                    not isinstance(binding, Mapping)
                    or binding.get("definition_digest") != child.definition_digest
                ):
                    raise WorkflowValidationError(
                        f"target has no exact subworkflow binding for {key[0]}@{key[1]}"
                    )
    if domain_lock is not None:
        # The core keeps this as a generic, credential-free runtime binding.
        # Domain semantics remain in the optional authoring module, which
        # checks the declared request against the workflow scope and the fresh
        # host target on every assembly/resume.
        from .domain import DomainLock, validate_domain_target

        validate_domain_target(
            DomainLock.from_mapping(domain_lock),
            target,
            workflow,
            children,
            tool_manifests,
        )


def _project_drivers(
    workflow: WorkflowSpec,
    children: Sequence[WorkflowSpec],
    project: PortableProject,
    target: PortableTarget,
    driver_factories: Mapping[str, DriverFactory],
) -> dict[str, _ProjectDriver]:
    all_workflows = _all_workflows(workflow, children)
    agent_addresses = _workflow_agent_addresses(all_workflows)
    grouped: dict[str, dict[tuple[str, str, str], tuple[DriverBinding, Any]]] = {}
    for current in all_workflows:
        for step in _walk(current):
            if step.kind is not StepKind.AGENT:
                continue
            address = (current.workflow_id, current.version, step.step_id)
            binding = _driver_binding_for(target, address, agent_addresses)
            if binding is None:
                raise WorkflowValidationError(
                    "target has no driver binding for Agent definition "
                    + agent_address_key(*address)
                )
            instruction_path = project.agent_instruction_paths.get(
                agent_address_key(*address)
            )
            if instruction_path is None:
                raise WorkflowValidationError(
                    "project has no instructions for Agent definition "
                    + agent_address_key(*address)
                )
            instructions = instruction_path.read_text(encoding="utf-8")
            adapter = _build_adapter(binding, instructions, driver_factories)
            logical = str(step.config["driver"])
            grouped.setdefault(logical, {})[address] = (binding, adapter)
    return {
        logical: _ProjectDriver(logical, routes) for logical, routes in grouped.items()
    }


def _adapter(binding: DriverBinding, instructions: str) -> Any:
    if binding.driver == "pydantic-ai":
        adapter = PydanticAIDriver(
            model=binding.model, instructions=instructions, model_name=binding.model
        )
    elif binding.driver == "openai-agents":
        adapter = OpenAIAgentsDriver(
            model=binding.model, instructions=instructions, model_name=binding.model
        )
    else:
        raise WorkflowValidationError(
            f"unsupported project driver provider {binding.driver!r}"
        )
    if adapter.driver_version != binding.driver_version:
        raise WorkflowValidationError(
            f"installed driver for {binding.step_id} does not match the target version"
        )
    return adapter


def _build_adapter(
    binding: DriverBinding,
    instructions: str,
    driver_factories: Mapping[str, DriverFactory],
) -> Any:
    if binding.driver in {"pydantic-ai", "openai-agents"}:
        return _adapter(binding, instructions)
    factory = driver_factories.get(binding.driver)
    if factory is None:
        raise WorkflowValidationError(
            f"custom project driver {binding.driver!r} is not authorized by the host"
        )
    if factory.name != binding.driver or factory.driver_version != binding.driver_version:
        raise WorkflowValidationError(
            f"driver factory for {binding.step_id} does not match the target version"
        )
    if not binding.capabilities <= factory.capabilities:
        raise WorkflowValidationError(
            f"driver factory for {binding.step_id} lacks target capabilities"
        )
    if tuple(binding.runtime_dependencies) != tuple(factory.runtime_dependencies):
        raise WorkflowValidationError(
            f"driver factory for {binding.step_id} has different runtime dependencies"
        )
    try:
        adapter = factory.create(binding, instructions)
    except WorkflowValidationError:
        raise
    except Exception as error:
        raise WorkflowValidationError(
            f"driver factory for {binding.step_id} could not construct its adapter"
        ) from error
    if getattr(adapter, "driver_version", None) != binding.driver_version:
        raise WorkflowValidationError(
            f"constructed driver for {binding.step_id} does not match the target version"
        )
    for method in ("execution_binding", "decide", "decide_async"):
        if not callable(getattr(adapter, method, None)):
            raise WorkflowValidationError(
                f"constructed driver for {binding.step_id} is missing {method}"
            )
    return adapter


def _manifest_children(
    manifest: Mapping[str, JsonValue],
) -> tuple[WorkflowSpec, ...]:
    raw = manifest.get("subworkflows", [])
    if not isinstance(raw, list):
        raise WorkflowValidationError("run execution manifest has invalid subworkflows")
    return tuple(
        WorkflowSpec.from_mapping(item["workflow"])
        for item in raw
        if isinstance(item, Mapping) and isinstance(item.get("workflow"), Mapping)
    )


def _manifest_drivers(
    manifest: Mapping[str, JsonValue],
    target: PortableTarget,
    driver_factories: Mapping[str, DriverFactory],
) -> dict[str, _ProjectDriver]:
    expected = manifest.get("drivers")
    if not isinstance(expected, Mapping):
        raise WorkflowValidationError("run execution manifest has no driver bindings")
    route_addresses: dict[str, tuple[str, str, str]] = {}
    for logical, raw in expected.items():
        if not isinstance(logical, str) or not isinstance(raw, Mapping):
            continue
        routes = raw.get("routes")
        if not isinstance(routes, list):
            continue
        for route in routes:
            if not isinstance(route, Mapping):
                continue
            workflow_id = route.get("workflow_id")
            workflow_version = route.get("workflow_version")
            step_id = route.get("step_id")
            if all(
                isinstance(item, str) and item
                for item in (workflow_id, workflow_version, step_id)
            ):
                address = (workflow_id, workflow_version, step_id)
                route_addresses[agent_address_key(*address)] = address
    result: dict[str, _ProjectDriver] = {}
    for logical, raw in expected.items():
        if not isinstance(logical, str) or not isinstance(raw, Mapping):
            raise WorkflowValidationError("run driver binding is malformed")
        if raw.get("provider") != "v6-project-router":
            raise WorkflowValidationError(
                "resume requires the original project runtime driver protocol"
            )
        routes = raw.get("routes")
        if not isinstance(routes, list):
            raise WorkflowValidationError("run project driver routes are malformed")
        restored: dict[tuple[str, str, str], tuple[DriverBinding, Any]] = {}
        for route in routes:
            if not isinstance(route, Mapping):
                raise WorkflowValidationError("run project driver route is malformed")
            workflow_id = route.get("workflow_id")
            workflow_version = route.get("workflow_version")
            step_id = route.get("step_id")
            if not all(
                isinstance(item, str) and item
                for item in (workflow_id, workflow_version, step_id)
            ):
                raise WorkflowValidationError("run project driver address is malformed")
            frozen_target = DriverBinding.from_mapping(route.get("target"))
            if frozen_target.step_id != step_id:
                raise WorkflowValidationError(
                    "run project driver route does not match its frozen target"
                )
            current = _driver_binding_for(
                target,
                (workflow_id, workflow_version, step_id),
                route_addresses,
            )
            if current != frozen_target:
                raise WorkflowValidationError(
                    f"target binding changed for Agent step {frozen_target.step_id}"
                )
            adapter_binding = route.get("adapter")
            if not isinstance(adapter_binding, Mapping):
                raise WorkflowValidationError("run Agent adapter binding is malformed")
            instructions = adapter_binding.get("instructions")
            if not isinstance(instructions, str):
                raise WorkflowValidationError("run Agent instructions are not frozen")
            adapter = _build_adapter(
                frozen_target, instructions, driver_factories
            )
            if adapter.execution_binding() != adapter_binding:
                raise WorkflowValidationError(
                    f"runtime binding changed for Agent step {frozen_target.step_id}"
                )
            restored[(workflow_id, workflow_version, step_id)] = (
                frozen_target,
                adapter,
            )
        router = _ProjectDriver(logical, restored)
        if router.execution_binding() != raw:
            raise WorkflowValidationError("run project driver manifest was altered")
        result[logical] = router
    return result


def _executor(
    project: PortableProject,
    registries: Sequence[str],
    release_deliveries: Sequence[ToolDelivery] = (),
    *,
    context_factory: ToolContextFactory | None = None,
) -> FunctionToolExecutor:
    """Build the standard registry used by project, wheel, CLI, and resume.

    ``FunctionToolExecutor`` delegates lifecycle safety to the two existing
    local executors while accepting both ``def`` and ``async def`` handlers
    through exactly the same trusted registration and release-delivery path.
    """

    executor = FunctionToolExecutor(context_factory=context_factory)
    roots = {_registry_module(target).split(".", 1)[0] for target in registries}
    # sys.path and sys.modules are process-global.  Serialize the short startup
    # import window and temporarily isolate registry package names so two
    # generated projects using the conventional ``runtime_tools`` module do not
    # silently reuse one another's code in the same embedding process.
    with (
        _PROJECT_IMPORT_LOCK,
        _project_import_path(project.root),
        _isolated_modules(roots),
    ):
        for target in registries:
            _registry(target)(executor)
    for delivery in release_deliveries:
        bind_release_tool_delivery(executor, delivery)
    return executor


def _release_deliveries_for(
    workflow: WorkflowSpec,
    children: Sequence[WorkflowSpec],
    target: PortableTarget,
) -> tuple[ToolDelivery, ...]:
    """Select only declared release tools; a target may contain other tools."""

    required: set[tuple[str, str, str]] = set()
    for current in _all_workflows(workflow, children):
        for step in _walk(current):
            if step.kind is StepKind.TOOL:
                required.add(_reference_identity(step.config["tool"]))
            elif step.kind is StepKind.AGENT:
                for contract in step.config.get("tools", []):
                    if isinstance(contract, Mapping):
                        required.add(_reference_identity(contract.get("tool", contract)))
    return tuple(
        delivery
        for delivery in target.tool_deliveries
        if delivery.mode == "release" and delivery.identity in required
    )


def _reference_identity(value: object) -> tuple[str, str, str]:
    if not isinstance(value, Mapping):
        raise WorkflowValidationError("workflow tool reference is malformed")
    reference = ToolReference.from_mapping(value)
    return reference.tool_id, reference.version, reference.package_digest


def _registry_module(target: str) -> str:
    module_name, separator, attribute = target.partition(":")
    if not separator or not module_name or not attribute or module_name.startswith("."):
        raise WorkflowValidationError("tool registry must use absolute MODULE:CALLABLE")
    return module_name


def _registry(target: str) -> Callable[[ToolRegistrationExecutor], None]:
    module_name, _separator, attribute = target.partition(":")
    _registry_module(target)
    try:
        candidate = getattr(importlib.import_module(module_name), attribute)
    except (ImportError, AttributeError) as error:
        raise WorkflowValidationError(
            f"could not load trusted tool registry {target}"
        ) from error
    if not callable(candidate):
        raise WorkflowValidationError("trusted tool registry is not callable")
    return candidate


@contextmanager
def _isolated_modules(roots: set[str]):  # type: ignore[no-untyped-def]
    def selected(name: str) -> bool:
        return any(name == root or name.startswith(f"{root}.") for root in roots)

    saved = {
        name: module for name, module in list(sys.modules.items()) if selected(name)
    }
    for name in saved:
        sys.modules.pop(name, None)
    importlib.invalidate_caches()
    try:
        yield
    finally:
        for name in tuple(sys.modules):
            if selected(name):
                sys.modules.pop(name, None)
        sys.modules.update(saved)


@contextmanager
def _project_import_path(root: Path):  # type: ignore[no-untyped-def]
    value = str(root)
    sys.path.insert(0, value)
    try:
        yield
    finally:
        if sys.path and sys.path[0] == value:
            sys.path.pop(0)
        else:
            try:
                sys.path.remove(value)
            except ValueError:
                pass


def runtime_fingerprint(runtime: ProjectRuntime) -> str:
    """Return a useful JSON-only identity for embedding diagnostics."""

    return digest(
        {
            "workflow": runtime.workflow.definition_digest,
            "target": runtime.target.to_mapping(),
            "subworkflows": [item.definition_digest for item in runtime.subworkflows],
            "project": str(runtime.project.root),
        }
    )


def describe_runtime(runtime: ProjectRuntime) -> dict[str, JsonValue]:
    return {
        "workflow_id": runtime.workflow.workflow_id,
        "version": runtime.workflow.version,
        "definition_digest": runtime.workflow.definition_digest,
        "runtime_fingerprint": runtime_fingerprint(runtime),
        "target": runtime.target.target_kind,
        "subworkflows": [
            {
                "workflow_id": item.workflow_id,
                "version": item.version,
                "definition_digest": item.definition_digest,
            }
            for item in runtime.subworkflows
        ],
        "canonical_target": canonical_json(runtime.target.to_mapping()),
    }
