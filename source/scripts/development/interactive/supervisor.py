from __future__ import annotations

import threading
import time
from typing import Any, Protocol

from .events import EventStore
from .profile import ChangePlan


class IncrementalAdapter(Protocol):
    def apply(self, plan: ChangePlan) -> None: ...

    def validate(self, scopes: tuple[str, ...]) -> None: ...


class IncrementalSupervisor:
    def __init__(
        self,
        adapter: IncrementalAdapter,
        *,
        events: EventStore | None = None,
    ) -> None:
        self.adapter = adapter
        self.events = events
        self._condition = threading.Condition()
        self._pending: ChangePlan | None = None
        self._active = False
        self._stopping = False
        self._thread: threading.Thread | None = None
        self._submitted = 0
        self._completed = 0
        self._failed = 0
        self._superseded = 0
        self._last_error: str | None = None
        self._last_plan: dict[str, object] | None = None

    def start(self) -> None:
        with self._condition:
            if self._thread is not None:
                return
            self._thread = threading.Thread(
                target=self._run, name="incremental-supervisor", daemon=True
            )
            self._thread.start()

    def submit(self, plan: ChangePlan) -> None:
        if not plan.paths:
            return
        with self._condition:
            if self._stopping:
                raise RuntimeError("DEV_SUPERVISOR_STOPPED")
            self._submitted += 1
            if self._pending is None:
                self._pending = plan
            else:
                self._pending = self._pending.merge(plan)
                self._superseded += 1
            self._condition.notify_all()

    def _publish(
        self,
        kind: str,
        message: str,
        plan: ChangePlan,
        **details: Any,
    ) -> None:
        if self.events is not None:
            self.events.publish(
                kind, message, paths=plan.paths, details=details or None
            )

    def _run(self) -> None:
        while True:
            with self._condition:
                while self._pending is None and not self._stopping:
                    self._condition.wait()
                if self._stopping and self._pending is None:
                    return
                plan = self._pending
                self._pending = None
                self._active = True
            assert plan is not None
            started = time.monotonic()
            self._publish("increment.started", "开始应用增量变更", plan)
            try:
                self.adapter.apply(plan)
                if plan.check_scopes:
                    self.adapter.validate(plan.check_scopes)
            except Exception as error:  # noqa: BLE001 - control plane must survive.
                with self._condition:
                    self._failed += 1
                    self._last_error = f"{type(error).__name__}:{error}"[:500]
                self._publish(
                    "increment.failed",
                    "增量应用或验证失败",
                    plan,
                    errorType=type(error).__name__,
                )
            else:
                with self._condition:
                    self._completed += 1
                    self._last_error = None
                self._publish(
                    "increment.completed",
                    "增量变更已应用并验证",
                    plan,
                    durationMs=round((time.monotonic() - started) * 1000),
                )
            finally:
                with self._condition:
                    self._last_plan = plan.as_dict()
                    self._active = False
                    self._condition.notify_all()

    def wait_idle(self, timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        with self._condition:
            while self._active or self._pending is not None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._condition.wait(remaining)
            return True

    def status(self) -> dict[str, object]:
        with self._condition:
            return {
                "active": self._active,
                "pending": self._pending.as_dict() if self._pending else None,
                "submittedBatches": self._submitted,
                "completedBatches": self._completed,
                "failedBatches": self._failed,
                "supersededBatches": self._superseded,
                "lastError": self._last_error,
                "lastPlan": self._last_plan,
            }

    def stop(self, timeout: float = 5) -> None:
        with self._condition:
            self._stopping = True
            self._condition.notify_all()
            thread = self._thread
        if thread is not None:
            thread.join(timeout)
