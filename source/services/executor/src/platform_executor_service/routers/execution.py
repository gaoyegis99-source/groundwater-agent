from __future__ import annotations

from typing import Annotated

from fastapi import FastAPI, Path, Query, Request

from ..composition import ExecutorApplication
from ..contracts import JobRequestV6
from ..errors import ExecutionServiceError, JobNotFoundError
from ..worker_contracts import JobGraphResponse, JobRequestBodyV6, JobStatusResponseV6, NodeDiagnosticsResponse, lease_to_wire


def register_execution_routes(app: FastAPI, application: ExecutorApplication) -> None:
    @app.post(
        "/internal/execution/v6/jobs", status_code=202, response_model=JobStatusResponseV6,
        operation_id="executor_submit_job",
    )
    def submit_job(body: JobRequestBodyV6, request: Request):
        try:
            job_request = JobRequestV6.from_dict(
                body.model_dump(by_alias=True, exclude_none=True)
            )
        except ValueError as error:
            raise ExecutionServiceError(str(error)) from error
        if job_request.project_id != application.project_id:
            raise ExecutionServiceError("PROJECT_MISMATCH")
        return application.control.submit(
            job_request, request.state.traceparent
        ).as_dict()

    @app.get(
        "/internal/execution/v6/jobs/by-request/{requestId}",
        response_model=JobStatusResponseV6,
        operation_id="executor_get_job_by_request",
    )
    def get_by_request(
        request_id: Annotated[str, Path(alias="requestId")],
        project_id: str = Query(alias="projectId"),
    ):
        if project_id != application.project_id:
            raise JobNotFoundError("JOB_NOT_FOUND")
        return application.control.get_by_request(project_id, request_id).as_dict()

    @app.get("/internal/execution/v6/jobs/{jobId}", response_model=JobStatusResponseV6, operation_id="executor_get_job")
    def get_status(job_id: Annotated[str, Path(alias="jobId")]):
        return application.control.get(job_id).as_dict()

    @app.post(
        "/internal/execution/v6/jobs/{jobId}/cancel",
        status_code=202,
        response_model=JobStatusResponseV6,
        operation_id="executor_cancel_job",
    )
    def cancel_job(job_id: Annotated[str, Path(alias="jobId")]):
        return application.control.cancel(job_id).as_dict()

    @app.get("/internal/execution/v6/jobs/{jobId}/nodes", response_model=NodeDiagnosticsResponse, operation_id="executor_get_job_nodes")
    def node_diagnostics(job_id: Annotated[str, Path(alias="jobId")]):
        return lease_to_wire({"nodes": application.control.node_diagnostics(job_id)})


    @app.get(
        "/internal/execution/v6/jobs/{jobId}/graph",
        response_model=JobGraphResponse,
        operation_id="executor_get_job_graph",
    )
    def job_graph(job_id: Annotated[str, Path(alias="jobId")]):
        return application.control.job_graph(job_id)
