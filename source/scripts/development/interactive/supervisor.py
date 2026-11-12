from __future__ import annotations

from dataclasses import replace
import re
import threading
import time
import uuid
from typing import Any, Protocol

from .events import EventStore
from .profile import ChangePlan


class IncrementalAdapter(Protocol):
    def apply(self, plan: ChangePlan) -> dict[str, object] | None: ...

    def validate(self, scopes: tuple[str, ...]) -> tuple[str, ...] | None: ...


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
        self._batch: dict[str, object] | None = None
        self._failed_apply: ChangePlan | None = None
        self._failed_validation: tuple[str, ...] = ()

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
                assert plan is not None
                self._pending = None
                if self._failed_apply is not None:
                    plan = self._failed_apply.merge(plan)
                if self._failed_validation:
                    plan = replace(plan, check_scopes=tuple(sorted({
                        *plan.check_scopes, *self._failed_validation,
                    })))
                self._failed_apply = None
                self._failed_validation = ()
                self._active = True
                batch_id = uuid.uuid4().hex
                self._batch = {"id": batch_id, "phase": "APPLYING"}
            started = time.monotonic()
            self._publish("increment.started", "开始应用增量变更", plan, batchId=batch_id)
            stage = "apply"
            try:
                observed = self.adapter.apply(plan) or {}
                with self._condition:
                    self._batch = {
                        "id": batch_id, "phase": "VALIDATING", "runtimeUpdate": observed
                    }
                generations = observed.get("apiGenerations")
                if isinstance(generations, dict):
                    for service, proof in generations.items():
                        self._publish(
                            "runtime.api-observed",
                            "API 进程已报告当前源码摘要",
                            plan,
                            batchId=batch_id,
                            service=service,
                            sourceDigest=proof["sourceDigest"],
                            generation=proof.get("generation"),
                        )
                self._publish(
                    "increment.applied", "运行时更新阶段结束", plan, batchId=batch_id
                )
                stage = "validation"
                validated = self.adapter.validate(plan.check_scopes) if plan.check_scopes else ()
            except Exception as error:  # noqa: BLE001 - control plane must survive.
                message = str(error)
                code = (
                    message
                    if re.fullmatch(r"DEV_[A-Z_]+(?::[a-zA-Z0-9_,:-]+)?", message)
                    else type(error).__name__
                )
                with self._condition:
                    self._failed += 1
                    self._last_error = code
                    if stage == "apply":
                        self._failed_apply = plan
                    else:
                        self._failed_validation = plan.check_scopes
                    self._batch = {
                        **(self._batch or {}), "phase": "FAILED", "failedStage": stage,
                        "errorCode": code,
                    }
                self._publish(
                    "increment.failed",
                    "增量应用或验证失败",
                    plan,
                    batchId=batch_id,
                    stage=stage,
                    errorCode=code,
                    errorType=type(error).__name__,
                )
            else:
                with self._condition:
                    self._completed += 1
                    self._last_error = None
                    self._batch = {
                        **(self._batch or {}), "phase": "COMPLETED",
                        "validatedScopes": list(validated or ()),
                    }
                self._publish(
                    "increment.completed",
                    "增量批次已结束",
                    plan,
                    batchId=batch_id,
                    validatedScopes=list(validated or ()),
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
                "retryApply": self._failed_apply.as_dict() if self._failed_apply else None,
                "retryValidationScopes": list(self._failed_validation),
                "lastPlan": self._last_plan,
                "latestBatch": self._batch,
            }

    def stop(self) -> None:
        with self._condition:
            self._stopping = True
            self._condition.notify_all()
            thread = self._thread
        if thread is not None:
            # Drain accepted work so each validation can release its isolated database.
            thread.join()
