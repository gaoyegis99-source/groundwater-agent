from __future__ import annotations

from collections.abc import Callable, Iterable
from pathlib import Path
import os
import signal
import subprocess
import time
from typing import IO, Any
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, build_opener

from .profile import ChangePlan, DEV_METRIC_SERVICES


class LocalUiProcesses:
    """Own the host-side Vite processes used only by interactive development."""

    def __init__(self, *, root: Path, state_directory: Path) -> None:
        self.root = root.resolve()
        self.state_directory = state_directory.resolve()
        self._processes: dict[str, subprocess.Popen[bytes]] = {}
        self._logs: dict[str, IO[bytes]] = {}
        self._specifications = {
            "web": (
                self.root / "web",
                self.root / "scripts/development/interactive/vite-main.config.ts",
                5173,
                "http://127.0.0.1:5173/",
            ),
            "data-ui": (
                self.root / "services/data/web",
                self.root / "scripts/development/interactive/vite-data.config.ts",
                5174,
                "http://127.0.0.1:5174/data/",
            ),
            "knowledge-ui": (
                self.root / "services/knowledge-next/ui",
                self.root / "scripts/development/interactive/vite-knowledge.config.ts",
                5175,
                "http://127.0.0.1:5175/knowledge/",
            ),
        }

    def start(self) -> None:
        if self._processes:
            return
        vite = self.root / "node_modules/.bin/vite"
        if not vite.is_file():
            raise RuntimeError("DEV_VITE_NOT_MATERIALIZED")
        logs = self.state_directory / "logs"
        logs.mkdir(parents=True, exist_ok=True)
        try:
            for name, (
                working_directory,
                config,
                port,
                _url,
            ) in self._specifications.items():
                if not working_directory.is_dir() or not config.is_file():
                    raise RuntimeError(f"DEV_UI_INPUT_MISSING:{name}")
                log = (logs / f"{name}.log").open("ab", buffering=0)
                process = subprocess.Popen(
                    [
                        str(vite),
                        "--config",
                        str(config),
                        "--host",
                        "127.0.0.1",
                        "--port",
                        str(port),
                        "--strictPort",
                    ],
                    cwd=working_directory,
                    stdin=subprocess.DEVNULL,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                )
                self._logs[name] = log
                self._processes[name] = process
            opener = build_opener(ProxyHandler({}))
            for name, (
                _working_directory,
                _config,
                _port,
                url,
            ) in self._specifications.items():
                deadline = time.monotonic() + 15
                while True:
                    process = self._processes[name]
                    if process.poll() is not None:
                        raise RuntimeError(f"DEV_UI_PROCESS_FAILED:{name}")
                    try:
                        with opener.open(url, timeout=1):
                            break
                    except (HTTPError, URLError, OSError, TimeoutError):
                        if time.monotonic() >= deadline:
                            raise RuntimeError(f"DEV_UI_START_TIMEOUT:{name}")
                        time.sleep(0.1)
        except BaseException:
            self.stop()
            raise

    def status(self) -> dict[str, object]:
        processes: dict[str, object] = {}
        for name, (
            _working_directory,
            _config,
            _port,
            url,
        ) in self._specifications.items():
            process = self._processes.get(name)
            return_code = process.poll() if process is not None else None
            processes[name] = {
                "pid": process.pid if process is not None else None,
                "running": process is not None and return_code is None,
                "returnCode": return_code,
                "url": url,
                "log": str(self.state_directory / "logs" / f"{name}.log"),
            }
        return processes

    def stop(self) -> None:
        processes = tuple(self._processes.values())
        for process in processes:
            if process.poll() is None:
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
        for process in processes:
            if process.poll() is not None:
                continue
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait(timeout=3)
        self._processes.clear()
        for stream in self._logs.values():
            stream.close()
        self._logs.clear()


class CallbackRuntime:
    """Bridge the independent controller to the existing development commands."""

    def __init__(
        self,
        *,
        root: Path,
        state_directory: Path,
        product_id: str,
        prepare_runtime: Callable[[], None],
        reconcile_runtime: Callable[[], None],
        restart_services: Callable[[tuple[str, ...]], None],
        runtime_status: Callable[[], dict[str, object]],
        validate_target: Callable[[str], None],
        ui_enabled: bool,
        validation_enabled: bool,
        service_metrics_target: Callable[[str], str | None] | None = None,
    ) -> None:
        self.root = root.resolve()
        self.state_directory = state_directory.resolve()
        self.product_id = product_id
        self._prepare_runtime = prepare_runtime
        self._reconcile_runtime = reconcile_runtime
        self._restart_services = restart_services
        self._runtime_status = runtime_status
        self._validate_target = validate_target
        self.ui_enabled = ui_enabled
        self.validation_enabled = validation_enabled
        self._service_metrics_target = service_metrics_target
        self._closed = False
        self._ui = LocalUiProcesses(
            root=self.root, state_directory=self.state_directory
        )

    def prepare(self) -> None:
        self._closed = False
        self.state_directory.mkdir(parents=True, exist_ok=True)
        self._prepare_runtime()
        if self.ui_enabled:
            self._ui.start()

    def apply(self, plan: ChangePlan) -> None:
        if plan.reconcile:
            self._reconcile_runtime()
            return
        if plan.restart_services:
            self._restart_services(plan.restart_services)

    def validate(self, scopes: Iterable[str]) -> None:
        if not self.validation_enabled:
            return
        for scope in dict.fromkeys(scopes):
            self._validate_target(scope)

    def status(self) -> dict[str, object]:
        status: dict[str, Any] = dict(self._runtime_status())
        ui_status = self._ui.status() if self.ui_enabled else {}
        if (
            self.ui_enabled
            and not self._closed
            and any(
                not bool(item.get("running"))
                for item in ui_status.values()
                if isinstance(item, dict)
            )
        ):
            status["status"] = "DEGRADED"
            current_issues = status.get("issues")
            issues = list(current_issues) if isinstance(current_issues, list) else []
            issues.append(
                {
                    "code": "DEV_UI_PROCESS_NOT_RUNNING",
                    "summary": "至少一个宿主 Vite 开发进程未运行。",
                }
            )
            status["issues"] = issues
        status.update(
            {
                "productId": self.product_id,
                "reloadMode": "uvicorn-stat-reload",
                "uiEnabled": self.ui_enabled,
                "validationEnabled": self.validation_enabled,
                "ui": ui_status,
            }
        )
        return status

    def service_metrics(self, alias: str) -> str | None:
        if alias not in DEV_METRIC_SERVICES or self._service_metrics_target is None:
            return None
        try:
            return self._service_metrics_target(alias)
        except (OSError, RuntimeError, ValueError):
            return None

    def close(self) -> None:
        self._ui.stop()
        self._closed = True
