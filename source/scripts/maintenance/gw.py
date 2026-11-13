#!/usr/bin/env python3
"""Unified, factory-reset-safe command line for Groundwater V6."""

from __future__ import annotations

import argparse
import getpass
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.maintenance import (  # noqa: E402
    bootstrap_toolchain,
    config_runtime,
    dependency_materializer,
    instance_runtime,
    lifecycle_cleanup,
    migration_runtime,
    research_artifacts,
    workspace_cleanup,
)
from scripts.maintenance.contracts import ContractRefusal  # noqa: E402
from scripts.maintenance.daemon_runtime import DaemonRefusal  # noqa: E402
from scripts.maintenance.project_environment import (  # noqa: E402
    EnvironmentRefusal,
    PROJECT_ROOT,
    SOURCE_ROOT,
    child_environment,
    layout,
    lifecycle,
)
from scripts.maintenance.xdg_layout import (  # noqa: E402
    LayoutRefusal,
    private_root_status,
)


def _version(command: list[str]) -> str | None:
    try:
        completed = subprocess.run(
            command, capture_output=True, text=True, check=False, timeout=10
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if completed.returncode:
        return None
    lines = (completed.stdout or completed.stderr).strip().splitlines()
    return lines[0] if lines else None


def doctor() -> dict[str, Any]:
    contract = layout()
    resolved = lifecycle()
    permissions = private_root_status(resolved)
    configuration = config_runtime.status(SOURCE_ROOT, resolved, "research")
    rootless = instance_runtime.rootless_mapping_status()
    tools = {
        "docker": _version(["docker", "--version"]),
        "compose": _version(["docker", "compose", "version"]),
    }
    residue = workspace_cleanup.workspace_clean_plan(PROJECT_ROOT, SOURCE_ROOT)
    issues = []
    if (PROJECT_ROOT / "runtime").exists():
        issues.append("LEGACY_RUNTIME_PRESENT")
    if (SOURCE_ROOT / ".env").exists():
        issues.append("SOURCE_ENV_MIGRATION_PENDING")
    if configuration["status"] != "PASS":
        issues.append("CONFIGURATION_NOT_READY")
    if rootless["status"] != "PASS":
        issues.append("ROOTLESS_MAPPING_MISSING")
    if any(value not in {"PASS", "MISSING"} for value in permissions.values()):
        issues.append("XDG_ROOT_PERMISSIONS_INVALID")
    for name in ("docker", "compose"):
        if tools[name] is None:
            issues.append(f"{name.upper()}_MISSING")
    if residue["candidateCount"]:
        issues.append("WORKSPACE_RESIDUE_PRESENT")
    return {
        "status": "PASS" if not issues else "NOT_READY",
        "schema": contract["schema_version"],
        "project": contract["project"],
        "paths": {
            "config": str(resolved.config),
            "data": str(resolved.data),
            "state": str(resolved.state),
            "cache": str(resolved.cache),
            "runtime": str(resolved.runtime),
            "docker": str(resolved.docker),
            "socket": str(resolved.socket),
        },
        "configuration": configuration,
        "permissions": permissions,
        "tools": tools,
        "rootless": rootless,
        "workspaceResidue": {
            "candidates": residue["candidateCount"],
            "files": residue["files"],
            "bytes": residue["bytes"],
        },
        "issues": issues,
        "docker_contacted": False,
    }


def _apply_or_plan(
    plan: dict[str, Any], confirmation: str | None, apply
) -> dict[str, Any]:
    if confirmation is None:
        return {"status": "PLAN", **plan}
    return apply(plan, confirmation)


def _instance(args: argparse.Namespace) -> dict[str, Any]:
    resolved = lifecycle()
    if args.instance_action == "status":
        _, manifest = instance_runtime.read_manifest(resolved, args.instance)
        return {"status": "PASS", "instance": manifest}
    if args.instance_action == "stop":
        return instance_runtime.stop_instance(
            resolved, args.instance, docker=PROJECT_ROOT / "bin/docker"
        )
    plan = instance_runtime.destroy_plan(resolved, args.instance)
    return _apply_or_plan(
        plan,
        args.confirm,
        lambda value, confirmation: instance_runtime.apply_destroy(
            resolved,
            value,
            confirmation,
            docker=PROJECT_ROOT / "bin/docker",
        ),
    )


def _clean(args: argparse.Namespace) -> dict[str, Any]:
    resolved = lifecycle()
    if args.clean_action == "workspace":
        plan = workspace_cleanup.workspace_clean_plan(PROJECT_ROOT, SOURCE_ROOT)
        return _apply_or_plan(
            plan,
            args.confirm,
            lambda value, confirmation: workspace_cleanup.apply_workspace_clean(
                resolved, value, confirmation
            ),
        )
    plan = lifecycle_cleanup.cache_clean_plan(resolved)
    return _apply_or_plan(
        plan,
        args.confirm,
        lambda value, confirmation: lifecycle_cleanup.apply_plan(
            resolved, value, confirmation
        ),
    )


def _engine(args: argparse.Namespace) -> dict[str, Any]:
    resolved = lifecycle()
    plan = lifecycle_cleanup.engine_reset_plan(resolved)
    return _apply_or_plan(
        plan,
        args.confirm,
        lambda value, confirmation: lifecycle_cleanup.apply_plan(
            resolved, value, confirmation
        ),
    )


def _config(args: argparse.Namespace) -> dict[str, Any]:
    resolved = lifecycle()
    if args.config_action == "status":
        return config_runtime.status(SOURCE_ROOT, resolved, args.profile)
    plan = config_runtime.migration_plan(SOURCE_ROOT, resolved)
    return _apply_or_plan(
        plan,
        args.confirm,
        lambda value, confirmation: config_runtime.apply_migration(
            SOURCE_ROOT, resolved, value, confirmation
        ),
    )


def _snapshot(args: argparse.Namespace) -> dict[str, Any]:
    resolved = lifecycle()
    if args.snapshot_action == "create":
        return research_artifacts.create_snapshot(resolved, args.instance)
    if args.snapshot_action == "verify":
        return research_artifacts.verify(
            Path(args.snapshot).absolute(), "research-snapshot.v1"
        )
    plan = research_artifacts.restore_plan(
        resolved, Path(args.snapshot).absolute(), args.instance
    )
    return _apply_or_plan(
        plan,
        args.confirm,
        lambda value, confirmation: research_artifacts.apply_restore(
            resolved, value, confirmation
        ),
    )


def _archive(args: argparse.Namespace) -> dict[str, Any]:
    if args.archive_action == "verify":
        return research_artifacts.verify(
            Path(args.archive).absolute(), "research-archive.v1"
        )
    return research_artifacts.create_archive(
        lifecycle(),
        SOURCE_ROOT,
        args.instance,
        args.run,
        args.level,
        Path(args.assessment).absolute() if args.assessment else None,
    )


def _migrate(args: argparse.Namespace) -> dict[str, Any]:
    if args.migrate_action == "plan":
        return migration_runtime.create_plan(
            lifecycle(),
            args.instance,
            Path(args.identity_map).absolute(),
            docker=PROJECT_ROOT / "bin/docker",
        )
    return migration_runtime.apply_plan(
        lifecycle(),
        Path(args.plan).absolute(),
        args.confirm,
        docker=PROJECT_ROOT / "bin/docker",
        source_root=SOURCE_ROOT,
    )


def _exec_python(script: Path, arguments: list[str]) -> None:
    current = bootstrap_toolchain.status(SOURCE_ROOT, lifecycle())
    if current["status"] != "PASS":
        raise EnvironmentRefusal("PROJECT_TOOLCHAIN_NOT_READY")
    environment = child_environment()
    python = environment["V6_PYTHON"]
    if python != current["python"]:
        raise EnvironmentRefusal("PROJECT_TOOLCHAIN_NOT_READY")
    os.execve(python, [python, "-B", str(script), *arguments], environment)


def _deps(args: argparse.Namespace) -> dict[str, Any]:
    resolved = lifecycle()
    if args.deps_action == "status":
        return dependency_materializer.status(SOURCE_ROOT, resolved)
    if args.deps_action == "materialize":
        return dependency_materializer.materialize(
            SOURCE_ROOT, resolved, set(args.scope)
        )
    return dependency_materializer.verify_scope(SOURCE_ROOT, resolved, args.scope)


def _parse(argv: list[str]) -> argparse.Namespace:
    json_output = "--json" in argv
    argv = [item for item in argv if item != "--json"]
    parser = argparse.ArgumentParser(prog="gw", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("doctor")
    bootstrap = commands.add_parser("bootstrap")
    bootstrap_modes = bootstrap.add_mutually_exclusive_group(required=True)
    bootstrap_modes.add_argument("--status", action="store_true")
    bootstrap_modes.add_argument("--sync", action="store_true")
    deps = commands.add_parser("deps")
    deps_commands = deps.add_subparsers(dest="deps_action", required=True)
    deps_commands.add_parser("status")
    materialize = deps_commands.add_parser("materialize")
    materialize.add_argument(
        "--scope",
        action="append",
        choices=sorted(dependency_materializer.SCOPES),
        required=True,
    )
    verify = deps_commands.add_parser("verify")
    verify.add_argument(
        "--scope", choices=sorted(dependency_materializer.SCOPES), required=True
    )
    dev = commands.add_parser("dev")
    dev.add_argument("arguments", nargs=argparse.REMAINDER)
    instance = commands.add_parser("instance")
    instance_commands = instance.add_subparsers(dest="instance_action", required=True)
    for action in ("status", "stop", "destroy"):
        command = instance_commands.add_parser(action)
        command.add_argument("--instance", required=True)
        if action == "destroy":
            command.add_argument("--confirm")

    clean = commands.add_parser("clean")
    clean_commands = clean.add_subparsers(dest="clean_action", required=True)
    cache = clean_commands.add_parser("cache")
    cache.add_argument("--confirm")
    workspace = clean_commands.add_parser("workspace")
    workspace.add_argument("--confirm")

    engine = commands.add_parser("engine")
    engine_commands = engine.add_subparsers(dest="engine_action", required=True)
    reset = engine_commands.add_parser("reset")
    reset.add_argument("--confirm")

    config = commands.add_parser("config")
    config_commands = config.add_subparsers(dest="config_action", required=True)
    config_status = config_commands.add_parser("status")
    config_status.add_argument("--profile", choices=("research",), default="research")
    migrate_env = config_commands.add_parser("migrate-env")
    migrate_env.add_argument("--confirm")

    snapshot = commands.add_parser("snapshot")
    snapshot_commands = snapshot.add_subparsers(dest="snapshot_action", required=True)
    snapshot_create = snapshot_commands.add_parser("create")
    snapshot_create.add_argument("--instance", required=True)
    snapshot_verify = snapshot_commands.add_parser("verify")
    snapshot_verify.add_argument("--snapshot", required=True)
    snapshot_restore = snapshot_commands.add_parser("restore")
    snapshot_restore.add_argument("--snapshot", required=True)
    snapshot_restore.add_argument("--instance", required=True)
    snapshot_restore.add_argument("--confirm")

    archive = commands.add_parser("archive")
    archive_commands = archive.add_subparsers(dest="archive_action", required=True)
    archive_create = archive_commands.add_parser("create")
    archive_create.add_argument("--instance", required=True)
    archive_create.add_argument("--run", required=True)
    archive_create.add_argument(
        "--level", choices=("experiment", "benchmark"), required=True
    )
    archive_create.add_argument("--assessment")
    archive_verify = archive_commands.add_parser("verify")
    archive_verify.add_argument("--archive", required=True)

    migrate = commands.add_parser("migrate")
    migrate_commands = migrate.add_subparsers(dest="migrate_action", required=True)
    migrate_plan = migrate_commands.add_parser("plan")
    migrate_plan.add_argument("--instance", required=True)
    migrate_plan.add_argument("--identity-map", required=True)
    migrate_apply = migrate_commands.add_parser("apply")
    migrate_apply.add_argument("--plan", required=True)
    migrate_apply.add_argument("--confirm", required=True)

    auth = commands.add_parser("auth")
    auth_commands = auth.add_subparsers(dest="auth_action", required=True)
    bootstrap_admin = auth_commands.add_parser("bootstrap-admin")
    bootstrap_admin.add_argument("--instance")
    bootstrap_admin.add_argument("--product", default="groundwater")
    bootstrap_admin.add_argument("--username")
    bootstrap_admin.add_argument("--display-name")
    result = parser.parse_args(argv)
    result.json = json_output
    return result


def main(argv: list[str] | None = None) -> None:
    args = _parse(list(sys.argv[1:] if argv is None else argv))
    try:
        if args.command == "doctor":
            payload = doctor()
        elif args.command == "bootstrap":
            action = (
                bootstrap_toolchain.sync if args.sync else bootstrap_toolchain.status
            )
            payload = action(SOURCE_ROOT, lifecycle())
        elif args.command == "deps":
            payload = _deps(args)
        elif args.command == "dev":
            _exec_python(SOURCE_ROOT / "scripts/development/dev.py", args.arguments)
            return
        elif args.command == "instance":
            payload = _instance(args)
        elif args.command == "clean":
            payload = _clean(args)
        elif args.command == "engine":
            payload = _engine(args)
        elif args.command == "config":
            payload = _config(args)
        elif args.command == "snapshot":
            payload = _snapshot(args)
        elif args.command == "archive":
            payload = _archive(args)
        elif args.command == "migrate":
            payload = _migrate(args)
        elif args.command == "auth":
            if args.auth_action != "bootstrap-admin":
                raise ValueError("AUTH_COMMAND_INVALID")
            for source in (
                SOURCE_ROOT / "kernel/src",
                SOURCE_ROOT / "packages/protocol-contracts/src",
            ):
                if str(source) not in sys.path:
                    sys.path.insert(0, str(source))
            from platform_kernel.access.research_auth import bootstrap_admin
            from platform_kernel.database import initialize_database

            resolved = lifecycle()
            if args.instance:
                _path, manifest = instance_runtime.read_manifest(
                    resolved, args.instance
                )
                if manifest.get("schemaVersion") != "instance-manifest.v4":
                    raise ValueError("INSTANCE_MIGRATION_REQUIRED")
                database = Path(manifest["dataRoot"]) / "kernel/kernel.sqlite3"
                compose = json.loads(
                    (Path(manifest["stateRoot"]) / "compose.json").read_text()
                )
                project_id = compose["services"]["kernel-service"]["environment"][
                    "PLATFORM_PROJECT_ID"
                ]
            else:
                if re.fullmatch(r"[a-z][a-z0-9-]{0,62}", args.product) is None:
                    raise ValueError("PRODUCT_INVALID")
                product = (
                    SOURCE_ROOT / "products" / args.product / "product-manifest.json"
                )
                document = json.loads(product.read_text())
                project_id = document["projectId"]
                database = (
                    resolved.data
                    / "research-dev"
                    / args.product
                    / "kernel/kernel.sqlite3"
                )
            initialize_database(database)
            username = args.username or input("Research admin username: ").strip()
            display_name = (
                args.display_name or input("Research admin display name: ").strip()
            )
            password = getpass.getpass("Research admin password: ")
            confirmation = getpass.getpass("Confirm password: ")
            if password != confirmation:
                raise ValueError("PASSWORD_CONFIRMATION_MISMATCH")
            user_id = bootstrap_admin(
                database,
                project_id,
                username,
                display_name,
                password,
            )
            payload = {"status": "PASS", "userId": user_id}
        else:  # pragma: no cover - argparse owns the command set
            raise ValueError("COMMAND_INVALID")
    except (
        bootstrap_toolchain.BootstrapRefusal,
        ContractRefusal,
        config_runtime.ConfigRefusal,
        DaemonRefusal,
        dependency_materializer.DependencyRefusal,
        EnvironmentRefusal,
        instance_runtime.InstanceRefusal,
        lifecycle_cleanup.CleanupRefusal,
        migration_runtime.MigrationError,
        LayoutRefusal,
        research_artifacts.ResearchArtifactError,
        workspace_cleanup.WorkspaceCleanupRefusal,
        OSError,
        ValueError,
    ) as error:
        payload = {"status": "FAILED", "code": str(error)}
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    if payload["status"] == "FAILED":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
