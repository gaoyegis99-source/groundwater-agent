"""Project process environment and isolated Docker boundary.

The resolver is standard-library only so ``doctor`` and bootstrap work after a
factory reset. Persistent, cache and ephemeral paths follow XDG instead of a
repository-local ``runtime`` tree.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shlex
import stat
import sys
from pathlib import Path

try:
    from .xdg_layout import (
        LayoutRefusal,
        owned_path as _owned_path,
        resolve_lifecycle_paths,
    )
except ImportError:  # Direct execution through the project launchers.
    from xdg_layout import (  # type: ignore
        LayoutRefusal,
        owned_path as _owned_path,
        resolve_lifecycle_paths,
    )

try:
    from .toolchain_contract import toolchain_name
except ImportError:  # Direct execution through the project launchers.
    from toolchain_contract import toolchain_name  # type: ignore

try:
    from .dependency_contract import dependency_name
except ImportError:  # Direct execution through the project launchers.
    from dependency_contract import dependency_name  # type: ignore

PROJECT = "groundwater-v6"
SOURCE_ROOT = Path(__file__).resolve().parents[2]
PROJECT_ROOT = SOURCE_ROOT.parent


class EnvironmentRefusalError(LayoutRefusal):
    """A maintenance path or Docker target violates the project contract."""


EnvironmentRefusal = EnvironmentRefusalError


def _refusal(error: LayoutRefusal) -> EnvironmentRefusal:
    return EnvironmentRefusal(str(error))


def owned_path(value: str | Path, allowed: Path) -> Path:
    """Compatibility wrapper exposing project-specific refusal types."""

    try:
        return _owned_path(value, allowed)
    except LayoutRefusal as error:
        raise _refusal(error) from error


def layout() -> dict:
    path = PROJECT_ROOT / "project-layout.json"
    if not path.exists():
        raise EnvironmentRefusal("PROJECT_NOT_BOOTSTRAPPED")
    if path.is_symlink():
        raise EnvironmentRefusal("LAYOUT_SYMLINK_REJECTED")
    try:
        result = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise EnvironmentRefusal("LAYOUT_INVALID") from error
    if (
        not isinstance(result, dict)
        or result.get("project") != PROJECT
        or result.get("schema_version") != 4
        or result.get("source") != "source"
        or not isinstance(result.get("lifecycle"), dict)
        or not isinstance(result.get("docker"), dict)
    ):
        raise EnvironmentRefusal("LAYOUT_INVALID")
    if result["lifecycle"] != {
        "config": "xdg-config",
        "data": "xdg-data",
        "state": "xdg-state",
        "cache": "xdg-cache",
        "runtime": "xdg-runtime",
    }:
        raise EnvironmentRefusal("LAYOUT_INVALID")
    return result


def lifecycle(inherited: dict[str, str] | None = None):
    try:
        return resolve_lifecycle_paths(inherited)
    except LayoutRefusal as error:
        raise _refusal(error) from error


def _workspace_identity(root: Path) -> str:
    return hashlib.sha256(str(root.absolute()).encode("utf-8")).hexdigest()[:16]


def validation_cache_root(
    root: Path = SOURCE_ROOT, inherited: dict[str, str] | None = None
) -> Path:
    """Return the rebuildable validation root for one linked worktree."""

    resolved = lifecycle(inherited)
    return owned_path(
        Path("validation/worktrees") / _workspace_identity(root), resolved.build
    )


def validation_state_root(
    root: Path = SOURCE_ROOT, inherited: dict[str, str] | None = None
) -> Path:
    """Return the durable validation-receipt root for one linked worktree."""

    resolved = lifecycle(inherited)
    return owned_path(
        Path("validation/worktrees") / _workspace_identity(root), resolved.audit
    )


def socket_path(inherited: dict[str, str] | None = None) -> Path:
    contract = layout()["docker"]
    if contract.get("socket") != "docker.sock":
        raise EnvironmentRefusal("DOCKER_SOCKET_OUTSIDE_PROJECT")
    return lifecycle(inherited).socket


def require_socket(inherited: dict[str, str] | None = None) -> Path:
    path = socket_path(inherited)
    try:
        info = path.stat()
    except OSError as error:
        raise EnvironmentRefusal("PROJECT_DOCKER_UNAVAILABLE") from error
    if not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.getuid():
        raise EnvironmentRefusal("PROJECT_DOCKER_UNAVAILABLE")
    return path


def paths(inherited: dict[str, str] | None = None) -> dict[str, str]:
    env = dict(os.environ if inherited is None else inherited)
    layout()
    resolved = lifecycle(env)
    try:
        default_toolchain = toolchain_name(SOURCE_ROOT)
        default_dependencies = dependency_name(SOURCE_ROOT)
    except ValueError as error:
        raise EnvironmentRefusal(str(error)) from error
    toolchain = owned_path(default_toolchain, resolved.toolchains)
    configured_toolchain = env.get("V6_TOOLCHAIN_ENVIRONMENT")
    if configured_toolchain:
        try:
            toolchain = owned_path(configured_toolchain, resolved.toolchains)
        except LayoutRefusal as error:
            raise _refusal(error) from error
    generated = owned_path("assemblies", resolved.build)
    configured_generated = env.get("PLATFORM_GENERATED_ROOT")
    if configured_generated:
        try:
            generated = owned_path(configured_generated, resolved.build)
        except LayoutRefusal as error:
            raise _refusal(error) from error
    interpreter = Path(env.get("V6_PYTHON", str(toolchain / "bin/python")))
    try:
        owned_path(interpreter.parent, resolved.toolchains)
    except LayoutRefusal as error:
        raise _refusal(error) from error
    if interpreter.name not in {"python", "python3", "python3.12"}:
        raise EnvironmentRefusal("PROJECT_PYTHON_INVALID")
    return {
        "V6_SOURCE_ROOT": str(SOURCE_ROOT),
        "V6_PROJECT_ROOT": str(PROJECT_ROOT),
        "V6_CONFIG_ROOT": str(resolved.config),
        "V6_DATA_ROOT": str(resolved.data),
        "V6_STATE_ROOT": str(resolved.state),
        "V6_CACHE_ROOT": str(resolved.cache),
        "V6_RUNTIME_ROOT": str(resolved.runtime),
        "V6_INSTANCES_ROOT": str(resolved.instances),
        "V6_AUDIT_ROOT": str(resolved.audit),
        # Compatibility variables retain their names, but no longer point into
        # the repository or share one deletion boundary.
        "V6_RUNTIME_WORK_ROOT": str(resolved.build),
        "V6_RUNTIME_ACTIVE_ROOT": str(resolved.state / "instances"),
        "V6_RUNTIME_ARCHIVE_ROOT": str(resolved.state / "archive"),
        "V6_RUNTIME_TOOLCHAIN_ROOT": str(resolved.toolchains),
        "V6_DOCKER_DATA_ROOT": str(resolved.docker),
        "V6_TOOLCHAIN_ENVIRONMENT": str(toolchain),
        "V6_TOOL_ROOT": str(resolved.toolchains / "tools"),
        "PLATFORM_DEPENDENCY_ROOT": str(
            owned_path(default_dependencies, owned_path("deps", resolved.cache))
        ),
        "V6_PYTHON": str(interpreter),
        "PLATFORM_GENERATED_ROOT": str(generated),
        "UV_PROJECT_ENVIRONMENT": str(toolchain),
        "XDG_CONFIG_HOME": str(resolved.config_home),
        "XDG_DATA_HOME": str(resolved.data_home),
        "XDG_STATE_HOME": str(resolved.state_home),
        "XDG_CACHE_HOME": str(resolved.cache_home),
        "XDG_RUNTIME_DIR": str(resolved.runtime_home),
    }


def sensitive_test_variable(name: str) -> bool:
    return name.startswith(
        (
            "V6_",
            "V7_",
            "PLATFORM_",
            "DATA_",
            "KNOWLEDGE_",
            "EXECUTOR_",
            "POSTGRES_",
            "MINIO_",
        )
    ) or name.endswith(("_TOKEN", "_SECRET", "_PASSWORD", "_DATABASE_URL", "_DSN"))


def child_environment(
    *, testing: bool = False, inherited: dict[str, str] | None = None
) -> dict[str, str]:
    original = dict(os.environ if inherited is None else inherited)
    resolved = paths(original)
    result = {
        key: value
        for key, value in original.items()
        if key
        not in {
            "DOCKER_CONTEXT",
            "DOCKER_TLS",
            "DOCKER_TLS_VERIFY",
            "DOCKER_CERT_PATH",
            "PYTHONHOME",
            "PYTHONPATH",
            "UV_PROJECT",
            "UV_WORKING_DIRECTORY",
            "UV_ACTIVE",
        }
        and not (testing and sensitive_test_variable(key))
    }
    result.update(resolved)
    result["PYTHONPATH"] = str(SOURCE_ROOT)
    result["VIRTUAL_ENV"] = result["UV_PROJECT_ENVIRONMENT"]
    result["DOCKER_HOST"] = f"unix://{socket_path(result)}"
    result["PYTHONDONTWRITEBYTECODE"] = "1"
    result["UV_LINK_MODE"] = "copy"
    result["PATH"] = os.pathsep.join(
        (
            str(PROJECT_ROOT / "bin"),
            str(Path(result["UV_PROJECT_ENVIRONMENT"]) / "bin"),
            original.get("PATH", os.defpath),
        )
    )
    return result


def activate(*, testing: bool = False) -> None:
    resolved = child_environment(testing=testing)
    for key in tuple(os.environ):
        if key not in resolved:
            del os.environ[key]
    os.environ.update(resolved)


def docker_executable() -> str:
    socket_path()
    return str(PROJECT_ROOT / "bin/docker")


def docker_arguments(arguments: list[str]) -> list[str]:
    for index, arg in enumerate(arguments):
        executes_container_command = bool(arguments) and (
            arguments[0] in {"run", "exec"}
            or (
                arguments[0] == "compose"
                and any(
                    item in {"run", "exec"} for item in arguments[1:index]
                )
            )
        )
        configured_entrypoints = [
            arguments[position + 1]
            for position, item in enumerate(arguments[:index])
            if item == "--entrypoint" and position + 1 < index
        ]
        configured_entrypoints.extend(
            item.split("=", 1)[1]
            for item in arguments[:index]
            if item.startswith("--entrypoint=")
        )
        entrypoint_is_interpreter = any(
            Path(item).name in {"sh", "bash", "python", "python3"}
            for item in configured_entrypoints
        )
        interpreter_command_flag = (
            arg == "-c"
            and executes_container_command
            and (
                entrypoint_is_interpreter
                or (
                    index > 0
                    and Path(arguments[index - 1]).name
                    in {"sh", "bash", "python", "python3"}
                )
            )
        )
        if interpreter_command_flag:
            continue
        if (
            arg in {"-H", "-c", "--host", "--context"}
            or arg.startswith(("--host=", "--context="))
            or arg.startswith(("-H", "-c"))
        ):
            raise EnvironmentRefusal("DOCKER_TARGET_OVERRIDE_REJECTED")
    return ["/usr/bin/docker", *arguments]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("shell", "docker"))
    parser.add_argument("--testing", action="store_true")
    args, rest = parser.parse_known_args()
    try:
        if args.action == "docker":
            command = docker_arguments(rest)
            require_socket()
            os.execve(command[0], command, child_environment())
        if rest:
            parser.error("unexpected arguments")
        result = child_environment(testing=args.testing)
        for key in sorted(set(os.environ) - set(result)):
            print("unset " + shlex.quote(key))
        keys = set(paths()) | {
            "DOCKER_HOST",
            "PATH",
            "PYTHONDONTWRITEBYTECODE",
            "UV_LINK_MODE",
            "PYTHONPATH",
            "VIRTUAL_ENV",
        }
        for key in sorted(keys):
            print(f"export {key}={shlex.quote(result[key])}")
    except (EnvironmentRefusal, LayoutRefusal) as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(75) from error


if __name__ == "__main__":
    main()
