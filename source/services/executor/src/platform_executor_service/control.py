from __future__ import annotations

from pathlib import Path

from .artifacts import ArtifactStore
from .contracts import JobRequestV6, JobStatusV6
from .control_components import (
    AdmissionControl,
    CompletionControl,
    ControlContext,
    ExecutionQueries,
    LeaseControl,
    RecoveryControl,
)
from .data_client import DataClient


class ExecutorControl:
    """Stable facade over responsibility-specific execution services."""

    def __init__(
        self,
        database: Path,
        artifacts: ArtifactStore,
        config: dict,
        data_client: DataClient,
        project_id: str,
    ) -> None:
        self.database = database
        self.artifacts = artifacts
        self.config = config
        self.project_id = project_id
        self.catalog_digest = config["operatorCatalogDigest"]
        self.models = {
            (item["modelId"], item["version"]): item for item in config["models"]
        }
        self._context = ControlContext(
            database=database,
            artifacts=artifacts,
            config=config,
            data_client=data_client,
            project_id=project_id,
            catalog_digest=self.catalog_digest,
            models=self.models,
        )
        self.admission = AdmissionControl(self._context)
        self.recovery = RecoveryControl(self._context)
        self.leases = LeaseControl(self._context, self.recovery)
        self.completion = CompletionControl(self._context)
        self.queries = ExecutionQueries(self._context)

    @property
    def data_client(self) -> DataClient:
        return self._context.data_client

    @data_client.setter
    def data_client(self, value: DataClient) -> None:
        self._context.data_client = value

    def submit(
        self, request: JobRequestV6, traceparent: str | None = None
    ) -> JobStatusV6:
        return self.admission.submit(request, traceparent)

    def heartbeat(self, body: dict) -> None:
        self.leases.heartbeat(body)

    def claim(self, body: dict) -> dict | None:
        return self.leases.claim(body)

    def lease_heartbeat(self, lease_id: str, body: dict) -> None:
        self.leases.heartbeat_lease(lease_id, body)

    def complete(self, lease_id: str, body: dict) -> None:
        self.completion.complete(lease_id, body)

    def fail(self, lease_id: str, body: dict) -> None:
        self.completion.fail(lease_id, body)

    def reconcile(self) -> None:
        self.recovery.reconcile()

    def recover(self) -> None:
        self.recovery.recover()

    def get_by_request(self, project_id: str, request_id: str) -> JobStatusV6:
        return self.queries.by_request(project_id, request_id)

    def get(self, job_id: str) -> JobStatusV6:
        return self.queries.get(job_id)

    def publication(self, job_id: str) -> dict:
        return self.queries.publication(job_id)

    def cancel(self, job_id: str) -> JobStatusV6:
        return self.queries.cancel(job_id)

    def ready(self) -> bool:
        return self.queries.ready()

    def runtime_statuses(self) -> dict[str, str]:
        return self.queries.runtime_statuses()

    def job_graph(self, job_id: str) -> dict:
        return self.queries.job_graph(job_id)

    def node_diagnostics(self, job_id: str) -> list[dict]:
        return self.queries.node_diagnostics(job_id)
