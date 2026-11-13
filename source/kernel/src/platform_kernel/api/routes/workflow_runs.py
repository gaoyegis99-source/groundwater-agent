from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query

from ...composition import PlatformApplication
from ...workflow_execution.api_projection import (
    WorkflowGraphResponse,
    WorkflowNodeDiagnosticsResponse,
    workflow_run_response,
)
from ..dependencies import platform_application
from ..schemas import PROBLEM_RESPONSE, WorkflowPublicationResponse, WorkflowRunResponse
from ..wire import to_wire


router = APIRouter(responses={401: PROBLEM_RESPONSE, 403: PROBLEM_RESPONSE, 404: PROBLEM_RESPONSE})


@router.get("/api/v12/workflow-runs/{runId}", response_model=WorkflowRunResponse, operation_id="kernel_get_workflow_run")
def get_run(
    run_id: Annotated[str, Path(alias="runId")],
    project_id: Annotated[str, Query(alias="projectId")],
    application: PlatformApplication = Depends(platform_application),
):
    return to_wire(
        workflow_run_response(application.workflow_execution.get(project_id, run_id))
    )


@router.get(
    "/api/v12/workflow-runs/{runId}/publication",
    response_model=WorkflowPublicationResponse,
    responses={503: PROBLEM_RESPONSE},
    operation_id="kernel_get_workflow_run_publication",
)
async def get_publication(
    run_id: Annotated[str, Path(alias="runId")],
    project_id: Annotated[str, Query(alias="projectId")],
    application: PlatformApplication = Depends(platform_application),
):
    return await application.workflow_execution.run_publication(project_id, run_id)


@router.get(
    "/api/v12/workflow-runs/{runId}/nodes",
    response_model=WorkflowNodeDiagnosticsResponse,
    responses={503: PROBLEM_RESPONSE},
    operation_id="kernel_get_workflow_run_nodes",
)
async def get_nodes(
    run_id: Annotated[str, Path(alias="runId")],
    project_id: Annotated[str, Query(alias="projectId")],
    application: PlatformApplication = Depends(platform_application),
):
    return await application.workflow_execution.node_diagnostics(project_id, run_id)


@router.get(
    "/api/v12/workflow-runs/{runId}/graph",
    response_model=WorkflowGraphResponse,
    operation_id="kernel_get_workflow_run_graph",
)
async def get_graph(
    run_id: Annotated[str, Path(alias="runId")],
    project_id: Annotated[str, Query(alias="projectId")],
    application: PlatformApplication = Depends(platform_application),
):
    return await application.workflow_execution.run_graph(project_id, run_id)
