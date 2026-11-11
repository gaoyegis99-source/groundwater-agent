from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
from platform_protocol_contracts import (
    activate_trace_context,
    reset_trace_context,
    trace_context_for_id,
)

from ..contracts import JobRequestV6
from ..database import duration_ms, utc_now
from ..application.workflow_api import dispatch_confirmation_current

from .persistence.status import save_cancelled
from .persistence.queue import get_target_digest, service_target_digest
from ..settings import ExecutableModelDefinition, ServiceBinding
from .execution_client import ExecutionClient, ExecutionClientError, ExecutionRejectedError
from ..application.query_dtos import ConfirmedAction
from .models import DispatchWork, WorkflowRun, WorkflowTickResult
from .persistence import (
    claim_dispatch_batch,
    enqueue_batch,
    get_run,
    recover_dispatches,
    recovery_source,
    release_communication_error,
    release_pre_submit_block,
    runs_for_agent,
    save_job_status,
    save_timeout,
    save_rejection,
)


DISPATCH_BATCH_SIZE = 32


class WorkflowExecutionOwner:
    def __init__(
        self,
        database: Path,
        models: tuple[ExecutableModelDefinition, ...],
        services: tuple[ServiceBinding, ...],
        execution_client: ExecutionClient,
        controls=None,
        expected_definition_set_digest: str | None = None,
        dispatch_concurrency: int = 8,
        trace_id_resolver: Callable[[str], str | None] | None = None,
    ) -> None:
        if dispatch_concurrency < 1 or dispatch_concurrency > 64:
            raise ValueError("WORKFLOW_DISPATCH_CONCURRENCY_INVALID")
        self.database = database
        self.models = {item.model_id: item for item in models}
        self.services = {item.name: item for item in services}
        self.execution_client = execution_client
        self.controls = controls
        self.expected_definition_set_digest = expected_definition_set_digest
        self.dispatch_concurrency = dispatch_concurrency
        self.trace_id_resolver = trace_id_resolver

    def enqueue_confirmed_batch(
        self,
        connection: sqlite3.Connection,
        *,
        agent_run_id: str,
        session_id: str,
        project_id: str,
        confirmation_id: str | None = None,
        actions: tuple[ConfirmedAction, ...],
    ) -> tuple[WorkflowRun, ...]:
        return enqueue_batch(
            connection,
            agent_run_id=agent_run_id,
            session_id=session_id,
            project_id=project_id,
            confirmation_id=confirmation_id,
            actions=actions,
            models=self.models,
            services=self.services,
        )

    def get(self, project_id: str, run_id: str) -> WorkflowRun:
        return get_run(self.database, project_id, run_id)

    def for_agent(self, agent_run_id: str) -> tuple[WorkflowRun, ...]:
        return runs_for_agent(self.database, agent_run_id)

    def recover_interrupted(self) -> None:
        recover_dispatches(self.database)

    def recovery_source(self, project_id: str, session_id: str, run_id: str):
        return recovery_source(self.database, project_id, session_id, run_id)

    async def tick(self) -> AsyncIterator[WorkflowTickResult]:
        work = claim_dispatch_batch(self.database, DISPATCH_BATCH_SIZE, wait=False)
        semaphore = asyncio.Semaphore(self.dispatch_concurrency)

        async def drive(item: DispatchWork) -> WorkflowTickResult:
            async with semaphore:
                terminal = await self._drive(item)
                return WorkflowTickResult(
                    1, (item.run.agent_run_id,) if terminal else ()
                )

        async with asyncio.TaskGroup() as tasks:
            pending = [tasks.create_task(drive(item)) for item in work]
            try:
                for completed in asyncio.as_completed(pending):
                    yield await completed
            except GeneratorExit:
                # Iterator closure is not a worker error. TaskGroup drains the
                # cancelled I/O; durable dispatch claims remain for recovery.
                for task in pending:
                    if not task.done():
                        task.cancel()
                return

    async def _drive(self, work: DispatchWork) -> bool:
        trace_id = (
            None
            if self.trace_id_resolver is None
            else self.trace_id_resolver(work.run.agent_run_id)
        )
        context = trace_context_for_id(
            trace_id or work.run.agent_run_id
        )
        token = activate_trace_context(context)
        try:
            return await self._drive_with_context(work)
        finally:
            reset_trace_context(token)

    async def _drive_with_context(self, work: DispatchWork) -> bool:
        stopping = self.controls is not None and self.controls.requested(work.run.agent_run_id)
        if stopping and work.state == "REQUESTED":
            save_cancelled(self.database, work.run)
            return True
        if work.state == "REQUESTED" and _expired(work.deadline_at):
            save_timeout(self.database, work.run)
            return True
        service = self.services.get(work.run.execution_service)
        if service is None:
            # A restarted worker without the original service binding cannot
            # submit or reconcile this run. Keep the claim recoverable until a
            # compatible worker is available instead of raising KeyError.
            if work.state == "REQUESTED":
                release_pre_submit_block(
                    self.database, work.run.run_id,
                    "WORKFLOW_DEFINITION_UNAVAILABLE",
                )
            else:
                release_communication_error(
                    self.database, work.run.run_id,
                    "WORKFLOW_DEFINITION_UNAVAILABLE",
                )
            return False
        target_digest = get_target_digest(self.database, work.run.run_id)
        if target_digest is None or (
            target_digest != "legacy-unbound"
            and target_digest != service_target_digest(service)
        ):
            # A service name is not an endpoint identity. Never query, submit,
            # poll or cancel at a different URL from the one frozen at enqueue.
            release = (
                release_pre_submit_block
                if work.state == "REQUESTED"
                else release_communication_error
            )
            release(
                self.database, work.run.run_id,
                _reconciliation_alert(work) or "WORKFLOW_EXECUTION_TARGET_MISMATCH",
            )
            return False
        try:
            status = None
            if work.state == "SUBMIT_UNKNOWN":
                expired_before_lookup = _expired(work.deadline_at)
                status = await self.execution_client.get_by_request(
                    service, work.run.project_id, work.run.run_id
                )
                if status is None and _expired(work.deadline_at):
                    # A pre-deadline 404 can arrive after an in-flight submit
                    # commits. Only a lookup begun after the deadline closes
                    # that creation window.
                    if not expired_before_lookup:
                        status = await self.execution_client.get_by_request(
                            service, work.run.project_id, work.run.run_id
                        )
                    if status is None:
                        if self.controls is not None and self.controls.requested(
                            work.run.agent_run_id
                        ):
                            save_cancelled(self.database, work.run)
                        else:
                            save_timeout(self.database, work.run)
                        return True
            if work.state in {"REQUESTED", "SUBMIT_UNKNOWN"} and status is None:
                # A 404 only says no Job was visible at lookup time. Every
                # path that can issue a new submit must recheck live gates;
                # the first REQUESTED check cannot authorize a later retry.
                stopping = self.controls is not None and self.controls.requested(
                    work.run.agent_run_id
                )
                if stopping:
                    if work.state == "REQUESTED":
                        save_cancelled(self.database, work.run)
                        return True
                    release_communication_error(
                        self.database, work.run.run_id,
                        _reconciliation_alert(work),
                    )
                    return False
                if _expired(work.deadline_at):
                    if work.state == "REQUESTED":
                        save_timeout(self.database, work.run)
                        return True
                    status = await self.execution_client.get_by_request(
                        service, work.run.project_id, work.run.run_id
                    )
                    if status is None:
                        save_timeout(self.database, work.run)
                        return True
                if status is None and self.expected_definition_set_digest is not None:
                    current_digest = await self.execution_client.get_definition_set_digest(
                        service, work.run.project_id
                    )
                    if current_digest != self.expected_definition_set_digest:
                        if work.state == "REQUESTED":
                            release_pre_submit_block(
                                self.database, work.run.run_id,
                                "EXECUTOR_DEFINITION_SET_MISMATCH",
                            )
                        else:
                            release_communication_error(
                                self.database, work.run.run_id,
                                "EXECUTOR_DEFINITION_SET_MISMATCH",
                            )
                        return False
                if status is None and not _matches_frozen_model(
                    self.models.get(work.run.model_id), work.run
                ):
                    # A process restarted with a different manifest must not
                    # create a new remote Job from an old queued run. A known
                    # remote Job is still reconciled above/below this gate.
                    if work.state == "REQUESTED":
                        release_pre_submit_block(
                            self.database, work.run.run_id,
                            "WORKFLOW_DEFINITION_UNAVAILABLE",
                        )
                    else:
                        release_communication_error(
                            self.database, work.run.run_id,
                            "WORKFLOW_DEFINITION_UNAVAILABLE",
                        )
                    return False
                if status is None and not dispatch_confirmation_current(
                    self.database, work.run
                ):
                    # An earlier confirmation cannot authorize a new remote
                    # action after its approval or task binding changes.
                    if work.state == "REQUESTED":
                        release_pre_submit_block(
                            self.database, work.run.run_id,
                            "WORKFLOW_CONFIRMATION_NOT_CURRENT",
                        )
                    else:
                        release_communication_error(
                            self.database, work.run.run_id,
                            _reconciliation_alert(work)
                            or "WORKFLOW_CONFIRMATION_NOT_CURRENT",
                        )
                    return False
                # Digest lookup is I/O too: cancellation or the deadline may
                # change while it is in flight. Recheck at the final boundary.
                if status is None and self.controls is not None and self.controls.requested(
                    work.run.agent_run_id
                ):
                    if work.state == "REQUESTED":
                        save_cancelled(self.database, work.run)
                        return True
                    release_communication_error(
                        self.database, work.run.run_id,
                        _reconciliation_alert(work),
                    )
                    return False
                if status is None and _expired(work.deadline_at):
                    if work.state == "REQUESTED":
                        save_timeout(self.database, work.run)
                        return True
                    status = await self.execution_client.get_by_request(
                        service, work.run.project_id, work.run.run_id
                    )
                    if status is None:
                        save_timeout(self.database, work.run)
                        return True
                if status is None:
                    status = await self.execution_client.submit(
                        service,
                        JobRequestV6(
                            request_id=work.run.run_id,
                            project_id=work.run.project_id,
                            model_id=work.run.model_id,
                            model_version=work.run.model_version,
                            model_checksum=work.run.model_checksum,
                            workflow_id=work.run.workflow_id,
                            workflow_version=work.run.workflow_version,
                            workflow_checksum=work.run.workflow_checksum,
                            executor_id=work.run.executor_id,
                            arguments=work.run.arguments,
                            input_references=work.run.input_references,
                            deadline_at=work.deadline_at,
                        ),
                    )
            elif work.state == "SUBMITTED":
                status = await self.execution_client.get(
                    service, work.run.remote_job_id
                )
            if status.request_id != work.run.run_id:
                raise ExecutionClientError("EXECUTION_JOB_REQUEST_MISMATCH")
            if self.controls is not None and self.controls.requested(work.run.agent_run_id) and status.status in {"QUEUED", "RUNNING"}:
                await self.execution_client.cancel(service, status.job_id)
                status = await self.execution_client.get(service, status.job_id)
                if status.request_id != work.run.run_id:
                    raise ExecutionClientError("EXECUTION_JOB_REQUEST_MISMATCH")
            return save_job_status(self.database, work, status)
        except ExecutionRejectedError as error:
            if str(error) == "JOB_DEADLINE_EXCEEDED":
                save_timeout(self.database, work.run)
            else:
                save_rejection(self.database, work.run, str(error))
            return True
        except ExecutionClientError as error:
            release_communication_error(
                self.database, work.run.run_id,
                _reconciliation_alert(work) or f"EXECUTION_SERVICE_UNAVAILABLE:{error}",
            )
            return False


def _expired(deadline_at: str) -> bool:
    deadline = datetime.fromisoformat(deadline_at.replace("Z", "+00:00"))
    return datetime.now(timezone.utc) >= deadline


def _matches_frozen_model(
    model: ExecutableModelDefinition | None, run: WorkflowRun
) -> bool:
    return model is not None and (
        model.model_id,
        model.model_version,
        model.model_checksum,
        model.workflow_id,
        model.workflow_version,
        model.workflow_checksum,
        model.execution_service,
        model.executor_id,
    ) == (
        run.model_id,
        run.model_version,
        run.model_checksum,
        run.workflow_id,
        run.workflow_version,
        run.workflow_checksum,
        run.execution_service,
        run.executor_id,
    )


def _reconciliation_alert(work: DispatchWork) -> str | None:
    if work.state not in {"SUBMIT_UNKNOWN", "SUBMITTED"}:
        return None
    if _expired(work.deadline_at) or duration_ms(work.run.created_at, utc_now()) >= 300_000:
        return "RECONCILIATION_REQUIRED"
    return None
