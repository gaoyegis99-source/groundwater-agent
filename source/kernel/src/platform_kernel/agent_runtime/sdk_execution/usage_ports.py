"""Typed persistence ports for body-free SDK usage accounting.

The capture hooks receive one ledger object instead of an open-ended group of
optional callbacks.  The SQLite adapter remains local to Kernel and owns no
prompt, response, or observation body.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from ..persistence.context_views import create_context_view, finish_context_view
from ..persistence.provider_requests import (
    begin_provider_request,
    finish_provider_request,
    mark_provider_protocol_failure,
)
from ..persistence.timeline import save_activity
from ..protocol_repair import single_planned_approval_tool, stage_protocol_retry


class UsageLedgerPort(Protocol):
    def create_context_view(
        self,
        *,
        request_agent_run_id: str,
        segment_id: str,
        request_ordinal: int,
        purpose: str,
        manifest: dict[str, object],
        status: str,
        error_code: str | None,
    ) -> str | None: ...

    def finish_context_view(
        self,
        context_view_id: str | None,
        *,
        status: str,
        actual_input_tokens: int | None,
        error_code: str | None,
    ) -> None: ...

    def begin_provider_request(
        self,
        *,
        agent_run_id: str,
        stage: str,
        provider: str,
        model: str,
        protocol: str,
        attempt: int,
    ) -> str: ...

    def finish_provider_request(
        self,
        provider_request_id: str,
        *,
        outcome: str,
        error_class: str | None,
        input_tokens: int,
        output_tokens: int,
        total_tokens: int,
        latency_ms: int,
    ) -> None: ...

    def mark_protocol_failure(self, agent_run_id: str) -> tuple[str, int] | None: ...

    def stage_protocol_retry(self, context: object) -> bool: ...

    def planned_approval_tool(self, agent_run_id: str, enabled_tools: object) -> str | None: ...


class ActivityLogPort(Protocol):
    """Append an allowlisted activity record to a durable session timeline."""

    def record(self, session_id: str, activity: dict[str, Any]) -> None: ...


class ActivityRecorder(Protocol):
    """A segment-scoped activity sink used by SDK hooks."""

    def record(self, activity: dict[str, Any]) -> None: ...


class NoopActivityRecorder:
    """Explicitly discard activity for body-free routing-only captures."""

    def record(self, activity: dict[str, Any]) -> None:
        del activity


@dataclass(frozen=True)
class SQLiteActivityLog:
    database: Path

    def record(self, session_id: str, activity: dict[str, Any]) -> None:
        save_activity(self.database, session_id, activity)


@dataclass(frozen=True)
class ScopedActivityRecorder:
    """Bind one durable activity log to the current Session at composition time."""

    activity_log: ActivityLogPort
    session_id: str

    def record(self, activity: dict[str, Any]) -> None:
        self.activity_log.record(self.session_id, activity)


@dataclass(frozen=True)
class CallbackActivityRecorder:
    """Narrow test adapter; production uses ``SQLiteActivityLog`` instead."""

    callback: Callable[[dict[str, Any]], None]

    def record(self, activity: dict[str, Any]) -> None:
        self.callback(activity)


@dataclass(frozen=True)
class CallbackActivityLog:
    """Narrow test adapter for callers that need to observe a Session key."""

    callback: Callable[[str, dict[str, Any]], None]

    def record(self, session_id: str, activity: dict[str, Any]) -> None:
        self.callback(session_id, activity)


class SQLiteUsageLedger:
    """Kernel's typed adapter over context and Provider-attempt persistence."""

    def __init__(self, database: Path) -> None:
        self._database = database

    def create_context_view(self, **kwargs) -> str | None:
        return create_context_view(self._database, **kwargs)

    def finish_context_view(self, context_view_id: str | None, **kwargs) -> None:
        finish_context_view(self._database, context_view_id, **kwargs)

    def begin_provider_request(self, **kwargs) -> str:
        return begin_provider_request(self._database, **kwargs)

    def finish_provider_request(self, provider_request_id: str, **kwargs) -> None:
        finish_provider_request(self._database, provider_request_id, **kwargs)

    def mark_protocol_failure(self, agent_run_id: str) -> tuple[str, int] | None:
        return mark_provider_protocol_failure(self._database, agent_run_id)

    def stage_protocol_retry(self, context: object) -> bool:
        return stage_protocol_retry(self._database, context)

    def planned_approval_tool(self, agent_run_id: str, enabled_tools: object) -> str | None:
        return single_planned_approval_tool(self._database, agent_run_id, enabled_tools)


__all__ = (
    "ActivityLogPort",
    "ActivityRecorder",
    "CallbackActivityLog",
    "CallbackActivityRecorder",
    "NoopActivityRecorder",
    "ScopedActivityRecorder",
    "SQLiteActivityLog",
    "SQLiteUsageLedger",
    "UsageLedgerPort",
)
