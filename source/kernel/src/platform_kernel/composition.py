from __future__ import annotations

import asyncio
from functools import partial
import json
from pathlib import Path
from threading import Lock
from typing import TypedDict

from agents.models.interface import Model
import httpx

from .agent_runtime.owner import AgentRuntimeOwner
from .agent_runtime.metrics import agent_status_counts
from .agent_runtime.persistence.provider_requests import (
    provider_latency_metrics,
    provider_unknown_metrics,
)
from .agent_runtime.tracing import (
    SQLiteTraceProcessor,
    install_trace_processors as install_trace_processors,
)
from .agent_runtime.persistence import load_memory, trace_id_for_run
from .agent_runtime.observation_client import ObservationClient
from .change_signal import CommitSignal
from .agent_runtime.preview import PreviewHub
from .agent_runtime.availability import (
    ProviderPool,
    build_agent_provider_pool,
    build_intent_router_provider_pool,
)
from .agent_runtime.provider import build_model_settings
from .agent_runtime.sdk_execution import SDKExecutionDependencies
from .agent_runtime.sdk_execution.usage_ports import (
    SQLiteActivityLog,
    SQLiteUsageLedger,
)
from .database import (
    connect_database,
    initialize_database,
    register_commit_listener,
    sqlite_busy_total,
    unregister_commit_listener,
)
from .intent_routing import IntentRouter
from .application.agent_projection import AgentProjectionAdapter
from .interaction.control_persistence import ControlRequests
from .interaction.access import agent_owner_subject
from .interaction.owner import InteractionOwner
from .runtime import KernelRuntime
from .readiness import PlatformReadiness, ReadinessReport
from .settings import KernelSettings
from .workflow_execution.owner import WorkflowExecutionOwner
from .workflow_execution.execution_client import ExecutionClient
from .workflow_execution.metrics import (
    reconciliation_required_count,
    workflow_status_counts,
)


class KernelMetricSnapshot(TypedDict):
    agents: dict[str, int]
    workflows: dict[str, int]
    reconciliation_required: int
    scheduled: dict[str, int]
    scheduled_queued: dict[str, int]
    scheduled_rejected_total: dict[str, int]
    providers: dict[str, float]
    provider_unknown: dict[str, float]
    agent_concurrency: int
    workflow_dispatch_concurrency: int
    sse: dict[str, int]
    sqlite_busy: int
    intent_routing: dict[str, int | float]


