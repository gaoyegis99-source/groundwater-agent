from __future__ import annotations

import asyncio
from collections import deque
from contextlib import aclosing
from datetime import datetime, timezone
import logging

from .agent_runtime.case_resolver import (
    matches_execution_case_request,
    matches_explicit_model_tool_request,
)
from .agent_runtime.owner import AgentRuntimeOwner
from .agent_runtime.persistence.context import load_route_context_sources
from .change_signal import CommitSignal
from .errors import (
    AgentRunError, SchedulerStoreReadonlyError, UpstreamServiceError,
    WriteLockUnavailableError,
)
from .interaction.control_intents import rule_intent
from .interaction.owner import InteractionOwner
from .intent_routing import (
    IntentDecisionV4, IntentRouter, IntentRoutingError, IntentRoutingResult,
    IntentRoutingUsage,
)
from .workflow_execution.owner import WorkflowExecutionOwner
from .task_supervisor import KernelTaskSupervisor


IDLE_WAIT_SECONDS = 1.0
TRACE_PRUNE_INTERVAL_SECONDS = 3600.0
_LOGGER = logging.getLogger(__name__)


class KernelRuntime:
    def __init__(
        self,
        interaction: InteractionOwner,
        agent_runtime: AgentRuntimeOwner,
        workflow_execution: WorkflowExecutionOwner,
        intent_router: IntentRouter,
        commit_signal: CommitSignal | None = None,
        controls=None,
        *,
        task_concurrency: int,
        task_queue_capacity: int = 100,
        shutdown_grace_seconds: float = 30,
    ) -> None:
        self.controls = controls
        self.interaction = interaction
        self.agent_runtime = agent_runtime
        self.workflow_execution = workflow_execution
        self.intent_router = intent_router
        self.commit_signal = commit_signal or CommitSignal()
        self._stop = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._store_readonly = False
        self._tasks = KernelTaskSupervisor(task_concurrency, task_queue_capacity)
        self._poll_routes_first = True
        self._next_admission = "database"
        self._shutdown_grace_seconds = shutdown_grace_seconds
        self._next_trace_prune_at = 0.0
        self._claimed_controls: deque[dict] = deque()

    async def start(self) -> None:
        await self.recover()
        self._task = asyncio.create_task(self._dispatch(), name="kernel:dispatcher")
        self._task.add_done_callback(self._log_dispatcher_failure)

    @staticmethod
    def _log_dispatcher_failure(task: asyncio.Task[None]) -> None:
        if not task.cancelled() and (error := task.exception()) is not None:
            _LOGGER.error("Kernel dispatcher stopped unexpectedly", exc_info=error)

    async def recover(self) -> None:
        self.workflow_execution.recover_interrupted()
        await self.interaction.recover_control_proposals()
        await self._process_controls()
        self.agent_runtime.recover_interrupted()
        starts = self.interaction.prepare_recovery()
        self._prune_traces()
        for session_id in starts:
            self._schedule_start(session_id)
        self._schedule_fair_work()
        for run in self.agent_runtime.execution_waiting_runs():
            if self.agent_runtime.reconcile_execution(run.agent_run_id):
                self._schedule_resume(run.agent_run_id)

    def is_ready(self) -> bool:
        return (
            self._task is not None
            and not self._task.done()
            and not self._store_readonly
            and not self._agent_task_failed()
        )

    def metric_counts(self) -> dict[str, int]:
        return self._tasks.counts()

    def task_metrics(self) -> dict[str, dict[str, int]]:
        return self._tasks.metrics()

    async def stop(self) -> None:
        self._stop.set()
        try:
            if self._task is not None:
                await self._task
        finally:
            await self._wait_for_agent_tasks()

    async def _dispatch(self) -> None:
        while not self._stop.is_set():
            generation = self.commit_signal.generation
            processed_count = 0
            try:
                if self._store_readonly:
                    self._check_scheduler_store_writable()
                await self._process_controls()
                self._tasks.reap()
                if asyncio.get_running_loop().time() >= self._next_trace_prune_at:
                    self._prune_traces()
                self._schedule_fair_work()
                async with aclosing(self.workflow_execution.tick()) as results:
                    async for result in results:
                        processed_count += result.processed_count
                        for agent_run_id in result.ready_agent_run_ids:
                            if (
                                not self._tasks.contains("resume", agent_run_id)
                                and self.agent_runtime.reconcile_execution(agent_run_id)
                            ):
                                self._schedule_resume(agent_run_id)
                            elif not self._tasks.contains("resume", agent_run_id):
                                recovered = self.agent_runtime.finalize_recovery(
                                    agent_run_id
                                )
                                if recovered is not None:
                                    self.interaction.project(recovered)
            except SchedulerStoreReadonlyError:
                if not self._store_readonly:
                    _LOGGER.warning("Kernel scheduler write store is read-only; waiting for recovery")
                self._store_readonly = True
                processed_count = 0
            except WriteLockUnavailableError:
                # Only a refused BEGIN reaches here. Leave durable queue state
                # intact and yield before another poll; never replay a write.
                pass
            if processed_count:
                continue
            stop = asyncio.create_task(self._stop.wait())
            changed = asyncio.create_task(
                self.commit_signal.wait(generation, IDLE_WAIT_SECONDS)
            )
            done, pending = await asyncio.wait(
                (stop, changed), return_when=asyncio.FIRST_COMPLETED
            )
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
            for task in done:
                task.result()

    def _check_scheduler_store_writable(self) -> None:
        if not self.interaction.scheduler_store_writable():
            raise WriteLockUnavailableError("KERNEL_WRITE_BUSY")
        self._store_readonly = False
        _LOGGER.info("Kernel scheduler write store recovered")

    async def _process_controls(self) -> None:
        if self.controls is None:
            return
        if not self._claimed_controls:
            self._claimed_controls.extend(self.controls.claim(limit=32))
        while self._claimed_controls:
            control = self._claimed_controls[0]
            target = control["agent_run_id"]
            deciding = self.controls.deciding(target)
            finalized = self.controls.finalize(control)
            self._claimed_controls.popleft()
            if finalized is None:
                continue
            if deciding:
                await self._tasks.cancel("decision", "singleton")
            await self._tasks.cancel("route", target)
            await self._tasks.cancel("resume", target)
            await self._tasks.cancel("start", control["session_id"])

    def _schedule_decision(self) -> bool:
        if not self.interaction.has_pending_decision():
            return False
        return self._tasks.queue("decision", "singleton", self._run_decision)

    def _schedule_resume(self, agent_run_id: str) -> None:
        if self.controls is not None and self.controls.requested(agent_run_id):
            return
        self._tasks.queue(
            "resume", agent_run_id,
            lambda: self._resume_and_project(agent_run_id),
        )
        self._tasks.pump()

    def _schedule_start(self, session_id: str) -> None:
        self._tasks.queue(
            "start", session_id,
            lambda: self.interaction.resume_pending_start(
                session_id, propagate_error=False
            ),
        )

    def _schedule_database_work(self, *, limit: int | None = None) -> int:
        available = self._tasks.available
        if limit is not None:
            available = min(available, limit)
        if available == 0:
            return 0
        routes = self.interaction.requested_routes(available)
        starts = self.interaction.requested_starts(available)
        route_items = iter(routes)
        start_items = iter(starts)
        kinds = ("route", "start") if self._poll_routes_first else ("start", "route")
        self._poll_routes_first = not self._poll_routes_first
        scheduled = 0
        exhausted: set[str] = set()
        while scheduled < available and len(exhausted) < 2:
            for kind in kinds:
                if scheduled >= available or kind in exhausted:
                    continue
                try:
                    item = next(route_items if kind == "route" else start_items)
                except StopIteration:
                    exhausted.add(kind)
                    continue
                if kind == "route":
                    key = item.agent_run_id
                    accepted = self._tasks.queue(
                        kind, key, lambda key=key: self._run_route(key)
                    )
                else:
                    key = item
                    accepted = self._tasks.queue(
                        kind, key,
                        lambda key=key: self.interaction.resume_pending_start(
                            key, propagate_error=False
                        ),
                    )
                scheduled += int(accepted)
        return scheduled

    def _schedule_fair_work(self) -> None:
        """Admit decision and database work in a stable round-robin order.

        A decision is only admitted when one is actually pending.  It is not
        inserted ahead of every older task, so capacity one alternates between
        sustained decision and route/start backlogs.
        """

        missed = 0
        while self._tasks.available and missed < 2:
            lane = self._next_admission
            self._next_admission = (
                "decision" if lane == "database" else "database"
            )
            admitted = (
                self._schedule_database_work(limit=1) > 0
                if lane == "database"
                else self._schedule_decision()
            )
            if admitted:
                missed = 0
                self._tasks.pump()
            else:
                missed += 1
        self._tasks.pump()

    async def _run_route(self, agent_run_id: str) -> None:
        route = self.interaction.claim_route(agent_run_id)
        if route is None:
            return
        sources = load_route_context_sources(
            self.agent_runtime.database,
            session_id=route.session_id,
            before_goal_revision=route.goal_revision,
        )
        memory = sources.memory
        task = sources.task_context
        try:
            if matches_execution_case_request(
                self.agent_runtime.settings, route.message
            ) or matches_explicit_model_tool_request(
                self.agent_runtime.settings, route.message
            ):
                now = datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
                result = IntentRoutingResult(
                    intent=IntentDecisionV4.from_python(
                        response_kind="DIALOGUE", semantic_intent="EXECUTE_ON_DATA",
                        task_action="NONE", dialogue_policy="EXECUTION_PROPOSAL",
                        control_intent=None, clarification_reason=None,
                    ),
                    provider="kernel", model="execution-case-rules",
                    trace_id="rule-execution-case", provider_request_count=0,
                    usage=IntentRoutingUsage(0, 0, 0),
                    started_at=now, finished_at=now, duration_ms=0,
                )
                routing_source = "RULE"
            elif not route.input_references and rule_intent(route.message) == "STATUS_QUERY":
                now = datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
                result = IntentRoutingResult(
                    intent=IntentDecisionV4.from_python(
                        response_kind="DIALOGUE", semantic_intent="STATUS_QUERY",
                        task_action="NONE", dialogue_policy="READ_ONLY",
                        control_intent=None, clarification_reason=None,
                    ),
                    provider="kernel", model="state-rules", trace_id="rule-status",
                    provider_request_count=0,
                    usage=IntentRoutingUsage(0, 0, 0),
                    started_at=now, finished_at=now, duration_ms=0,
                )
                routing_source = "RULE"
            else:
                result = await self.intent_router.route(
                    message=route.message,
                    memory=None if memory is None else memory.summary,
                    history=sources.history,
                    task_context=task,
                    candidate_context={
                        "has_task": task is not None,
                        "allowed_task_actions": (
                            ["NONE", "CREATE"] if task is None
                            else ["NONE", "REVISE", "CONTINUE"] + (
                                ["REVIEW"]
                                if task["progress"]["state"] == "WAITING_REVIEW"
                                else []
                            )
                        ),
                        "controls_are_kernel_only": True,
                        "input_references": list(route.input_references),
                    },
                    request_agent_run_id=route.agent_run_id,
                    session_id=route.session_id,
                    goal_revision=route.goal_revision,
                    memory_through_goal_revision=(
                        0 if memory is None else memory.through_goal_revision
                    ),
                )
                routing_source = "MODEL"
        except IntentRoutingError as error:
            self.interaction.fail_route(
                agent_run_id,
                error.code,
                provider=error.provider,
                model=error.model,
                trace_id=error.trace_id,
                input_tokens=error.usage.input_tokens,
                output_tokens=error.usage.output_tokens,
                total_tokens=error.usage.total_tokens,
                provider_requests=error.provider_request_count,
            )
            return
        self.interaction.resolve_route(
            agent_run_id,
            result.intent,
            provider=result.provider,
            model=result.model,
            trace_id=result.trace_id,
            input_tokens=result.usage.input_tokens,
            output_tokens=result.usage.output_tokens,
            total_tokens=result.usage.total_tokens,
            provider_requests=result.provider_request_count,
            routing_source=routing_source,
        )

    async def _run_decision(self) -> None:
        await self.interaction.tick()

    def _prune_traces(self) -> None:
        self.agent_runtime.prune_traces()
        self._next_trace_prune_at = (
            asyncio.get_running_loop().time() + TRACE_PRUNE_INTERVAL_SECONDS
        )

    def _agent_task_failed(self) -> bool:
        return self._tasks.failed()

    async def _wait_for_agent_tasks(self) -> None:
        await self._tasks.close(grace_seconds=self._shutdown_grace_seconds)

    async def _resume_and_project(self, agent_run_id: str) -> None:
        try:
            run = await self.agent_runtime.resume_execution(agent_run_id)
        except (AgentRunError, UpstreamServiceError) as error:
            run = self.agent_runtime.get(error.agent_run_id)
        self.interaction.project(run)
