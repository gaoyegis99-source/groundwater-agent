from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query

from ...composition import PlatformApplication
from ...workflow_execution.api_projection import workflow_run_response
from ..dependencies import platform_application
from ..schemas import PROBLEM_RESPONSE, WorkflowRunResponse
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
