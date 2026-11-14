from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Iterable

from scripts.development.source_mounts import source_mount_python_path
from .watcher import is_container_build_file


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

API_TARGETS = {
    "kernel-service": ("platform_kernel.main", "app"),
    "data-service": ("platform_data_service.main", "app"),
    "knowledge-next-service": ("platform_knowledge_next.api", "create_app"),
    "executor-control": ("platform_executor_service.main", "app"),
}

_PROTOCOL = "packages/protocol-contracts/src"
_DATA_CLIENT = "services/data/client/src"
_EMBEDDING_CLIENT = "packages/embedding-client/src"
_EXECUTOR_WORKERS = (
    "executor-native-worker", "executor-qgis-worker", "executor-remote_http-worker",
)
API_SOURCE_ROOTS = {
    "kernel-service": (
        "kernel/src", _PROTOCOL, "packages/workflow-compiler/src",
        "services/executor/client/src",
    ),
    "data-service": ("services/data/src", _PROTOCOL),
    "knowledge-next-service": (
        "services/knowledge-next/src", _PROTOCOL, _DATA_CLIENT, _EMBEDDING_CLIENT,
    ),
    "executor-control": (
        "services/executor/src", "packages/operator-pack/src", _PROTOCOL, _DATA_CLIENT,
        "packages/workflow-compiler/src",
    ),
}


def _api_command(service: str) -> list[str]:
    return [
        "uvicorn", "scripts.development.interactive.generation:create_app",
        "--factory", "--app-dir", "/workspace", "--host", "0.0.0.0", "--port", "8000",
        *(
            value
            for directory in API_SOURCE_ROOTS[service]
            for value in ("--reload-dir", f"/workspace/{directory}")
        ),
        "--reload",
    ]


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


def affected_apis(paths: Iterable[str]) -> tuple[str, ...]:
    python_paths = tuple(path for path in paths if path.endswith(".py"))
    return tuple(
        service
        for service, directories in API_SOURCE_ROOTS.items()
        if any(path.startswith(directory + "/")
               for directory in directories for path in python_paths)
    )


def classify_changes(paths: Iterable[str]) -> ChangePlan:
    plan = ChangePlan(paths=_ordered(_safe_relative(path) for path in paths))
    for path in plan.paths:
        current = ChangePlan()
        filename = PurePosixPath(path).name
        if (
            is_container_build_file(filename)
            or filename in {"pyproject.toml", "package.json"}
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
            current = ChangePlan(check_scopes=("kernel",))
        elif path.startswith(_DATA_CLIENT + "/"):
            current = ChangePlan(
                restart_services=("knowledge-next-worker", *_EXECUTOR_WORKERS),
                check_scopes=("data", "knowledge", "executor"),
            )
        elif path.startswith(_EMBEDDING_CLIENT + "/"):
            current = ChangePlan(
                restart_services=("knowledge-next-worker",),
                check_scopes=("knowledge", "contracts"),
            )
        elif path.startswith("services/data/"):
            api_only = "/api/" in path or path.endswith("/main.py")
            current = ChangePlan(
                restart_services=(
                    ("data-worker",)
                    if path.startswith("services/data/src/") and not api_only
                    else ()
                ),
                check_scopes=("data",),
            )
        elif path.startswith("services/knowledge-next/"):
            current = ChangePlan(
                restart_services=(
                    ("knowledge-next-worker",)
                    if path.startswith("services/knowledge-next/src/")
                    else ()
                ),
                check_scopes=("knowledge",),
            )
        elif path.startswith("services/executor/"):
            current = ChangePlan(
                restart_services=(
                    _EXECUTOR_WORKERS if path.startswith("services/executor/src/") else ()
                ),
                check_scopes=(
                    ("executor", "kernel")
                    if path.startswith("services/executor/client/src/")
                    else ("executor",)
                ),
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
        reload_services=affected_apis(plan.paths),
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
            _DATA_CLIENT,
            _EMBEDDING_CLIENT,
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
                "PYTHONPATH": source_mount_python_path(name, python_path),
                "PYTHONDONTWRITEBYTECODE": "1",
                "PYTHONPYCACHEPREFIX": "/tmp/platform-dev-pycache",
            },
            "labels": {"platform.dev.mode": "interactive"},
        }
        if name in API_TARGETS:
            service["command"] = _api_command(name)
            service["environment"]["PLATFORM_DEV_API_SERVICE"] = name
        if name in ports:
            service["ports"] = [f"127.0.0.1:{ports[name]}:8000"]
        services[name] = service
    return {"services": services}
