from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import subprocess
import sys

from .controller import InteractiveController
from .profile import DEV_METRIC_SERVICES, build_compose_override
from .runtime import CallbackRuntime


@dataclass(frozen=True)
class InteractiveHooks:
    ensure_dependencies: Callable[[], object]
    compile_product: Callable[[], Path]
    run_compose: Callable[
        [list[str], tuple[Path, ...], bool], subprocess.CompletedProcess[str]
    ]
    runtime_status: Callable[[], dict[str, object]]
    record_bootstrap: Callable[[subprocess.CompletedProcess[str]], object]


class InteractiveCommand:
    """Compose existing dev operations into an isolated incremental runtime."""

    def __init__(
        self,
        *,
        root: Path,
        state_directory: Path,
        product_id: str,
        hooks: InteractiveHooks,
    ) -> None:
        self.root = root.resolve()
        self.state_directory = state_directory.resolve()
        if self.state_directory.is_relative_to(self.root):
            raise ValueError("DEV_STATE_MUST_BE_OUTSIDE_SOURCE")
        self.product_id = product_id
        self.hooks = hooks

    @property
    def override_path(self) -> Path:
        return self.state_directory / "compose.interactive.json"

    def _write_override(self, output: Path) -> set[str]:
        try:
            compose = json.loads((output / "compose.json").read_text(encoding="utf-8"))
            available = compose["services"]
        except (OSError, KeyError, TypeError, json.JSONDecodeError) as error:
            raise RuntimeError("DEV_ASSEMBLY_COMPOSE_INVALID") from error
        if not isinstance(available, dict):
            raise RuntimeError("DEV_ASSEMBLY_COMPOSE_INVALID")
        payload = build_compose_override(self.root, available)
        self.state_directory.mkdir(parents=True, mode=0o700, exist_ok=True)
        self.state_directory.chmod(0o700)
        temporary = self.override_path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        temporary.chmod(0o600)
        temporary.replace(self.override_path)
        return set(available)

    def _prepare(self) -> None:
        self.hooks.ensure_dependencies()
        output = self.hooks.compile_product()
        available = self._write_override(output)
        extra = (self.override_path,)
        self.hooks.run_compose(["config", "--quiet"], extra, True)
        self.hooks.run_compose(
            ["up", "--build", "--pull", "missing", "-d", "--wait"],
            extra,
            True,
        )
        if "knowledge-bootstrap" in available:
            completed = self.hooks.run_compose(
                ["run", "--rm", "--no-deps", "knowledge-bootstrap"],
                extra,
                False,
            )
            self.hooks.record_bootstrap(completed)
            if completed.returncode:
                raise RuntimeError("DEV_KNOWLEDGE_BOOTSTRAP_FAILED")

    def _restart(self, requested: tuple[str, ...]) -> None:
        try:
            payload = json.loads(self.override_path.read_text(encoding="utf-8"))
            available = payload["services"]
        except (OSError, KeyError, TypeError, json.JSONDecodeError) as error:
            raise RuntimeError("DEV_INTERACTIVE_OVERRIDE_NOT_READY") from error
        if not isinstance(available, dict):
            raise RuntimeError("DEV_INTERACTIVE_OVERRIDE_NOT_READY")
        services = tuple(name for name in requested if name in available)
        if services:
            self.hooks.run_compose(["restart", *services], (self.override_path,), True)

    def _validation_commands(self, target: str) -> tuple[tuple[str, ...], ...]:
        if target in {
            "kernel",
            "data",
            "knowledge",
            "executor",
            "assembler",
            "contracts",
            "web",
            "integration",
            "all",
        }:
            return (
                (
                    sys.executable,
                    "scripts/development/dev.py",
                    "check",
                    "--scope",
                    target,
                ),
            )
        if target in {"data-ui", "knowledge-ui"}:
            workspace = (
                "services/data/web"
                if target == "data-ui"
                else "services/knowledge-next/ui"
            )
            return tuple(
                ("npm", "--prefix", workspace, "run", action)
                for action in ("test", "typecheck")
            )
        if target == "development":
            return (
                (
                    sys.executable,
                    "-m",
                    "pytest",
                    "-q",
                    "tests/cli/test_interactive_dev.py",
                    "tests/architecture/test_interactive_development_boundary.py",
                ),
            )
        if target == "documentation":
            return ((sys.executable, "-m", "scripts.checks.check_documentation"),)
        raise RuntimeError(f"DEV_VALIDATION_TARGET_UNKNOWN:{target}")

    def _validate(self, target: str) -> None:
        if re.fullmatch(r"[a-z][a-z0-9-]{0,31}", target) is None:
            raise RuntimeError("DEV_VALIDATION_TARGET_INVALID")
        logs = self.state_directory / "logs"
        logs.mkdir(parents=True, exist_ok=True)
        path = logs / f"validation-{target}.log"
        environment = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
        with path.open("wb") as stream:
            for command in self._validation_commands(target):
                stream.write((json.dumps(command) + "\n").encode())
                stream.flush()
                completed = subprocess.run(
                    command,
                    cwd=self.root,
                    env=environment,
                    stdin=subprocess.DEVNULL,
                    stdout=stream,
                    stderr=subprocess.STDOUT,
                    check=False,
                )
                if completed.returncode:
                    raise RuntimeError(
                        f"DEV_VALIDATION_FAILED:{target}:{completed.returncode}"
                    )

    def _service_metrics(self, alias: str) -> str | None:
        service = DEV_METRIC_SERVICES.get(alias)
        if service is None or not self.override_path.is_file():
            return None
        probe = (
            "import urllib.request;"
            "print(urllib.request.urlopen("
            "'http://127.0.0.1:8000/internal/metrics',timeout=2"
            ").read().decode(),end='')"
        )
        try:
            completed = self.hooks.run_compose(
                ["exec", "-T", service, "python", "-c", probe],
                (self.override_path,),
                False,
            )
        except (OSError, RuntimeError, ValueError):
            return None
        return completed.stdout if completed.returncode == 0 else None

    def runtime(self, *, ui_enabled: bool, validation_enabled: bool) -> CallbackRuntime:
        return CallbackRuntime(
            root=self.root,
            state_directory=self.state_directory,
            product_id=self.product_id,
            prepare_runtime=self._prepare,
            reconcile_runtime=self._prepare,
            restart_services=self._restart,
            runtime_status=self.hooks.runtime_status,
            validate_target=self._validate,
            ui_enabled=ui_enabled,
            validation_enabled=validation_enabled,
            service_metrics_target=self._service_metrics,
        )

    def run(
        self,
        *,
        dashboard_port: int,
        poll_interval: float,
        debounce_seconds: float,
        ui_enabled: bool,
        validation_enabled: bool,
        once: bool,
    ) -> dict[str, object]:
        controller = InteractiveController(
            root=self.root,
            state_directory=self.state_directory,
            runtime=self.runtime(
                ui_enabled=ui_enabled,
                validation_enabled=validation_enabled,
            ),
            dashboard_port=dashboard_port,
            poll_interval=poll_interval,
            debounce_seconds=debounce_seconds,
        )
        return controller.run(once=once)

    def describe(self) -> dict[str, object]:
        return {
            "status": "DRY_RUN",
            "mode": "interactive-incremental",
            "productId": self.product_id,
            "composeOverride": str(self.override_path),
            "backendAccess": "compose-internal-via-gateway",
            "ui": {
                "web": "http://127.0.0.1:5173/",
                "data-ui": "http://127.0.0.1:5174/data/",
                "knowledge-ui": "http://127.0.0.1:5175/knowledge/",
            },
            "separation": {
                "sourceMount": "read-only",
                "generatedFiles": "xdg-state-only",
                "runtimeImports": "none",
            },
        }


def read_interactive_status(state_directory: Path) -> dict[str, object]:
    path = state_directory / "status.json"
    if not path.is_file():
        return {"status": "NOT_RUNNING", "mode": "interactive-incremental"}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"status": "INVALID", "mode": "interactive-incremental"}
    if not isinstance(payload, dict):
        return {"status": "INVALID", "mode": "interactive-incremental"}
    return payload
