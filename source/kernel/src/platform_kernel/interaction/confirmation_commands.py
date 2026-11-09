"""Confirmation state commands behind the stable InteractionOwner facade."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from .confirmation import request_decision
from .decision_execution import DecisionExecutor
from .models import SessionView


class SessionProjectionPort(Protocol):
    def get(self, session_id: str) -> SessionView: ...


@dataclass(frozen=True)
class ConfirmationExecutor:
    """Apply one durable user decision, then project the resulting Session."""

    database: Path
    project_id: str
    decisions: DecisionExecutor
    sessions: SessionProjectionPort

    async def reject(
        self,
        session_id: str,
        confirmation_id: str,
        reason: str,
        *,
        decision_subject: str,
    ) -> SessionView:
        request_decision(
            self.database,
            session_id,
            confirmation_id,
            "REJECT",
            reason,
            self.project_id,
            decision_subject,
        )
        return self.sessions.get(session_id)

    async def confirm(
        self,
        session_id: str,
        confirmation_id: str,
        *,
        decision_subject: str,
    ) -> SessionView:
        if request_decision(
            self.database,
            session_id,
            confirmation_id,
            "CONFIRM",
            None,
            self.project_id,
            decision_subject,
        ):
            await self.decisions.drive(confirmation_id)
        return self.sessions.get(session_id)


__all__ = ("ConfirmationExecutor", "SessionProjectionPort")
