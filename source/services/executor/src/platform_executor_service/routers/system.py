from __future__ import annotations

import os

from fastapi import FastAPI

from ..composition import ExecutorApplication
from ..database import check_database
from ..errors import ExecutorUnavailableError
from ..worker_contracts import HealthResponse, ReadinessResponse


def register_system_routes(app: FastAPI, application: ExecutorApplication) -> None:
    @app.get("/internal/health/live", response_model=HealthResponse, operation_id="executor_internal_liveness")
    def liveness():
        try:
            check_database(application.database)
        except RuntimeError as error:
            raise ExecutorUnavailableError("EXECUTOR_NOT_READY") from error
        return {"status": "UP"}

    @app.get("/internal/health/ready", response_model=ReadinessResponse, operation_id="executor_internal_readiness")
    def readiness():
        liveness()
        if (
            not application.runtime.is_ready()
            or not application.temp_root.is_dir()
            or not application.object_root.is_dir()
            or not application.database.parent.is_dir()
            or not os.access(application.temp_root, os.R_OK | os.W_OK)
            or not os.access(application.database.parent, os.R_OK | os.W_OK)
        ):
            raise ExecutorUnavailableError("EXECUTOR_NOT_READY")
        return {
            "status": "UP",
            "definitionSetDigest": application.definition_set_digest,
            "components": {
                "controlPlane": "UP",
                **application.control.runtime_statuses(),
            },
        }

    @app.get("/internal/health", response_model=ReadinessResponse, operation_id="executor_internal_health")
    def health():
        return readiness()
