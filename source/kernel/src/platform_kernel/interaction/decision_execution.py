"""Execution of already-persisted confirmation decisions.

The owner composes this runner with the Session-facing commands.  Keeping the
claim/side-effect/finalize sequence here makes the durable decision lifecycle
explicit without exposing a second public interaction entry point.
"""

from __future__ import annotations

from pathlib import Path

from ..application.ports import AgentRuntimePort
from ..errors import AgentRunError, UpstreamServiceError
from .confirmation import claim_decision, finalize_decision


class DecisionExecutor:
    def __init__(self, database: Path, agent_runtime: AgentRuntimePort) -> None:
        self.database = database
        self.agent_runtime = agent_runtime

    async def drive(
        self, confirmation_id: str, *, propagate_error: bool = True
    ) -> None:
        decision = claim_decision(self.database, confirmation_id)
        if decision is None:
            return
        error: AgentRunError | UpstreamServiceError | None = None
        run = self.agent_runtime.get(decision.agent_run_id, decision.project_id)
        try:
            if decision.decision == "CONFIRM":
                if decision.kind == "RECOVERY":
                    run = self.agent_runtime.confirm_recovery(
                        run.agent_run_id,
                        run.project_id,
                        decision.confirmation_id,
                        decision.actions,
                    )
                else:
                    run = self.agent_runtime.confirm(
                        run.agent_run_id,
                        run.project_id,
                        decision.confirmation_id,
                        decision.actions,
                    )
            elif decision.decision == "REJECT":
                if decision.reason is None:
                    raise ValueError("REJECTION_REASON_REQUIRED")
                run = await self.agent_runtime.reject(
                    run.agent_run_id,
                    run.project_id,
                    decision.reason,
                    tool_call_ids=decision.tool_call_ids,
                )
            else:
                self.agent_runtime.supersede(run.agent_run_id)
                run = self.agent_runtime.get(run.agent_run_id, run.project_id)
        except (AgentRunError, UpstreamServiceError) as caught:
            error = caught
            run = self.agent_runtime.get(decision.agent_run_id, decision.project_id)
        current = finalize_decision(self.database, decision, run)
        if not current and run.status == "WAITING_CONFIRMATION":
            self.agent_runtime.supersede(run.agent_run_id)
        if error is not None and propagate_error:
            raise error


__all__ = ("DecisionExecutor",)
