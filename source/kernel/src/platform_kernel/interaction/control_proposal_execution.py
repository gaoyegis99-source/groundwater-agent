"""Effectful reconciliation commands for durable control proposals.

The public :class:`InteractionOwner` remains the stable application facade.  This
module owns the proposal claim, target-side effect, completion, and recovery
sequence so that the facade does not also implement the command workflow.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from ..errors import ConfirmationConflictError
from .control_persistence import request_stop
from .control_proposal_commands import (
    claim_control_proposal,
    finish_control_proposal,
)
from .control_proposal_queries import (
    confirmed_control_proposals,
    control_source_message,
)
from .models import SessionView


class ConfirmationCommandPort(Protocol):
    """The stable confirmation operations required by proposal execution."""

    async def confirm(
        self,
        session_id: str,
        confirmation_id: str,
        decision_subject: str | None = None,
    ) -> SessionView: ...

    async def reject(
        self,
        session_id: str,
        confirmation_id: str,
        reason: str,
        decision_subject: str | None = None,
    ) -> SessionView: ...

    def get(self, session_id: str) -> SessionView: ...


@dataclass(frozen=True)
class ControlProposalExecutor:
    """Execute one claimed proposal and reconcile incomplete claims on startup."""

    database: Path
    project_id: str
    confirmations: ConfirmationCommandPort

    async def confirm(
        self,
        session_id: str,
        proposal_id: str,
        request_id: str,
        *,
        decision_subject: str,
    ) -> SessionView:
        proposal = claim_control_proposal(
            self.database,
            session_id,
            self.project_id,
            proposal_id,
            request_id,
            decision_subject,
        )
        if proposal["status"] == "COMPLETE":
            return self.confirmations.get(session_id)
        if proposal["status"] != "CONFIRMED":
            raise ConfirmationConflictError("CONTROL_PROPOSAL_NOT_ACTIONABLE")
        try:
            if proposal["intent"] == "APPROVE_CONFIRMATION":
                await self.confirmations.confirm(
                    session_id, str(proposal["target_id"]), decision_subject
                )
            elif proposal["intent"] == "REJECT_CONFIRMATION":
                await self.confirmations.reject(
                    session_id,
                    str(proposal["target_id"]),
                    control_source_message(self.database, proposal_id),
                    decision_subject,
                )
            else:
                request_stop(
                    self.database,
                    session_id,
                    self.project_id,
                    f"control-proposal:{proposal_id}",
                    str(proposal["target_id"]),
                    decision_subject,
                )
        except Exception:
            finish_control_proposal(self.database, proposal_id, failed=True)
            raise
        finish_control_proposal(self.database, proposal_id)
        return self.confirmations.get(session_id)

    async def recover(self) -> None:
        """Resume only durable claims that have not reached a terminal state."""

        for proposal in confirmed_control_proposals(self.database):
            request_id = proposal["decision_request_id"]
            if not isinstance(request_id, str):
                raise ValueError("CONTROL_PROPOSAL_DECISION_REQUEST_MISSING")
            try:
                await self.confirm(
                    str(proposal["session_id"]),
                    str(proposal["proposal_id"]),
                    request_id,
                    decision_subject="system-recovery",
                )
            except ConfirmationConflictError:
                # A terminalized proposal is already safe to leave behind.
                continue


__all__ = ("ConfirmationCommandPort", "ControlProposalExecutor")
