from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Iterable


DEV_METRIC_SERVICES = {
    "kernel": "kernel-service",
    "data": "data-service",
    "knowledge": "knowledge-next-service",
    "executor": "executor-control",
}

_SOURCE_SERVICES = (
    "kernel-service",
    "data-migrate",
    "data-service",
    "data-worker",
    "knowledge-next-migrate",
    "knowledge-next-service",
    "knowledge-next-worker",
    "executor-control",
    "executor-native-worker",
    "executor-qgis-worker",
    "executor-remote_http-worker",
)

_API_COMMANDS = {
    "kernel-service": (
        "uvicorn",
        "platform_kernel.main:app",
        "--host",
        "0.0.0.0",
        "--port",
        "8000",
        "--reload-dir",
        "/workspace/kernel/src",
        "--reload-dir",
        "/workspace/packages/protocol-contracts/src",
        "--reload",
    ),
    "data-service": (
        "uvicorn",
        "platform_data_service.main:app",
        "--host",
        "0.0.0.0",
        "--port",
        "8000",
        "--reload-dir",
        "/workspace/services/data/src",
        "--reload-dir",
        "/workspace/packages/protocol-contracts/src",
        "--reload",
    ),
    "knowledge-next-service": (
        "uvicorn",
        "platform_knowledge_next.api:create_app",
        "--factory",
        "--host",
        "0.0.0.0",
        "--port",
        "8000",
        "--reload-dir",
        "/workspace/services/knowledge-next/src",
        "--reload-dir",
        "/workspace/packages/protocol-contracts/src",
        "--reload",
    ),
    "executor-control": (
        "uvicorn",
        "platform_executor_service.main:app",
        "--host",
        "0.0.0.0",
        "--port",
        "8000",
        "--reload-dir",
        "/workspace/services/executor/src",
        "--reload-dir",
        "/workspace/packages/operator-pack/src",
        "--reload-dir",
        "/workspace/packages/protocol-contracts/src",
        "--reload",
    ),
}


def _ordered(values: Iterable[str]) -> tuple[str, ...]:
    return tuple(sorted(set(values)))


@dataclass(frozen=True)
class ChangePlan:
    paths: tuple[str, ...] = ()
    hmr: tuple[str, ...] = ()
    reload_services: tuple[str, ...] = ()
    restart_services: tuple[str, ...] = ()
    check_scopes: tuple[str, ...] = ()
    reconcile: bool = False
    controller_restart_required: bool = False

    def merge(self, other: "ChangePlan") -> "ChangePlan":
        return ChangePlan(
            paths=_ordered((*self.paths, *other.paths)),
            hmr=_ordered((*self.hmr, *other.hmr)),
            reload_services=_ordered((*self.reload_services, *other.reload_services)),
            restart_services=_ordered(
                (*self.restart_services, *other.restart_services)
            ),
            check_scopes=_ordered((*self.check_scopes, *other.check_scopes)),
            reconcile=self.reconcile or other.reconcile,
            controller_restart_required=(
                self.controller_restart_required or other.controller_restart_required
            ),
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "paths": list(self.paths),
            "hmr": list(self.hmr),
            "reloadServices": list(self.reload_services),
            "restartServices": list(self.restart_services),
            "checkScopes": list(self.check_scopes),
            "reconcile": self.reconcile,
            "controllerRestartRequired": self.controller_restart_required,
        }


def _safe_relative(value: str) -> str:
    normalized = value.replace("\\", "/")
    path = PurePosixPath(normalized)
    if path.is_absolute() or ".." in path.parts or normalized in {"", "."}:
        raise ValueError(f"DEV_CHANGE_PATH_INVALID:{value}")
    return path.as_posix()