class PlatformApplication:
    def __init__(
        self,
        root: Path | None = None,
        *,
        settings: KernelSettings | None = None,
        model: Model | None = None,
        intent_router_model: Model | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.settings = settings or KernelSettings.from_environment()
        self.capabilities_json = json.dumps(
            {
                "productId": self.settings.product_id,
                "projectId": self.settings.project_id,
                "services": sorted(self.settings.services_by_name),
                "orchestrationOwner": "AGENT",
                "observationTools": [
                    {
                        "name": item.name,
                        "description": item.description,
                        "inputSchema": item.input_schema,
                        "outputSchema": item.output_schema,
                    }
                    for item in self.settings.observation_tools
                ],
                "models": [
                    {
                        "modelId": item.model_id,
                        "modelVersion": item.model_version,
                        "modelChecksum": item.model_checksum,
                        "workflowId": item.workflow_id,
                        "workflowVersion": item.workflow_version,
                        "workflowChecksum": item.workflow_checksum,
                        "description": item.tool_description,
                        "tool": item.tool_name,
                        "inputSchema": item.input_schema,
                        "dataInputParameters": list(item.data_input_parameters),
                        "requiredInputs": [
                            name
                            for name in item.input_schema.get("required", [])
                            if name not in item.default_parameters
                        ],
                        "defaultParameters": item.default_parameters,
                        "finalOutputs": list(item.final_outputs),
                        "applicabilityStatus": "REQUIRES_CURRENT_KNOWLEDGE",
                        "deadlineSeconds": item.deadline_seconds,
                    }
                    for item in self.settings.models
                ],
            },
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        root = root or self.settings.data_root
        root.mkdir(parents=True, exist_ok=True)
        self.database = root / "kernel.sqlite3"
        self._metric_lock = Lock()
        self._sse_connections = 0
        self._sse_reconnections = 0
        initialize_database(self.database)
        self.commit_signal = CommitSignal()
        self.previews = PreviewHub()
        self._owns_client = client is None
        # Service-to-service traffic is entirely described by the instance
        # configuration.  Ambient HTTP(S)/SOCKS proxy variables belong to the
        # operator's shell and must not silently alter an instance network
        # boundary (or add an undeclared ``socksio`` runtime dependency).
        self.client = client or httpx.AsyncClient(trust_env=False)
        self.provider_pool: ProviderPool | None = None
        if model is None:
            self.provider_pool = build_agent_provider_pool(self.settings)
            model = self.provider_pool.model
        agent_slots = asyncio.Semaphore(self.settings.agent_concurrency)
        self.workflow_execution = WorkflowExecutionOwner(
            self.database,
            self.settings.models,
            self.settings.services,
            ExecutionClient(self.client),
            controls=ControlRequests(self.database),
            expected_definition_set_digest=self.settings.definition_set_digest,
            dispatch_concurrency=self.settings.workflow_dispatch_concurrency,
            trace_id_resolver=partial(trace_id_for_run, self.database),
            composition=self.settings.workflow_composition,
        )
        self.agent_projection = AgentProjectionAdapter()
        self.agent_runtime = AgentRuntimeOwner(
            self.database,
            self.settings,
            self.workflow_execution,
            SDKExecutionDependencies(
                database=self.database,
                model=model,
                model_settings=build_model_settings(self.settings),
                settings=self.settings,
                observation_client=ObservationClient(
                    self.client,
                    actor_subject_resolver=partial(
                        agent_owner_subject, self.database
                    ),
                    default_actor_subject=self.settings.local_identity_subject,
                ),
                agent_slots=agent_slots,
                activity_log=SQLiteActivityLog(self.database),
                publish_preview=self.previews.publish
                if self.settings.agent_preview_enabled
                else None,
                usage_ledger=SQLiteUsageLedger(self.database),
            ),
            self.agent_projection,
        )
        router_provider_pool = None
        if intent_router_model is None:
            router_provider_pool = build_intent_router_provider_pool(
                self.settings.intent_router,
                timeout_seconds=self.settings.readiness_timeout_seconds,
                success_cache_seconds=self.settings.readiness_success_cache_seconds,
                failure_cache_seconds=self.settings.readiness_failure_cache_seconds,
            )
        self.intent_router = IntentRouter(
            self.settings.intent_router,
            agent_slots=agent_slots,
            model=intent_router_model,
            usage_ledger=SQLiteUsageLedger(self.database),
            provider_pool=router_provider_pool,
        )
        self.readiness = PlatformReadiness(
            self.settings,
            agent_providers=self.provider_pool,
            router_providers=router_provider_pool,
        )
        self.interaction = InteractionOwner(
            self.agent_runtime,
            self.workflow_execution,
            self.database,
            self.settings.project_id,
            self.client,
            self.settings.services_by_name["data"],
            models=self.settings.models,
            memory_reader=load_memory,
            local_identity_subject=self.settings.local_identity_subject,
            max_pending_agent_turns=(
                self.settings.agent_concurrency + self.settings.agent_queue_capacity
            ),
        )
        self.trace_processor = SQLiteTraceProcessor(self.database)
        self.runtime = KernelRuntime(
            self.interaction,
            self.agent_runtime,
            self.workflow_execution,
            self.intent_router,
            self.commit_signal,
            controls=ControlRequests(self.database),
            task_concurrency=self.settings.agent_concurrency,
            task_queue_capacity=self.settings.agent_queue_capacity,
            shutdown_grace_seconds=self.settings.agent_shutdown_grace_seconds,
        )

    async def start(self) -> None:
        self.commit_signal.bind()
        register_commit_listener(self.database, self.commit_signal.notify)
        await self.runtime.start()

    async def close(self) -> None:
        errors: list[BaseException] = []
        try:
            await self.runtime.stop()
        except BaseException as error:
            errors.append(error)
        finally:
            unregister_commit_listener(self.database, self.commit_signal.notify)
            self.commit_signal.unbind()
        try:
            if self.provider_pool is not None:
                await self.provider_pool.close()
        except BaseException as error:
            errors.append(error)
        try:
            await self.intent_router.close()
        except BaseException as error:
            errors.append(error)
        try:
            await self.readiness.close()
        except BaseException as error:
            errors.append(error)
        try:
            if self._owns_client:
                await self.client.aclose()
        except BaseException as error:
            errors.append(error)
        if len(errors) == 1:
            raise errors[0]
        if errors:
            primary = errors[0]
            for secondary in errors[1:]:
                primary.add_note(f"Additional shutdown failure: {secondary!r}")
            raise primary

    def is_ready(self) -> bool:
        return self.runtime.is_ready()

    async def readiness_report(self) -> ReadinessReport:
        return await self.readiness.check(kernel_ready=self.is_ready())

    def check_local_store(self) -> None:
        with connect_database(self.database) as connection:
            connection.execute("SELECT 1").fetchone()

    def metric_snapshot(self) -> KernelMetricSnapshot:
        with self._metric_lock:
            sse = {
                "connections": self._sse_connections,
                "reconnections": self._sse_reconnections,
            }
        task_metrics = self.runtime.task_metrics()
        return {
            "agents": agent_status_counts(self.database),
            "workflows": workflow_status_counts(self.database),
            "reconciliation_required": reconciliation_required_count(self.database),
            "scheduled": task_metrics["active"],
            "scheduled_queued": task_metrics["queued"],
            "scheduled_rejected_total": task_metrics["rejected_total"],
            "providers": provider_latency_metrics(self.database),
            "provider_unknown": provider_unknown_metrics(self.database),
            "agent_concurrency": self.settings.agent_concurrency,
            "workflow_dispatch_concurrency": self.settings.workflow_dispatch_concurrency,
            "sse": sse,
            "sqlite_busy": sqlite_busy_total(),
            "intent_routing": dict(self.interaction.route_metrics()),
        }

    def record_sse_connection(self, *, reconnect: bool) -> None:
        with self._metric_lock:
            self._sse_connections += 1
            if reconnect:
                self._sse_reconnections += 1
