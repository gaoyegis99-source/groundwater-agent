from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agents import ModelSettings
from agents.models.interface import Model

from ...settings import KernelSettings
from ..memory import MemoryUpdate
from ..observation_client import ObservationClient
from .usage_ports import ActivityLogPort, UsageLedgerPort


@dataclass(frozen=True)
class ExecutionUsage:
    input_tokens: int
    output_tokens: int
    total_tokens: int


@dataclass(frozen=True)
class SDKExecutionResult:
    result: Any
    usage: ExecutionUsage
    memory_update: MemoryUpdate | None


@dataclass(frozen=True)
class SDKExecutionDependencies:
    database: Path
    model: Model
    model_settings: ModelSettings
    settings: KernelSettings
    observation_client: ObservationClient
    agent_slots: asyncio.Semaphore
    activity_log: ActivityLogPort
    publish_preview: Callable[[str, str, str | None], None] | None = None
    usage_ledger: UsageLedgerPort | None = None


class SDKExecutionError(RuntimeError):
    def __init__(
        self,
        code: str,
        *,
        upstream: bool,
        usage: ExecutionUsage | None = None,
        memory_update: MemoryUpdate | None = None,
        failed_activity_id: str | None = None,
        diagnostics: dict | None = None,
    ) -> None:
        super().__init__(code)
        self.code = code
        self.upstream = upstream
        self.usage = usage
        self.memory_update = memory_update
        self.failed_activity_id = failed_activity_id
        self.diagnostics = diagnostics


class SDKStateError(RuntimeError):
    pass
