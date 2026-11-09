"""Command-line entry point for the standalone V6 kernel.

The CLI has no network, Web, container, Data, or Knowledge service startup
path.  A developer may explicitly opt into a local trusted-tool registry and
scripted Agent decisions for a fully local workflow run.
"""

from __future__ import annotations

import argparse
import importlib
import json
import os
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .drivers import SCRIPTED_DECISIONS_SCHEMA_V1, ScriptedAgentDriver
from .engine import (
    CancellationToken,
    FunctionToolExecutor,
    ToolContext,
    ToolExecutionRequest,
    ToolExecutionResult,
    WorkflowEngine,
    WorkflowRunError,
)
from .files import FileWorkspace
from .host import HostRuntime, HostRuntimeLimits, RuntimeAssembly, assemble_runtime
from .journal import (
    EventCursor,
    EventCursorError,
    RunConflictError,
    RunExecutionMismatchError,
    RunJournal,
    RunJournalError,
    RunListCursor,
    RunNotFoundError,
    RunQueryFilter,
    RunRetentionPolicy,
    RunSnapshot,
    SQLiteRunJournal,
)
from .portable import BUNDLE_V1, BundleTarget, export_bundle, import_bundle
from .spec import (
    ASYNC_SCHEMA_VERSIONS,
    RECOMMENDED_NEW_WORKFLOW_SCHEMA_VERSION,
    AgentDecision,
    ApprovalDecision,
    ToolReference,
    WorkflowSpec,
    WorkflowValidationError,
    workflow_semantics,
)

_MAX_CLI_INPUT_BYTES = 1 * 1024 * 1024


CLI_DIAGNOSTIC_SCHEMA_V1 = "v6-cli-diagnostic-v1"
_STRICT_STATUS_EXIT_CODES = {
    "SUCCEEDED": 0,
    "BLOCKED": 10,
    "WAITING_APPROVAL": 11,
    "RECONCILING": 12,
    "CANCEL_REQUESTED": 13,
    "CANCELLED": 14,
    "RUNNING": 15,
    "RECOVERY_PENDING": 16,
}