def classify_changes(paths: Iterable[str]) -> ChangePlan:
    plan = ChangePlan(paths=_ordered(_safe_relative(path) for path in paths))
    for path in plan.paths:
        current = ChangePlan()
        filename = PurePosixPath(path).name
        if (
            filename.startswith("Dockerfile")
            or filename == "pyproject.toml"
            or path.startswith(("deployment/", "environments/"))
        ):
            current = ChangePlan(check_scopes=("all",), reconcile=True)
        elif path.startswith("web/"):
            current = ChangePlan(hmr=("web",), check_scopes=("web",))
        elif path.startswith("services/data/web/"):
            current = ChangePlan(hmr=("data-ui",), check_scopes=("data-ui",))
        elif path.startswith("services/knowledge-next/ui/"):
            current = ChangePlan(hmr=("knowledge-ui",), check_scopes=("knowledge-ui",))
        elif path.startswith(("packages/ui-boundary/", "packages/operator-ui/")):
            current = ChangePlan(
                hmr=("web", "data-ui", "knowledge-ui"),
                check_scopes=("web", "data-ui", "knowledge-ui"),
            )
        elif path.startswith("kernel/"):
            current = ChangePlan(
                reload_services=("kernel-service",), check_scopes=("kernel",)
            )
        elif path.startswith("services/data/"):
            worker_only = "/operations/worker.py" in path or path.endswith("/cli.py")
            api_only = "/api/" in path or path.endswith("/main.py")
            current = ChangePlan(
                reload_services=() if worker_only else ("data-service",),
                restart_services=() if api_only else ("data-worker",),
                check_scopes=("data",),
            )
        elif path.startswith("services/knowledge-next/"):
            current = ChangePlan(
                reload_services=("knowledge-next-service",),
                restart_services=("knowledge-next-worker",),
                check_scopes=("knowledge",),
            )
        elif path.startswith("services/executor/"):
            current = ChangePlan(
                reload_services=("executor-control",),
                restart_services=(
                    "executor-native-worker",
                    "executor-qgis-worker",
                    "executor-remote_http-worker",
                ),
                check_scopes=("executor",),
            )
        elif path.startswith("packages/operator-pack/"):
            current = ChangePlan(
                restart_services=(
                    "executor-native-worker",
                    "executor-qgis-worker",
                ),
                check_scopes=("executor", "contracts"),
                reconcile="/catalog/" in path,
            )
        elif path.startswith(
            (
                "platform/assembler/",
                "products/",
                "domains/",
                "packages/workflow-compiler/",
                "packages/protocol-contracts/",
            )
        ):
            current = ChangePlan(
                check_scopes=("assembler", "contracts"), reconcile=True
            )
        elif path.startswith("scripts/development/interactive/") or path == (
            "scripts/development/dev.py"
        ):
            current = ChangePlan(
                check_scopes=("development",), controller_restart_required=True
            )
        elif path.startswith("scripts/development/"):
            current = ChangePlan(check_scopes=("development",))
        elif path.startswith("tests/"):
            current = ChangePlan(check_scopes=("development",))
        elif path.startswith("docs/"):
            current = ChangePlan(check_scopes=("documentation",))
        elif path in {
            "package-lock.json",
            "pyproject.toml",
            "system-deps.lock.json",
            "toolchain.lock.json",
            "uv.lock",
        } or path.startswith("environments/"):
            current = ChangePlan(check_scopes=("all",), reconcile=True)
        plan = plan.merge(current)
    return ChangePlan(
        paths=plan.paths,
        hmr=plan.hmr,
        reload_services=plan.reload_services,
        restart_services=plan.restart_services,
        check_scopes=plan.check_scopes,
        reconcile=plan.reconcile,
        controller_restart_required=plan.controller_restart_required,
    )


def build_compose_override(
    root: Path,
    available_services: dict[str, Any],
    *,
    service_ports: dict[str, int] | None = None,
) -> dict[str, object]:
    """Build a development-only override without altering an assembly bundle."""
    ports = {} if service_ports is None else service_ports
    source = str(root.resolve())
    python_path = ":".join(
        f"/workspace/{relative}"
        for relative in (
            "kernel/src",
            "services/data/src",
            "services/knowledge-next/src",
            "services/executor/src",
            "packages/protocol-contracts/src",
            "packages/workflow-compiler/src",
            "packages/operator-pack/src",
            "services/executor/client/src",
        )
    )
    services: dict[str, object] = {}
    for name in _SOURCE_SERVICES:
        if name not in available_services:
            continue
        service: dict[str, object] = {
            "volumes": [
                {
                    "type": "bind",
                    "source": source,
                    "target": "/workspace",
                    "read_only": True,
                }
            ],
            "environment": {
                "PYTHONPATH": python_path,
                "PYTHONDONTWRITEBYTECODE": "1",
            },
            "labels": {"platform.dev.mode": "interactive"},
        }
        if name in _API_COMMANDS:
            service["command"] = list(_API_COMMANDS[name])
        if name in ports:
            service["ports"] = [f"127.0.0.1:{ports[name]}:8000"]
        services[name] = service
    return {"services": services}
