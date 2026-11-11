from __future__ import annotations

from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import threading
import time
from typing import Protocol, TextIO

from .events import EventStore
from .profile import ChangePlan, classify_changes
from .server import ObservabilityServer
from .supervisor import IncrementalSupervisor
from .watcher import SourceSnapshot, diff_snapshots


class InteractiveRuntime(Protocol):
    def prepare(self) -> None: ...

    def apply(self, plan: ChangePlan) -> None: ...

    def validate(self, scopes: tuple[str, ...]) -> None: ...

    def status(self) -> dict[str, object]: ...

    def service_metrics(self, alias: str) -> str | None: ...

    def close(self) -> None: ...


def _now() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


class InteractiveController:
    """Watch source and coordinate isolated, observable incremental updates."""

    def __init__(
        self,
        *,
        root: Path,
        state_directory: Path,
        runtime: InteractiveRuntime,
        dashboard_port: int = 28185,
        poll_interval: float = 0.75,
        debounce_seconds: float = 0.35,
        status_refresh_seconds: float = 5,
    ) -> None:
        if poll_interval <= 0 or debounce_seconds < 0:
            raise ValueError("DEV_WATCH_INTERVAL_INVALID")
        self.root = root.resolve()
        self.state_directory = state_directory.resolve()
        self.state_directory.mkdir(parents=True, mode=0o700, exist_ok=True)
        self.state_directory.chmod(0o700)
        self.runtime = runtime
        self.poll_interval = poll_interval
        self.debounce_seconds = debounce_seconds
        self.status_refresh_seconds = status_refresh_seconds
        self.events = EventStore(
            root=self.root,
            log_path=self.state_directory / "events.jsonl",
        )
        self.supervisor = IncrementalSupervisor(adapter=runtime, events=self.events)
        self.server = ObservabilityServer(
            host="127.0.0.1",
            port=dashboard_port,
            events=self.events,
            status=self.status,
            service_metrics=runtime.service_metrics,
        )
        self._phase = "CREATED"
        self._stop = threading.Event()
        self._status_lock = threading.Lock()
        self._cached_runtime: dict[str, object] = {"status": "NOT_READY"}
        self._last_runtime_refresh = 0.0

    @property
    def status_path(self) -> Path:
        return self.state_directory / "status.json"

    def request_stop(self) -> None:
        self._stop.set()

    def _acquire_lock(self) -> TextIO:
        path = self.state_directory / "controller.lock"
        stream = path.open("a+", encoding="utf-8")
        path.chmod(0o600)
        try:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            stream.close()
            raise RuntimeError("DEV_CONTROLLER_ALREADY_RUNNING") from error
        stream.seek(0)
        stream.truncate()
        stream.write(f"{os.getpid()}\n")
        stream.flush()
        os.fsync(stream.fileno())
        return stream

    def _refresh_runtime(self, *, force: bool = False) -> None:
        now = time.monotonic()
        with self._status_lock:
            if (
                not force
                and now - self._last_runtime_refresh < self.status_refresh_seconds
            ):
                return
            try:
                status = self.runtime.status()
            except Exception as error:  # noqa: BLE001 - diagnostic path survives.
                status = {
                    "status": "NOT_READY",
                    "errorType": type(error).__name__,
                }
            self._cached_runtime = status
            self._last_runtime_refresh = now

    def status(self) -> dict[str, object]:
        self._refresh_runtime()
        with self._status_lock:
            runtime = dict(self._cached_runtime)
        return {
            "schemaVersion": "interactive-development-status.v1",
            "mode": "interactive-incremental",
            "phase": self._phase,
            "pid": os.getpid(),
            "updatedAt": _now(),
            "dashboard": f"http://127.0.0.1:{self.server.port}/",
            "runtime": runtime,
            "supervisor": self.supervisor.status(),
            "separation": {
                "controlPlane": "development-only",
                "runtimeImports": "none",
                "writableState": "xdg-only",
            },
        }

    def _write_status(self) -> dict[str, object]:
        payload = self.status()
        self.state_directory.mkdir(parents=True, mode=0o700, exist_ok=True)
        self.state_directory.chmod(0o700)
        temporary = self.status_path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        temporary.chmod(0o600)
        temporary.replace(self.status_path)
        return payload

    def _watch(self) -> None:
        previous = SourceSnapshot.capture(self.root)
        pending: set[str] = set()
        last_change = 0.0
        last_status_write = time.monotonic()
        while not self._stop.wait(self.poll_interval):
            current = SourceSnapshot.capture(self.root, previous=previous)
            changes = diff_snapshots(previous, current)
            previous = current
            if changes:
                paths = tuple(change.path for change in changes)
                pending.update(paths)
                last_change = time.monotonic()
                self.events.publish(
                    "change.detected",
                    "检测到源码变化",
                    paths=paths,
                    details={"count": len(changes)},
                )
            if pending and time.monotonic() - last_change >= self.debounce_seconds:
                plan = classify_changes(pending)
                pending.clear()
                self.events.publish(
                    "change.planned",
                    "已生成隔离增量计划",
                    paths=plan.paths,
                    details=plan.as_dict(),
                )
                if plan.controller_restart_required:
                    self.events.publish(
                        "controller.restart-required",
                        "开发控制器自身已变化，请重启控制器",
                        paths=plan.paths,
                    )
                self.supervisor.submit(plan)
                self._write_status()
                last_status_write = time.monotonic()
            self._refresh_runtime()
            if time.monotonic() - last_status_write >= self.status_refresh_seconds:
                self._write_status()
                last_status_write = time.monotonic()

    def run(self, *, once: bool = False) -> dict[str, object]:
        self.state_directory.mkdir(parents=True, mode=0o700, exist_ok=True)
        self.state_directory.chmod(0o700)
        lock = self._acquire_lock()
        try:
            self._phase = "STARTING"
            self.events.publish("controller.starting", "正在启动交互式增量开发")
            error: BaseException | None = None
            try:
                self.runtime.prepare()
                self.supervisor.start()
                self.server.start()
                self._phase = "READY"
                self._refresh_runtime(force=True)
                self.events.publish(
                    "controller.ready",
                    "交互式增量开发已就绪",
                    details={"dashboardPort": self.server.port},
                )
                self._write_status()
                if not once:
                    self._watch()
            except KeyboardInterrupt:
                self.events.publish("controller.interrupted", "收到终止请求")
            except BaseException as caught:
                error = caught
                self._phase = "FAILED"
                self.events.publish(
                    "controller.failed",
                    "交互式增量开发启动或运行失败",
                    details={"errorType": type(caught).__name__},
                )
            finally:
                self.supervisor.stop()
                self.server.stop()
                self.runtime.close()
                self._phase = "STOPPED" if error is None else "FAILED"
                self._refresh_runtime(force=True)
                self.events.publish("controller.stopped", "交互式增量开发已停止")
                result = self._write_status()
            if error is not None:
                raise error
            return result
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
            lock.close()
