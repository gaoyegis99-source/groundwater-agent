from __future__ import annotations

from pathlib import Path
from collections.abc import Callable
from uuid import uuid4
import httpx

from ..application.ports import AgentRuntimePort, WorkflowExecutionPort
from ..application.query_dtos import AgentRunView, MemoryView
from ..errors import ResourceNotFoundError
from .workflow_drafts import create_workflow_draft, get_workflow_draft
from .actors import DEFAULT_LOCAL_ACTOR_SUBJECT
from .confirmation import (
    has_requested,
    next_requested,
    recover_requested,
)
from .models import SessionView
from .history import list_sessions, message_history, session_view
from .intent_route_queue import (
    claim_intent_route,
    recover_intent_routes,
    requested_intent_routes,
)
from ..application.route_commands import fail_intent_route, resolve_intent_route
from .message_request_common import IntentRouteRequest
from .message_requests import create_message_request, message_request
from .persistence import (
    create_session,
    events_after,
    get_session,
    history,
    recover_agent_starts,
    requested_agent_starts,
    reserve_turn,
)
from .agent_starts import PendingAgentStartExecutor
from .decision_execution import DecisionExecutor
from .confirmation_commands import ConfirmationExecutor
from .revisions import revise_confirmation
from .control_proposal_commands import dismiss_control_proposal
from .control_proposal_execution import ControlProposalExecutor
from .workflow_recovery_commands import WorkflowRecoveryExecutor