@dataclass(frozen=True)
class _CliDiagnosticError(Exception):
    code: str
    message: str
    file: str | None = None
    line: int | None = None
    column: int | None = None
    path: str | None = None
    keyword: str | None = None
    hint: str | None = None

    def __str__(self) -> str:
        return self.message


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="platform-agent-workflow")
    subcommands = parser.add_subparsers(dest="command", required=True)

    init = subcommands.add_parser(
        "init", help="create a safe built-in project template"
    )
    init.add_argument("kind", choices=("workflow", "tool"))
    init.add_argument("destination", type=Path)
    init.add_argument("--name", default="agent-workflow")

    domain = subcommands.add_parser(
        "domain",
        help="check or compile a declarative v6-domain-pack-v1 without executing it",
    )
    domain_commands = domain.add_subparsers(dest="domain_command", required=True)
    domain_check = domain_commands.add_parser(
        "check", help="validate a domain declaration, pinned resources, and gates"
    )
    domain_check.add_argument("source", type=Path)
    domain_compile = domain_commands.add_parser(
        "compile", help="emit ordinary V6 projects from a checked domain pack"
    )
    domain_compile.add_argument("source", type=Path)
    domain_compile.add_argument("output", type=Path)
    domain_compile.add_argument(
        "--tool-registry", action="append", default=[], metavar="MODULE:CALLABLE"
    )
    domain_compile.add_argument(
        "--parameter-profile",
        metavar="NAME",
        help="freeze one declared profile into reserved workflow literal bindings",
    )

    check = subcommands.add_parser("check", help="validate a workflow or project")
    check.add_argument("workflow", type=Path)

    plan = subcommands.add_parser("plan", help="preview a workflow without execution")
    plan.add_argument("source", type=Path)

    lock = subcommands.add_parser("lock", help="create or verify a PEP 751 lock")
    lock.add_argument("project", type=Path)
    lock.add_argument("--check", action="store_true")

    build_tool = subcommands.add_parser(
        "build-tool", help="build a tool wheel and emit its exact manifest"
    )
    build_tool.add_argument("project", type=Path)

    for name, help_text in (
        ("run", "start a local workflow"),
        ("resume", "resume a local workflow"),
    ):
        command = subcommands.add_parser(name, help=help_text)
        command.add_argument(
            "--journal", type=Path, default=Path(".v6-agent-runs.sqlite")
        )
        command.add_argument(
            "--tool-registry", action="append", default=[], metavar="MODULE:CALLABLE"
        )
        command.add_argument("--scripted-decisions", type=Path)
        command.add_argument("--driver-name", default="scripted")
        command.add_argument(
            "--strict-exit",
            action="store_true",
            help="return a distinct nonzero code unless the workflow succeeded",
        )
        command.add_argument(
            "--file-workspace", type=Path, help="explicit local file workspace root"
        )
        command.add_argument(
            "--driver-factory", action="append", default=[], metavar="MODULE:OBJECT"
        )
        if name == "run":
            command.add_argument("workflow", type=Path)
            command.add_argument("--target-config", type=Path)
            command.add_argument("--input", default="{}", help="inline JSON object")
            command.add_argument("--input-file", type=Path)
            command.add_argument("--run-id")
        else:
            command.add_argument("run_id")
            command.add_argument("--expected-execution-id")
            command.add_argument("--project", type=Path)
            command.add_argument("--target-config", type=Path)
            command.add_argument("--approval-id")
            decision = command.add_mutually_exclusive_group()
            decision.add_argument("--approve", action="store_true")
            decision.add_argument("--reject", action="store_true")

    cancel = subcommands.add_parser("cancel", help="record a cancellation request")
    cancel.add_argument("run_id")
    cancel.add_argument("--journal", type=Path, default=Path(".v6-agent-runs.sqlite"))
    cancel.add_argument("--reason", default="cancelled by caller")
    cancel.add_argument("--expected-execution-id")
    cancel.add_argument("--strict-exit", action="store_true")

    reconcile = subcommands.add_parser(
        "reconcile", help="submit a confirmed result for an unknown action"
    )
    reconcile.add_argument("run_id")
    reconcile.add_argument("action_id")
    reconcile.add_argument(
        "--journal", type=Path, default=Path(".v6-agent-runs.sqlite")
    )
    reconcile.add_argument("--project", type=Path)
    reconcile.add_argument("--target-config", type=Path)
    reconcile.add_argument("--file-workspace", type=Path)
    reconcile.add_argument(
        "--driver-factory", action="append", default=[], metavar="MODULE:OBJECT"
    )
    reconcile.add_argument(
        "--tool-registry", action="append", default=[], metavar="MODULE:CALLABLE"
    )
    reconcile.add_argument("--succeeded", action="store_true")
    reconcile.add_argument("--error-code")
    reconcile.add_argument("--output", default="{}", help="confirmed JSON object")
    reconcile.add_argument("--output-file", type=Path)
    reconcile.add_argument("--strict-exit", action="store_true")
    reconcile.add_argument("--expected-execution-id")

    events = subcommands.add_parser("events", help="print persisted run events")
    events.add_argument("run_id")
    events.add_argument("--journal", type=Path, default=Path(".v6-agent-runs.sqlite"))
    events.add_argument("--after", type=int, default=0)
    events.add_argument("--cursor")
    events.add_argument("--expected-execution-id")
    events.add_argument("--limit", type=int, default=256)
    events.add_argument("--follow", action="store_true")

    runs = subcommands.add_parser(
        "runs", help="list compact local run metadata without reading outputs"
    )
    runs.add_argument("--journal", type=Path, default=Path(".v6-agent-runs.sqlite"))
    runs.add_argument("--status", action="append", default=[])
    runs.add_argument("--workflow-id")
    runs.add_argument("--pending-only", action="store_true")
    runs.add_argument("--include-archived", action="store_true")
    runs.add_argument("--cursor")
    runs.add_argument("--limit", type=int, default=50)

    explain = subcommands.add_parser(
        "explain", help="explain a persisted run without executing it"
    )
    explain.add_argument("run_id")
    explain.add_argument("--journal", type=Path, default=Path(".v6-agent-runs.sqlite"))

    preview = subcommands.add_parser(
        "preview-recovery",
        help="preview recovery operations without calling drivers or tools",
    )
    preview.add_argument("run_id")
    preview.add_argument("--journal", type=Path, default=Path(".v6-agent-runs.sqlite"))

    journal_info = subcommands.add_parser(
        "journal-info", help="show the local journal format and completed migrations"
    )
    journal_info.add_argument("journal", type=Path)

    journal_space = subcommands.add_parser(
        "journal-space", help="show SQLite file size and reusable free pages"
    )
    journal_space.add_argument("journal", type=Path)

    journal_vacuum = subcommands.add_parser(
        "journal-vacuum",
        help="explicitly rebuild an idle SQLite journal and reclaim free pages",
    )
    journal_vacuum.add_argument("journal", type=Path)

    backup = subcommands.add_parser(
        "journal-backup", help="create a verified online SQLite journal backup"
    )
    backup.add_argument("journal", type=Path)
    backup.add_argument("destination", type=Path)
    backup.add_argument("--overwrite", action="store_true")

    restore = subcommands.add_parser(
        "journal-restore", help="restore and upgrade a verified journal backup"
    )
    restore.add_argument("backup", type=Path)
    restore.add_argument("destination", type=Path)
    restore.add_argument("--overwrite", action="store_true")

    local_backup = subcommands.add_parser(
        "local-backup", help="back up a journal with its immutable file artifacts"
    )
    local_backup.add_argument("journal", type=Path)
    local_backup.add_argument("file_workspace", type=Path)
    local_backup.add_argument("destination", type=Path)
    local_backup.add_argument("--overwrite", action="store_true")
    local_backup.add_argument("--dependency-lock", type=Path)

    local_backup_plan = subcommands.add_parser(
        "local-backup-plan",
        help="preview full-workspace recovery package size and protected files",
    )
    local_backup_plan.add_argument("journal", type=Path)
    local_backup_plan.add_argument("file_workspace", type=Path)
    local_backup_plan.add_argument("--dependency-lock", type=Path)

    local_restore = subcommands.add_parser(
        "local-restore", help="restore a verified journal and file artifact package"
    )
    local_restore.add_argument("backup", type=Path)
    local_restore.add_argument("destination", type=Path)
    local_restore.add_argument("--overwrite", action="store_true")
    local_restore.add_argument("--dependency-lock", type=Path)

    local_environment = subcommands.add_parser(
        "local-environment-check",
        help="compare a recovery package with the current runtime without restoring",
    )
    local_environment.add_argument("backup", type=Path)
    local_environment.add_argument("--dependency-lock", type=Path)

    local_retention = subcommands.add_parser(
        "local-retention-preview",
        help="preview unreferenced artifacts without deleting them",
    )
    local_retention.add_argument("journal", type=Path)
    local_retention.add_argument("file_workspace", type=Path)
    local_retention.add_argument(
        "--backup", action="append", type=Path, default=[], help="recovery package to protect"
    )

    release_recovery = subcommands.add_parser(
        "journal-release-recovery",
        help="acknowledge a restored run's old owner is stopped before recovery",
    )
    release_recovery.add_argument("run_id")
    release_recovery.add_argument("--journal", type=Path, required=True)
    release_recovery.add_argument("--expected-execution-id")
    release_recovery.add_argument(
        "--source-owner-stopped",
        action="store_true",
        required=True,
        help="required acknowledgement that the original runtime cannot continue",
    )
    release_recovery.add_argument(
        "--strategy",
        choices=("query", "idempotent-redispatch"),
        default="query",
    )

    retain = subcommands.add_parser(
        "journal-retain", help="archive or purge terminal journal runs explicitly"
    )
    retain.add_argument("journal", type=Path)
    retain.add_argument(
        "--archive-after-seconds", type=float, default=30 * 24 * 60 * 60
    )
    retain.add_argument("--purge-after-seconds", type=float)
    retain.add_argument("--max-archived-runs", type=int)

    serve = subcommands.add_parser(
        "serve", help="start the optional localhost run/approval debug UI"
    )
    serve.add_argument("--journal", type=Path, default=Path(".v6-agent-runs.sqlite"))
    serve.add_argument(
        "--tool-registry", action="append", default=[], metavar="MODULE:CALLABLE"
    )
    serve.add_argument("--scripted-decisions", type=Path)
    serve.add_argument("--driver-name", default="scripted")
    serve.add_argument("--project", type=Path)
    serve.add_argument("--target-config", type=Path)
    serve.add_argument("--file-workspace", type=Path)
    serve.add_argument(
        "--driver-factory", action="append", default=[], metavar="MODULE:OBJECT"
    )
    serve.add_argument("--max-active-runs", type=int, default=4)
    serve.add_argument("--max-queued-runs", type=int, default=16)
    serve.add_argument("--max-tool-calls", type=int, default=16)
    serve.add_argument("--max-model-requests", type=int, default=4)
    serve.add_argument("--shutdown-timeout", type=float, default=30.0)
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8765)
    serve.add_argument(
        "--session-token",
        help=(
            "required local-Web control token; defaults to "
            "V6_LOCAL_WEB_SESSION_TOKEN when set"
        ),
    )

    export = subcommands.add_parser("export", help="create a portable workflow bundle")
    export.add_argument("workflow", type=Path)
    export.add_argument("output", type=Path)
    export.add_argument(
        "--format",
        dest="bundle_format",
        choices=("v1", "v2"),
        default="v1",
        help="v2 requires a configured authoring project directory",
    )
    export.add_argument(
        "--tools", type=Path, help="JSON list of exact tool-package manifests"
    )
    export.add_argument(
        "--dependencies", type=Path, help="JSON object of pinned dependencies"
    )
    export.add_argument("--files-root", type=Path)
    export.add_argument(
        "--file", action="append", default=[], help="relative required input file"
    )

    imported = subcommands.add_parser(
        "import", help="validate and unpack a portable bundle"
    )
    imported.add_argument("bundle", type=Path)
    imported.add_argument("target", type=Path)
    imported.add_argument(
        "--target-tools",
        type=Path,
        help="JSON list of trusted exact tool references for target validation",
    )
    imported.add_argument(
        "--allow-permission",
        action="append",
        default=[],
        help="permission granted by the target (repeatable)",
    )
    imported.add_argument(
        "--credential-binding",
        action="append",
        default=[],
        help="credential reference bound in the target (repeatable)",
    )
    imported.add_argument(
        "--target-config",
        type=Path,
        help="v6-portability-target-v1 JSON required for bundle v2",
    )
    imported.add_argument(
        "--as-project",
        action="store_true",
        help="assemble a compatible v2 bundle into a checked runnable project",
    )
    imported.add_argument(
        "--tool-registry",
        action="append",
        default=[],
        metavar="MODULE:CALLABLE",
        help="explicit target-owned registry recorded in the assembled project",
    )
    imported.add_argument(
        "--subworkflow",
        action="append",
        default=[],
        metavar="WORKFLOW@VERSION=PATH",
        help="exact local definition required by an assembled subworkflow",
    )
    imported.add_argument(
        "--skip-dependency-check",
        action="store_true",
        help="skip uv target-interpreter dry-run during import",
    )

    diagnose = subcommands.add_parser(
        "diagnose", help="report bundle compatibility without extraction"
    )
    diagnose.add_argument("bundle", type=Path)
    diagnose.add_argument("--target-config", type=Path, required=True)
    diagnose.add_argument(
        "--skip-dependency-check",
        action="store_true",
        help="skip uv target-interpreter dry-run",
    )
    diagnose.add_argument(
        "--check-environment",
        action="store_true",
        help="read target interpreter metadata and declared delivery artifacts without loading development tool modules",
    )
    diagnose.add_argument(
        "--verify-development-entrypoints",
        action="store_true",
        help="with --check-environment, import host-trusted development modules and verify callable attributes without registering or invoking tools",
    )

    evaluate = subcommands.add_parser(
        "evaluate", help="run an explicitly authorized live-model evaluation"
    )
    evaluate.add_argument("project", type=Path)
    evaluate.add_argument(
        "--provider", choices=("pydantic-ai", "openai-agents"), required=True
    )
    evaluate.add_argument("--model", required=True)
    evaluate.add_argument("--output", type=Path, required=True)
    evaluate.add_argument("--allow-model-requests", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "init":
            from .portability import scaffold_project

            project = scaffold_project(
                args.kind, args.destination, project_name=args.name
            )
            _print(
                {
                    "schema_version": "v6-agent-project-v1",
                    "kind": project.kind,
                    "root": str(project.root),
                }
            )
            return 0
        if args.command == "domain":
            from .domain import check_domain_pack, compile_domain_pack

            try:
                if args.domain_command == "check":
                    pack = check_domain_pack(args.source)
                    _print(
                        {
                            "schema_version": "v6-domain-pack-check-v1",
                            "effects": "not_executed",
                            "domain_id": pack.domain_id,
                            "version": pack.version,
                            "definition_digest": pack.definition_digest,
                            "entries": [
                                {
                                    "name": entry.name,
                                    "workflow_id": entry.workflow_id,
                                    "workflow_version": entry.version,
                                    "definition_digest": entry.definition_digest,
                                }
                                for entry in pack.entry_workflows
                            ],
                        }
                    )
                    return 0
                compilation = compile_domain_pack(
                    args.source,
                    args.output,
                    tool_registries=tuple(args.tool_registry),
                    parameter_profile=args.parameter_profile,
                )
            except WorkflowValidationError as error:
                raise _CliDiagnosticError(
                    "DOMAIN_PACK_INVALID",
                    str(error),
                    file=str(args.source),
                    path="/",
                    keyword="contract",
                    hint="Fix the declaration, pinned source asset, workflow gate, or tool scope.",
                ) from error
            _print(compilation.to_mapping())
            return 0
        if args.command == "check":
            if args.workflow.is_dir() or args.workflow.name == "pyproject.toml":
                from .portability import (
                    check_project,
                    compile_project_context,
                    load_project,
                )

                loaded = None
                try:
                    loaded = load_project(args.workflow)
                    compilation = (
                        compile_project_context(loaded)
                        if loaded.kind == "workflow"
                        else None
                    )
                    project = (
                        compilation.project
                        if compilation is not None
                        else check_project(loaded)
                    )
                except WorkflowValidationError as error:
                    root = (
                        args.workflow.parent
                        if args.workflow.name == "pyproject.toml"
                        else args.workflow
                    )
                    diagnostic_file = (
                        loaded.workflow_path
                        if loaded is not None and loaded.workflow_path is not None
                        else root / "pyproject.toml"
                    )
                    raise _CliDiagnosticError(
                        "PROJECT_CONTRACT_INVALID",
                        str(error),
                        file=str(diagnostic_file),
                        path="/",
                        keyword="contract",
                        hint="Fix the project declaration or referenced workflow file.",
                    ) from error
                result: dict[str, object] = {
                    "schema_version": "v6-agent-project-v1",
                    "kind": project.kind,
                    "root": str(project.root),
                }
                if compilation is not None:
                    spec = compilation.workflow
                    result.update(
                        {
                            "workflow_id": spec.workflow_id,
                            "workflow_version": spec.version,
                            "workflow_schema_version": spec.schema_version,
                            "semantics": workflow_semantics(spec).to_mapping(),
                            "recommended_new_project_schema_version": (
                                RECOMMENDED_NEW_WORKFLOW_SCHEMA_VERSION
                            ),
                        }
                    )
                _print(result)
                return 0
            spec = _load_workflow(args.workflow)
            if spec.schema_version in ASYNC_SCHEMA_VERSIONS:
                from .workflow_v3 import preflight_v3

                try:
                    preflight_v3(spec)
                except WorkflowValidationError as error:
                    raise _CliDiagnosticError(
                        "WORKFLOW_CONTRACT_INVALID",
                        str(error),
                        file=str(args.workflow),
                        path="/",
                        keyword="contract",
                        hint="Fix the workflow field or node named by this contract error.",
                    ) from error
            _print(
                {
                    "workflow_id": spec.workflow_id,
                    "version": spec.version,
                    "workflow_schema_version": spec.schema_version,
                    "definition_digest": spec.definition_digest,
                    "semantics": workflow_semantics(spec).to_mapping(),
                    "recommended_new_project_schema_version": (
                        RECOMMENDED_NEW_WORKFLOW_SCHEMA_VERSION
                    ),
                }
            )
            return 0
        if args.command == "plan":
            from .portability import compile_execution_plan

            source: object
            if args.source.is_dir() or args.source.name == "pyproject.toml":
                source = args.source
            else:
                source = _load_workflow(args.source)
            try:
                compiled_plan = compile_execution_plan(source)  # type: ignore[arg-type]
            except WorkflowValidationError as error:
                raise _CliDiagnosticError(
                    "WORKFLOW_PLAN_INVALID",
                    str(error),
                    file=str(args.source),
                    path="/",
                    keyword="contract",
                    hint="Run check on the same source and fix its reported contract.",
                ) from error
            _print(compiled_plan.to_mapping())
            return 0
        if args.command == "lock":
            from .portability import lock_project

            path = lock_project(args.project, check=args.check)
            _print({"lock": str(path), "checked": args.check})
            return 0
        if args.command == "build-tool":
            from .portability import build_tool_package

            _print(build_tool_package(args.project))
            return 0
        if args.command == "export":
            if args.bundle_format == "v2":
                if args.tools or args.dependencies or args.files_root or args.file:
                    raise WorkflowValidationError(
                        "bundle v2 reads tools, files, and lock from its project"
                    )
                from .portability import export_project_bundle

                bundle = export_project_bundle(args.workflow, args.output)
                _print(
                    {
                        "schema_version": bundle.schema_version,
                        "workflow_id": bundle.workflow.workflow_id,
                        "definition_digest": bundle.workflow.definition_digest,
                    }
                )
                return 0
            workflow = _load_workflow(args.workflow)
            tools = _load_json(args.tools, "tools") if args.tools else []
            dependencies = (
                _load_json(args.dependencies, "dependencies")
                if args.dependencies
                else {}
            )
            bundle = export_bundle(
                args.output,
                workflow,
                tools=tools,
                dependencies=dependencies,
                files_root=args.files_root,
                files=args.file,
                bundle_format=BUNDLE_V1,
            )
            _print(
                {
                    "workflow_id": bundle.workflow.workflow_id,
                    "definition_digest": bundle.workflow.definition_digest,
                }
            )
            return 0
        if args.command == "import":
            if args.target_config is not None:
                if (
                    args.target_tools
                    or args.allow_permission
                    or args.credential_binding
                ):
                    raise WorkflowValidationError(
                        "--target-config cannot be combined with legacy target flags"
                    )
                from .portability import PortableTarget

                target_capabilities: object | None = PortableTarget.from_mapping(
                    _load_json(args.target_config, "target config")
                )
            else:
                target_capabilities = _target_capabilities(
                    args.target_tools,
                    args.allow_permission,
                    args.credential_binding,
                )
            if args.as_project:
                from .portability import PortableTarget, import_bundle_project

                if not isinstance(target_capabilities, PortableTarget):
                    raise WorkflowValidationError(
                        "--as-project requires --target-config"
                    )
                subworkflows = _keyed_paths(
                    args.subworkflow, label="subworkflow binding"
                )
                project = import_bundle_project(
                    args.bundle,
                    args.target,
                    target_capabilities,
                    tool_registries=tuple(args.tool_registry),
                    subworkflow_sources=subworkflows,
                    check_dependencies=not args.skip_dependency_check,
                )
                _print(
                    {
                        "schema_version": "v6-agent-project-v1",
                        "kind": project.kind,
                        "root": str(project.root),
                    }
                )
                return 0
            if args.tool_registry or args.subworkflow:
                raise WorkflowValidationError(
                    "--tool-registry and --subworkflow require --as-project"
                )
            bundle = import_bundle(
                args.bundle,
                args.target,
                target_capabilities=target_capabilities,
                check_dependencies=not args.skip_dependency_check,
            )
            _print(
                {
                    "workflow_id": bundle.workflow.workflow_id,
                    "version": bundle.workflow.version,
                    "files": bundle.files,
                }
            )
            return 0
        if args.command == "diagnose":
            from .portability import PortableTarget, diagnose_bundle

            if (
                args.verify_development_entrypoints
                and not args.check_environment
            ):
                raise WorkflowValidationError(
                    "--verify-development-entrypoints requires --check-environment"
                )
            target = PortableTarget.from_mapping(
                _load_json(args.target_config, "target config")
            )
            report = diagnose_bundle(
                args.bundle,
                target,
                check_dependencies=not args.skip_dependency_check,
                check_environment=args.check_environment,
                verify_development_entrypoints=args.verify_development_entrypoints,
            )
            _print(report.to_mapping())
            return report.exit_code
        if args.command == "evaluate":
            from .evaluation import run_live_evaluation

            report = run_live_evaluation(
                args.project,
                provider=args.provider,
                model=args.model,
                output=args.output,
                allow_model_requests=args.allow_model_requests,
            )
            _print(report)
            return 0 if report["result"] == "PASS" else 1
        if args.command == "explain":
            from .inspection import explain_run

            _print(
                explain_run(
                    SQLiteRunJournal(args.journal).inspection(args.run_id)
                ).to_mapping()
            )
            return 0
        if args.command == "preview-recovery":
            from .inspection import preview_recovery

            _print(
                preview_recovery(
                    SQLiteRunJournal(args.journal).inspection(args.run_id)
                ).to_mapping()
            )
            return 0
        if args.command == "journal-info":
            _print(SQLiteRunJournal(args.journal).storage_info().to_mapping())
            return 0
        if args.command == "journal-space":
            _print(SQLiteRunJournal(args.journal).space_info().to_mapping())
            return 0
        if args.command == "journal-vacuum":
            _print(SQLiteRunJournal(args.journal).vacuum().to_mapping())
            return 0
        if args.command == "journal-backup":
            backup = SQLiteRunJournal(args.journal).backup_to(
                args.destination, overwrite=args.overwrite
            )
            _print({"backup": str(backup)})
            return 0
        if args.command == "journal-restore":
            journal = SQLiteRunJournal.restore_from_backup(
                args.backup, args.destination, overwrite=args.overwrite
            )
            _print(
                {
                    "journal": str(journal.path),
                    **journal.storage_info().to_mapping(),
                    "recovery_hold_run_ids": [
                        status.run_id for status in journal.recovery_holds()
                    ],
                }
            )
            return 0
        if args.command == "local-backup":
            from .recovery import create_local_recovery_package

            package = create_local_recovery_package(
                SQLiteRunJournal(args.journal),
                FileWorkspace.under(args.file_workspace, create=False),
                args.destination,
                overwrite=args.overwrite,
                dependency_lock=args.dependency_lock,
            )
            _print(
                {
                    "schema_version": "v6-local-recovery-v1",
                    "backup": str(package),
                }
            )
            return 0
        if args.command == "local-backup-plan":
            from .recovery import plan_local_recovery_package

            _print(
                plan_local_recovery_package(
                    SQLiteRunJournal(args.journal),
                    FileWorkspace.under(args.file_workspace, create=False),
                    dependency_lock=args.dependency_lock,
                ).to_mapping()
            )
            return 0
        if args.command == "local-restore":
            from .recovery import restore_local_recovery_package

            journal, workspace = restore_local_recovery_package(
                args.backup,
                args.destination,
                overwrite=args.overwrite,
                dependency_lock=args.dependency_lock,
            )
            from .recovery import check_local_recovery_environment

            environment = check_local_recovery_environment(
                args.backup, dependency_lock=args.dependency_lock
            )
            _print(
                {
                    "schema_version": "v6-local-recovery-v1",
                    "journal": str(journal.path),
                    "file_workspace": str(workspace.input_root.parent),
                    "environment": environment.to_mapping(),
                    "recovery_hold_run_ids": [
                        status.run_id for status in journal.recovery_holds()
                    ],
                }
            )
            return 0
        if args.command == "local-environment-check":
            from .recovery import check_local_recovery_environment

            report = check_local_recovery_environment(
                args.backup, dependency_lock=args.dependency_lock
            )
            _print(report.to_mapping())
            return 0 if report.status == "compatible" else 3 if report.status == "warning" else 4
        if args.command == "local-retention-preview":
            from .recovery import preview_local_artifact_retention

            _print(
                preview_local_artifact_retention(
                    SQLiteRunJournal(args.journal),
                    FileWorkspace.under(args.file_workspace, create=False),
                    backups=tuple(args.backup),
                ).to_mapping()
            )
            return 0
        if args.command == "journal-release-recovery":
            journal = SQLiteRunJournal(args.journal)
            snapshot = journal.release_backup_recovery(
                args.run_id,
                source_owner_stopped=args.source_owner_stopped,
                strategy=args.strategy,
                expected_execution_id=args.expected_execution_id,
            )
            _print(
                {
                    "run_id": snapshot.run_id,
                    "status": snapshot.status,
                    "revision": snapshot.revision,
                    "next_step": "resume_or_reconcile",
                }
            )
            return 0
        if args.command == "journal-retain":
            report = SQLiteRunJournal(args.journal).apply_retention(
                RunRetentionPolicy(
                    archive_after_seconds=args.archive_after_seconds,
                    purge_after_seconds=args.purge_after_seconds,
                    max_archived_runs=args.max_archived_runs,
                )
            )
            _print(report.to_mapping())
            return 0
        if args.command == "events":
            journal = SQLiteRunJournal(args.journal)
            cursor = EventCursor.from_token(args.cursor) if args.cursor else None
            if cursor is not None and args.after != 0:
                raise WorkflowValidationError("--cursor cannot be combined with --after")
            after = cursor.sequence if cursor is not None else args.after
            expected_execution_id = (
                cursor.execution_id if cursor is not None else args.expected_execution_id
            )
            if args.follow:
                engine = _engine(journal, [], None, "scripted")
                _follow_events(
                    engine,
                    args.run_id,
                    after,
                    expected_execution_id=expected_execution_id,
                    cursor=cursor,
                )
                return 0
            _print(
                [
                    event.to_mapping()
                    for event in journal.events(
                        args.run_id,
                        after=after,
                        limit=args.limit,
                        expected_execution_id=expected_execution_id,
                    )
                ]
            )
            return 0
        if args.command == "runs":
            cursor = RunListCursor.from_token(args.cursor) if args.cursor else None
            page = SQLiteRunJournal(args.journal).list_runs(
                RunQueryFilter(
                    statuses=tuple(args.status),
                    workflow_id=args.workflow_id,
                    pending_only=args.pending_only,
                    include_archived=args.include_archived,
                ),
                cursor,
                args.limit,
            )
            _print(page.to_mapping())
            return 0
        if args.command == "cancel":
            engine = _engine(
                SQLiteRunJournal(args.journal),
                args.tool_registry if hasattr(args, "tool_registry") else [],
                None,
                "scripted",
            )
            current = engine.get(
                args.run_id, expected_execution_id=args.expected_execution_id
            )
            current_workflow = WorkflowSpec.from_mapping(current.workflow)
            if current_workflow.schema_version in ASYNC_SCHEMA_VERSIONS:
                cancelled = engine.request_cancel(
                    args.run_id,
                    reason=args.reason,
                    expected_execution_id=args.expected_execution_id,
                )
            else:
                cancelled = engine.cancel(
                    args.run_id,
                    reason=args.reason,
                    expected_execution_id=args.expected_execution_id,
                )
            _print(_snapshot_cli_mapping(cancelled))
            return _strict_snapshot_exit(cancelled, args.strict_exit)
        if args.command == "reconcile":
            journal = SQLiteRunJournal(args.journal)
            engine = _execution_engine(
                journal,
                project=args.project,
                target_config=args.target_config,
                run_id=args.run_id,
                registries=args.tool_registry,
                file_workspace=args.file_workspace,
                driver_factories=args.driver_factory,
            )
            if args.succeeded == (args.error_code is not None):
                raise WorkflowValidationError(
                    "reconcile requires exactly one of --succeeded or --error-code"
                )
            output = (
                _load_inputs(args.output, args.output_file) if args.succeeded else None
            )
            snapshot = engine.reconcile(
                args.run_id,
                args.action_id,
                ToolExecutionResult(
                    args.action_id,
                    args.succeeded,
                    output=output,
                    error=args.error_code,
                ),
                expected_execution_id=args.expected_execution_id,
            )
            _print(_snapshot_cli_mapping(snapshot))
            return _strict_snapshot_exit(snapshot, args.strict_exit)
        if args.command == "serve":
            from .web import serve_web_app

            journal = SQLiteRunJournal(args.journal)
            configured_workflow = None
            if args.project is not None:
                runtime = _project_runtime(
                    journal,
                    args.project,
                    args.target_config,
                    args.tool_registry,
                    file_workspace=args.file_workspace,
                    driver_factories=args.driver_factory,
                )
                assembly = runtime.assembly
                configured_workflow = runtime.workflow
            else:
                assembly = _assembly(
                    journal,
                    args.tool_registry,
                    args.scripted_decisions,
                    args.driver_name,
                    file_workspace=args.file_workspace,
                )
            host_runtime = HostRuntime(
                assembly,
                limits=HostRuntimeLimits(
                    max_active_runs=args.max_active_runs,
                    max_queued_runs=args.max_queued_runs,
                    max_tool_calls=args.max_tool_calls,
                    max_model_requests=args.max_model_requests,
                    shutdown_timeout_seconds=args.shutdown_timeout,
                ),
            )
            try:
                serve_web_app(
                    host_runtime,
                    workflow=configured_workflow,
                    host=args.host,
                    port=args.port,
                    session_token=args.session_token
                    or os.environ.get("V6_LOCAL_WEB_SESSION_TOKEN"),
                )
            finally:
                host_runtime.close(
                    mode="request_cancel", timeout_seconds=args.shutdown_timeout
                )
            return 0
        journal = SQLiteRunJournal(args.journal)
        if args.command == "run":
            inputs = _load_inputs(args.input, args.input_file)
            if args.workflow.is_dir() or args.workflow.name == "pyproject.toml":
                if args.scripted_decisions is not None:
                    raise WorkflowValidationError(
                        "project runs use target driver bindings; run workflow.json "
                        "for the explicit offline scripted path"
                    )
                runtime = _project_runtime(
                    journal,
                    args.workflow,
                    args.target_config,
                    args.tool_registry,
                    file_workspace=args.file_workspace,
                    driver_factories=args.driver_factory,
                )
                snapshot = runtime.engine.run(
                    runtime.workflow, inputs, run_id=args.run_id
                )
            else:
                engine = _engine(
                    journal,
                    args.tool_registry,
                    args.scripted_decisions,
                    args.driver_name,
                    file_workspace=args.file_workspace,
                )
                snapshot = engine.run(
                    _load_workflow(args.workflow), inputs, run_id=args.run_id
                )
        else:
            engine = _execution_engine(
                journal,
                project=args.project,
                target_config=args.target_config,
                run_id=args.run_id,
                registries=args.tool_registry,
                decisions_path=args.scripted_decisions,
                driver_name=args.driver_name,
                file_workspace=args.file_workspace,
                driver_factories=args.driver_factory,
            )
            approved = True if args.approve else False if args.reject else None
            if args.approval_id is not None:
                if approved is None:
                    raise WorkflowValidationError(
                        "--approval-id requires --approve or --reject"
                    )
                snapshot = engine.resume(
                    args.run_id,
                    approvals=(ApprovalDecision(args.approval_id, approved),),
                    expected_execution_id=args.expected_execution_id,
                )
            else:
                snapshot = engine.resume(
                    args.run_id,
                    approved=approved,
                    expected_execution_id=args.expected_execution_id,
                )
        _print(
            _snapshot_cli_mapping(
                snapshot, simulated=args.scripted_decisions is not None
            )
        )
        return _strict_snapshot_exit(snapshot, args.strict_exit)
    except _CliDiagnosticError as error:
        _print_error(
            error.code,
            error.message,
            file=error.file,
            line=error.line,
            column=error.column,
            path=error.path,
            keyword=error.keyword,
            hint=error.hint,
        )
        return 2
    except RunNotFoundError as error:
        _print_error("RUN_NOT_FOUND", str(error))
        return 3
    except RunExecutionMismatchError as error:
        _print_error(error.code, str(error))
        return 4
    except RunConflictError as error:
        _print_error("RUN_CONFLICT", str(error))
        return 4
    except RunJournalError as error:
        _print_error("RUN_JOURNAL_FAILED", str(error))
        return 5
    except WorkflowRunError as error:
        _print_error("WORKFLOW_RUN_FAILED", str(error))
        return 5
    except RuntimeError as error:
        if not str(error).startswith("Install "):
            raise
        _print_error("RUNTIME_UNAVAILABLE", str(error))
        return 5
    except EventCursorError as error:
        _print_error(error.code, str(error))
        return 2
    except (OSError, WorkflowValidationError) as error:
        _print_error("WORKFLOW_INVALID", str(error))
        return 2


def _engine(
    journal: RunJournal,
    registries: list[str],
    decisions_path: Path | None,
    driver_name: str,
    *,
    file_workspace: Path | None = None,
) -> WorkflowEngine:
    return assemble_runtime(
        _assembly(
            journal,
            registries,
            decisions_path,
            driver_name,
            file_workspace=file_workspace,
        )
    )


def _assembly(
    journal: RunJournal,
    registries: list[str],
    decisions_path: Path | None,
    driver_name: str,
    *,
    file_workspace: Path | None = None,
) -> RuntimeAssembly:
    executor = FunctionToolExecutor(
        context_factory=_tool_context_factory(file_workspace)
    )
    for target in registries:
        _load_registry(target)(executor)
    drivers: dict[str, ScriptedAgentDriver] = {}
    if decisions_path is not None:
        raw = _load_json(decisions_path, "scripted decisions")
        decisions: object
        if isinstance(raw, list):
            decisions = tuple(AgentDecision.from_mapping(item) for item in raw)
        elif (
            isinstance(raw, Mapping)
            and set(raw) == {"schema_version", "agents"}
            and raw.get("schema_version") == SCRIPTED_DECISIONS_SCHEMA_V1
            and isinstance(raw.get("agents"), Mapping)
        ):
            agents = raw["agents"]
            assert isinstance(agents, Mapping)
            if any(
                not isinstance(address, str)
                or not isinstance(items, list)
                for address, items in agents.items()
            ):
                raise WorkflowValidationError(
                    "scripted Agent entries must map addresses to decision lists"
                )
            decisions = {
                address: tuple(AgentDecision.from_mapping(item) for item in items)
                for address, items in agents.items()
            }
        else:
            raise WorkflowValidationError(
                "scripted decisions must be a legacy list or v6-scripted-decisions-v1"
            )
        drivers[driver_name] = ScriptedAgentDriver(decisions)  # type: ignore[arg-type]
    return RuntimeAssembly(journal, executor, drivers)


def _project_runtime(
    journal: RunJournal,
    project: Path,
    target_config: Path | None,
    registries: list[str],
    *,
    file_workspace: Path | None = None,
    driver_factories: list[str] | None = None,
):  # type: ignore[no-untyped-def]
    if target_config is None:
        raise WorkflowValidationError("project execution requires --target-config")
    from .portability import PortableTarget
    from .runtime import load_project_runtime

    target = PortableTarget.from_mapping(_load_json(target_config, "target config"))
    return load_project_runtime(
        project,
        target,
        journal,
        registries=tuple(registries),
        tool_context_factory=_tool_context_factory(file_workspace),
        driver_factories=_driver_factories(driver_factories or []),
    )


def _execution_engine(
    journal: RunJournal,
    *,
    project: Path | None,
    target_config: Path | None,
    run_id: str,
    registries: list[str],
    decisions_path: Path | None = None,
    driver_name: str = "scripted",
    file_workspace: Path | None = None,
    driver_factories: list[str] | None = None,
) -> WorkflowEngine:
    if project is None:
        if target_config is not None:
            raise WorkflowValidationError("--target-config requires --project")
        return _engine(
            journal,
            registries,
            decisions_path,
            driver_name,
            file_workspace=file_workspace,
        )
    if decisions_path is not None:
        raise WorkflowValidationError(
            "project resume uses frozen target bindings; resume without --project "
            "for the explicit offline scripted path"
        )
    if target_config is None:
        raise WorkflowValidationError("project resume requires --target-config")
    from .portability import PortableTarget
    from .runtime import load_resume_runtime

    target = PortableTarget.from_mapping(_load_json(target_config, "target config"))
    return load_resume_runtime(
        project,
        target,
        journal,
        run_id,
        registries=tuple(registries),
        tool_context_factory=_tool_context_factory(file_workspace),
        driver_factories=_driver_factories(driver_factories or []),
    ).engine


def _target_capabilities(
    tools_path: Path | None,
    permissions: list[str],
    bindings: list[str],
) -> BundleTarget | None:
    if tools_path is None:
        if permissions or bindings:
            raise WorkflowValidationError(
                "--target-tools is required with target permission or credential bindings"
            )
        return None
    raw = _load_json(tools_path, "target tools")
    if not isinstance(raw, list):
        raise WorkflowValidationError("target tools must be a JSON list")
    return BundleTarget(
        trusted_tools=tuple(ToolReference.from_mapping(item) for item in raw),
        allowed_permissions=frozenset(permissions),
        credential_bindings=frozenset(bindings),
    )


def _load_registry(target: str) -> Callable[[FunctionToolExecutor], None]:
    module_name, separator, attribute = target.partition(":")
    if not separator or not module_name or not attribute:
        raise WorkflowValidationError("tool registry must use MODULE:CALLABLE")
    root_name = module_name.split(".", 1)[0]

    def selected(name: str) -> bool:
        return name == root_name or name.startswith(root_name + ".")

    saved_modules = {
        name: module for name, module in sys.modules.items() if selected(name)
    }
    for name in saved_modules:
        sys.modules.pop(name, None)
    import_root = str(Path.cwd().resolve())
    sys.path.insert(0, import_root)
    try:
        candidate = getattr(importlib.import_module(module_name), attribute)
    except (ImportError, AttributeError) as error:
        raise WorkflowValidationError(
            f"could not load trusted tool registry {target}"
        ) from error
    finally:
        if sys.path and sys.path[0] == import_root:
            sys.path.pop(0)
        else:
            try:
                sys.path.remove(import_root)
            except ValueError:
                pass
        for name in tuple(sys.modules):
            if selected(name):
                sys.modules.pop(name, None)
        sys.modules.update(saved_modules)
    if not callable(candidate):
        raise WorkflowValidationError("trusted tool registry is not callable")
    return candidate


def _tool_context_factory(file_workspace: Path | None):  # type: ignore[no-untyped-def]
    if file_workspace is None:
        return None
    workspace = FileWorkspace.under(file_workspace)

    def create(
        request: ToolExecutionRequest, cancellation: CancellationToken
    ) -> ToolContext:
        return ToolContext(request.action_id, cancellation, workspace)

    return create


def _driver_factories(targets: list[str]):  # type: ignore[no-untyped-def]
    from .runtime import DriverFactory

    factories: dict[str, DriverFactory] = {}
    for target in targets:
        module_name, separator, attribute = target.partition(":")
        if not separator or not module_name or not attribute:
            raise WorkflowValidationError(
                "driver factory must use MODULE:OBJECT"
            )
        try:
            candidate = getattr(importlib.import_module(module_name), attribute)
        except (ImportError, AttributeError) as error:
            raise WorkflowValidationError(
                f"could not load authorized driver factory {target}"
            ) from error
        if not isinstance(candidate, DriverFactory):
            raise WorkflowValidationError(
                "authorized driver factory object must be DriverFactory"
            )
        if candidate.name in factories:
            raise WorkflowValidationError("driver factory names must be unique")
        factories[candidate.name] = candidate
    return factories


def _keyed_paths(values: list[str], *, label: str) -> dict[str, Path]:
    result: dict[str, Path] = {}
    for value in values:
        key, separator, raw_path = value.partition("=")
        if not separator or not key or not raw_path or key in result:
            raise WorkflowValidationError(f"{label} must use unique KEY=PATH values")
        result[key] = Path(raw_path)
    return result


def _strict_snapshot_exit(snapshot: RunSnapshot, strict: bool) -> int:
    if not strict:
        return 0
    return _STRICT_STATUS_EXIT_CODES.get(snapshot.status, 10)


def _snapshot_cli_mapping(
    snapshot: RunSnapshot, *, simulated: bool = False
) -> dict[str, object]:
    result: dict[str, object] = dict(snapshot.to_mapping())
    if simulated:
        result["execution_mode"] = "simulated"
        result["model_evaluation"] = False
    failure = snapshot.state.get("failure")
    if isinstance(failure, Mapping):
        code = failure.get("code")
        path = failure.get("path")
        keyword = failure.get("keyword")
        step_id = failure.get("step_id")
        stable_code = code if isinstance(code, str) else "WORKFLOW_BLOCKED"
        diagnostic: dict[str, object] = {
            "schema_version": CLI_DIAGNOSTIC_SCHEMA_V1,
            "code": stable_code,
            "message": "workflow did not complete successfully",
            "path": path if isinstance(path, str) else None,
            "keyword": keyword if isinstance(keyword, str) else None,
            "step_id": step_id if isinstance(step_id, str) else None,
            "hint": _workflow_failure_hint(stable_code),
        }
        result["diagnostic"] = diagnostic
    return result


def _workflow_failure_hint(code: str) -> str:
    if code in {
        "STEP_INPUT_INVALID",
        "STEP_OUTPUT_INVALID",
        "AGENT_MODEL_OUTPUT_INVALID",
        "AGENT_TOOL_OUTPUT_INVALID",
    }:
        return "Fix the named step value to satisfy its declared JSON Schema."
    if code == "AGENT_TOOL_SCOPE_DENIED":
        return "Use one of the Agent step's frozen tool names and identities."
    if code in {"STEP_TIMEOUT", "RUN_TIMEOUT"}:
        return "Inspect the persisted events before changing the declared timeout."
    return "Inspect the named step and persisted events; rejected values are omitted."


def _load_workflow(path: Path) -> WorkflowSpec:
    try:
        content = path.read_text(encoding="utf-8")
    except OSError as error:
        raise _CliDiagnosticError(
            "WORKFLOW_READ_ERROR",
            "workflow file could not be read",
            file=str(path),
            path="/",
            hint="Check that the path exists and is readable.",
        ) from error
    if path.suffix.lower() in {".yaml", ".yml"}:
        try:
            import yaml  # type: ignore[import-not-found]
        except ImportError as error:
            raise WorkflowValidationError(
                "YAML support is optional; install platform-lightweight-agent-core[yaml]"
            ) from error
        try:
            raw: object = yaml.safe_load(content)
        except yaml.YAMLError as error:
            mark = getattr(error, "problem_mark", None)
            raise _CliDiagnosticError(
                "WORKFLOW_PARSE_ERROR",
                "workflow is not valid YAML",
                file=str(path),
                line=(mark.line + 1 if mark is not None else None),
                column=(mark.column + 1 if mark is not None else None),
                path="/",
                keyword="syntax",
                hint="Fix the YAML syntax at the reported line and column.",
            ) from error
    else:
        try:
            raw = json.loads(content)
        except json.JSONDecodeError as error:
            raise _CliDiagnosticError(
                "WORKFLOW_PARSE_ERROR",
                "workflow is not valid JSON",
                file=str(path),
                line=error.lineno,
                column=error.colno,
                path="/",
                keyword="syntax",
                hint="Fix the JSON syntax at the reported line and column.",
            ) from error
    try:
        return WorkflowSpec.from_mapping(raw)
    except WorkflowValidationError as error:
        raise _CliDiagnosticError(
            "WORKFLOW_CONTRACT_INVALID",
            str(error),
            file=str(path),
            path="/",
            keyword="contract",
            hint="Fix the workflow field or node named by this contract error.",
        ) from error


def _load_json(path: Path, label: str) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except OSError as error:
        raise _CliDiagnosticError(
            "INPUT_READ_ERROR",
            f"{label} could not be read",
            file=str(path),
            path="/",
            hint="Check that the path exists and is readable.",
        ) from error
    except json.JSONDecodeError as error:
        raise _CliDiagnosticError(
            "INPUT_PARSE_ERROR",
            f"{label} is not valid JSON",
            file=str(path),
            line=error.lineno,
            column=error.colno,
            path="/",
            keyword="syntax",
            hint="Fix the JSON syntax at the reported line and column.",
        ) from error


def _load_inputs(inline: str, path: Path | None) -> Mapping[str, Any]:
    if path is not None:
        try:
            if path.stat().st_size > _MAX_CLI_INPUT_BYTES:
                raise _CliDiagnosticError(
                    "INPUT_TOO_LARGE",
                    "workflow inputs exceed the CLI input limit",
                    file=str(path),
                    path="/",
                    keyword="maxBytes",
                    hint="Pass a FileReference for large data instead of inline JSON.",
                )
        except OSError as error:
            raise _CliDiagnosticError(
                "INPUT_READ_ERROR",
                "workflow inputs could not be read",
                file=str(path),
                path="/",
                hint="Check that the path exists and is readable.",
            ) from error
        raw = _load_json(path, "workflow inputs")
    else:
        if len(inline.encode("utf-8")) > _MAX_CLI_INPUT_BYTES:
            raise _CliDiagnosticError(
                "INPUT_TOO_LARGE",
                "workflow inputs exceed the CLI input limit",
                path="/",
                keyword="maxBytes",
                hint="Pass a FileReference for large data instead of inline JSON.",
            )
        try:
            raw = json.loads(inline)
        except json.JSONDecodeError as error:
            raise _CliDiagnosticError(
                "INPUT_PARSE_ERROR",
                "workflow inputs are not valid JSON",
                line=error.lineno,
                column=error.colno,
                path="/",
                keyword="syntax",
                hint="Fix the inline JSON passed to --input or --output.",
            ) from error
    if not isinstance(raw, Mapping):
        raise WorkflowValidationError("workflow inputs must be a JSON object")
    return raw


def _print(value: object) -> None:
    print(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")))


def _print_error(
    code: str,
    message: str,
    *,
    file: str | None = None,
    line: int | None = None,
    column: int | None = None,
    path: str | None = None,
    keyword: str | None = None,
    hint: str | None = None,
) -> None:
    print(
        json.dumps(
            {
                "schema_version": CLI_DIAGNOSTIC_SCHEMA_V1,
                "code": code,
                "message": message,
                "file": file,
                "line": line,
                "column": column,
                "path": path,
                "keyword": keyword,
                "hint": hint,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ),
        file=sys.stderr,
    )


def _follow_events(
    engine: WorkflowEngine,
    run_id: str,
    after: int,
    *,
    expected_execution_id: str | None = None,
    cursor: EventCursor | None = None,
) -> None:
    try:
        import anyio
    except ImportError as error:
        raise WorkflowValidationError(
            "event following requires platform-lightweight-agent-core[workflow]"
        ) from error

    async def follow() -> None:
        async for event in engine.stream_events(
            run_id,
            after=0 if cursor is not None else after,
            expected_execution_id=expected_execution_id,
            cursor=cursor,
        ):
            _print(event.to_mapping())

    anyio.run(follow)


if __name__ == "__main__":  # pragma: no cover - console-script entry point
    raise SystemExit(main())
