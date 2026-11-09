from __future__ import annotations

from typing import Annotated

from fastapi import FastAPI, Path

from ..composition import ExecutorApplication
from ..worker_contracts import (
    LeaseClaimResponseV3,
    LeaseCompletionV3,
    LeaseFailureV3,
    LeaseHeartbeatV3,
    WorkerIdentityV3,
    lease_to_wire,
)


def register_worker_routes(app: FastAPI, application: ExecutorApplication) -> None:
    @app.post("/internal/workers/v3/heartbeat", status_code=204, operation_id="executor_worker_heartbeat")
    def worker_heartbeat(request: WorkerIdentityV3):
        application.control.heartbeat(request.model_dump(by_alias=False))

    @app.post("/internal/workers/v3/nodes:claim", response_model=LeaseClaimResponseV3, operation_id="executor_claim_node")
    def claim_node(request: WorkerIdentityV3):
        lease = application.control.claim(request.model_dump(by_alias=False))
        return {"lease": lease_to_wire(lease)}

    @app.post("/internal/workers/v3/leases/{leaseId}:heartbeat", status_code=204, operation_id="executor_lease_heartbeat")
    def lease_heartbeat(lease_id: Annotated[str, Path(alias="leaseId")], request: LeaseHeartbeatV3):
        application.control.lease_heartbeat(
            lease_id, request.model_dump(by_alias=False)
        )

    @app.post("/internal/workers/v3/leases/{leaseId}:complete", status_code=204, operation_id="executor_complete_lease")
    def complete_lease(lease_id: Annotated[str, Path(alias="leaseId")], request: LeaseCompletionV3):
        application.control.complete(lease_id, request.model_dump(by_alias=False))

    @app.post("/internal/workers/v3/leases/{leaseId}:fail", status_code=204, operation_id="executor_fail_lease")
    def fail_lease(lease_id: Annotated[str, Path(alias="leaseId")], request: LeaseFailureV3):
        application.control.fail(lease_id, request.model_dump(by_alias=False))