class InteractionOwner:
    def __init__(
        self,
        agent_runtime: AgentRuntimePort,
        workflow_execution: WorkflowExecutionPort,
        database: Path,
        project_id: str,
        client: httpx.AsyncClient | None = None,
        resource_service: object | None = None,
        *,
        models: tuple[object, ...] = (),
        memory_reader: Callable[[Path, str], MemoryView | None],
        local_identity_subject: str = DEFAULT_LOCAL_ACTOR_SUBJECT,
        max_pending_agent_turns: int = 108,
    ) -> None:
        self.agent_runtime = agent_runtime
        self.workflow_execution = workflow_execution
        self.database = database
        self.project_id = project_id
        self.client = client
        self.resource_service = resource_service
        self.models = models
        self.local_identity_subject = local_identity_subject
        self.max_pending_agent_turns = max_pending_agent_turns
        self._decision_executor = DecisionExecutor(database, agent_runtime)
        self._start_executor = PendingAgentStartExecutor(
            database, agent_runtime, memory_reader
        )
        self._confirmation_executor = ConfirmationExecutor(
            database, project_id, self._decision_executor, self
        )
        self._control_proposal_executor = ControlProposalExecutor(
            database, project_id, self
        )
        self._workflow_recovery_executor = WorkflowRecoveryExecutor(
            database, project_id, workflow_execution, self
        )

    def _actor(self, subject: str | None) -> str:
        """Use the composed local identity only for direct non-HTTP callers."""

        return self.local_identity_subject if subject is None else subject

    def create_session(self, owner_subject: str | None = None) -> SessionView:
        session_id = str(uuid4())
        create_session(self.database, session_id, self.project_id, self._actor(owner_subject))
        return self.get(session_id)

    def list_sessions(
        self,
        before: int | None,
        limit: int,
        *,
        owner_subject: str | None = None,
        query: str = "",
        archived: bool = False,
    ) -> dict:
        return list_sessions(
            self.database,
            self.project_id,
            before,
            limit,
            owner_subject=self._actor(owner_subject),
            query=query,
            archived=archived,
        )

    def messages(self, session_id: str, before: int | None, limit: int) -> dict:
        return message_history(
            self.database, self.project_id, session_id, before, limit
        )

    def message(
        self,
        session_id: str,
        request_id: str,
        message: str,
        requested_mode: str,
        *,
        input_references: list[dict[str, object]] | None = None,
        corrects_request_id: str | None = None,
        corrects_agent_run_id: str | None = None,
        owner_subject: str | None = None,
    ) -> dict[str, object]:
        request, _ = create_message_request(
            self.database,
            session_id,
            self.project_id,
            request_id,
            message,
            requested_mode,
            input_references=input_references,
            corrects_request_id=corrects_request_id,
            corrects_agent_run_id=corrects_agent_run_id,
            owner_subject=self._actor(owner_subject),
            max_pending=self.max_pending_agent_turns,
        )
        return request

    def message_request(self, session_id: str, request_id: str) -> dict[str, object]:
        return message_request(self.database, session_id, self.project_id, request_id)

    def requested_routes(self, limit: int | None = None) -> tuple[IntentRouteRequest, ...]:
        return requested_intent_routes(self.database, limit)

    def route_history(self, session_id: str) -> tuple[tuple[int, str, str], ...]:
        session = get_session(self.database, session_id)
        if session.project_id != self.project_id:
            raise ResourceNotFoundError("SESSION_NOT_FOUND")
        items = history(self.database, session_id, after_revision=0)
        revisions = tuple(dict.fromkeys(item[0] for item in items))[-2:]
        return tuple(item for item in items if item[0] in revisions)

    def claim_route(self, agent_run_id: str) -> IntentRouteRequest | None:
        return claim_intent_route(self.database, agent_run_id)

    def resolve_route(
        self,
        agent_run_id: str,
        decision,
        dialogue_policy: str | None = None,
        *,
        provider: str,
        model: str,
        trace_id: str,
        provider_requests: int,
        input_tokens: int,
        output_tokens: int,
        total_tokens: int,
        routing_source: str = "MODEL",
    ) -> dict[str, object]:
        return resolve_intent_route(
            self.database,
            agent_run_id,
            decision,
            dialogue_policy,
            provider=provider,
            model=model,
            trace_id=trace_id,
            provider_requests=provider_requests,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
            routing_source=routing_source,
        )

    def fail_route(
        self,
        agent_run_id: str,
        error_code: str,
        *,
        provider: str,
        model: str,
        trace_id: str,
        provider_requests: int,
        input_tokens: int,
        output_tokens: int,
        total_tokens: int,
    ) -> dict[str, object]:
        return fail_intent_route(
            self.database,
            agent_run_id,
            error_code,
            provider=provider,
            model=model,
            trace_id=trace_id,
            provider_requests=provider_requests,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
        )

    async def turn(
        self,
        session_id: str,
        message: str,
        *,
        dialogue_policy: str = "READ_ONLY",
    ) -> SessionView:
        pending_agent_run_id = str(uuid4())
        superseded = reserve_turn(
            self.database,
            session_id,
            message,
            pending_agent_run_id,
            self.project_id,
            dialogue_policy=dialogue_policy,
        )
        if superseded is not None:
            await self._decision_executor.drive(superseded)
        await self.resume_pending_start(session_id)
        return self.get(session_id)

    def draft_workflow(self, session_id: str, request_id: str, message: str) -> dict:
        draft_id = create_workflow_draft(
            self.database, session_id, self.project_id, request_id, message
        )
        return self.workflow_draft(session_id, draft_id)

    def workflow_draft(self, session_id: str, draft_id: str) -> dict:
        return get_workflow_draft(self.database, session_id, self.project_id, draft_id)

    def requested_starts(self, limit: int | None = None) -> tuple[str, ...]:
        return requested_agent_starts(self.database, limit)

    async def reject(
        self,
        session_id: str,
        confirmation_id: str,
        reason: str,
        decision_subject: str | None = None,
    ) -> SessionView:
        return await self._confirmation_executor.reject(
            session_id,
            confirmation_id,
            reason,
            decision_subject=self._actor(decision_subject),
        )

    async def confirm(
        self,
        session_id: str,
        confirmation_id: str,
        decision_subject: str | None = None,
    ) -> SessionView:
        return await self._confirmation_executor.confirm(
            session_id,
            confirmation_id,
            decision_subject=self._actor(decision_subject),
        )

    async def confirm_control_proposal(
        self,
        session_id: str,
        proposal_id: str,
        request_id: str,
        decision_subject: str = "system-recovery",
    ) -> SessionView:
        return await self._control_proposal_executor.confirm(
            session_id,
            proposal_id,
            request_id,
            decision_subject=decision_subject,
        )

    async def recover_control_proposals(self) -> None:
        await self._control_proposal_executor.recover()

    def dismiss_control_proposal(
        self,
        session_id: str,
        proposal_id: str,
        request_id: str,
        decision_subject: str | None = None,
    ) -> SessionView:
        dismiss_control_proposal(
            self.database,
            session_id,
            self.project_id,
            proposal_id,
            request_id,
            self._actor(decision_subject),
        )
        return self.get(session_id)

    async def revise_confirmation(
        self,
        session_id: str,
        confirmation_id: str,
        request_id: str,
        expected_goal_revision: int,
        actions: tuple[dict[str, object], ...],
        decision_subject: str | None = None,
    ) -> SessionView:
        if self.client is None:
            raise RuntimeError("RESOURCE_SERVICE_UNAVAILABLE")
        await revise_confirmation(
            self.database,
            self.client,
            self.resource_service,
            self.models,
            session_id=session_id,
            confirmation_id=confirmation_id,
            project_id=self.project_id,
            request_id=request_id,
            expected_goal_revision=expected_goal_revision,
            actions=actions,
            decision_subject=self._actor(decision_subject),
        )
        return self.get(session_id)

    def recover_workflow(
        self,
        session_id: str,
        workflow_run_id: str,
        request_id: str,
        expected_goal_revision: int,
        decision_subject: str | None = None,
    ) -> SessionView:
        return self._workflow_recovery_executor.recover(
            session_id,
            workflow_run_id,
            request_id,
            expected_goal_revision,
            decision_subject=self._actor(decision_subject),
        )

    def prepare_recovery(self) -> tuple[str, ...]:
        recover_requested(self.database)
        recover_intent_routes(self.database)
        return recover_agent_starts(self.database)

    async def tick(self) -> bool:
        confirmation_id = next_requested(self.database)
        if confirmation_id is None:
            return False
        await self._decision_executor.drive(confirmation_id, propagate_error=False)
        return True

    def has_pending_decision(self) -> bool:
        return has_requested(self.database)

    def get(self, session_id: str) -> SessionView:
        return session_view(self.database, self.project_id, session_id)

    def events_after(
        self, session_id: str, event_id: int
    ) -> tuple[dict[str, object], ...]:
        self.get(session_id)
        return events_after(self.database, session_id, event_id)

    def results(self, session_id: str, after_revision: int) -> dict[str, object]:
        session = get_session(self.database, session_id)
        if session.project_id != self.project_id:
            raise ResourceNotFoundError("SESSION_NOT_FOUND")
        return self.agent_runtime.session_results(
            session_id, session.project_id, session.goal_revision, after_revision
        )

    def project(self, run: AgentRunView) -> None:
        self._start_executor.project(run)

    async def resume_pending_start(
        self, session_id: str, propagate_error: bool = True
    ) -> None:
        await self._start_executor.resume(
            session_id, propagate_error=propagate_error
        )
