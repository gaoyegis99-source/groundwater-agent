from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query

from ...agent_runtime.api_projection import agent_run_response, agent_trace_response
from ...composition import PlatformApplication
from ..dependencies import platform_application
from ..schemas import AgentRunResponse, AgentTraceResponse, PROBLEM_RESPONSE, ProviderRequestPage
from ..wire import to_wire


router = APIRouter(responses={401: PROBLEM_RESPONSE, 403: PROBLEM_RESPONSE, 404: PROBLEM_RESPONSE})


@router.get("/api/v12/agent-runs/{agentRunId}", response_model=AgentRunResponse, operation_id="kernel_get_agent_run")
def get_agent_run(
    agent_run_id: Annotated[str, Path(alias="agentRunId")],
    project_id: Annotated[str, Query(alias="projectId")],
    application: PlatformApplication = Depends(platform_application),
):
    return to_wire(
        agent_run_response(application.agent_runtime.get(agent_run_id, project_id))
    )


@router.get("/api/v12/agent-runs/{agentRunId}/trace", response_model=AgentTraceResponse, operation_id="kernel_get_agent_trace")
def get_agent_trace(
    agent_run_id: Annotated[str, Path(alias="agentRunId")],
    project_id: Annotated[str, Query(alias="projectId")],
    application: PlatformApplication = Depends(platform_application),
):
    run = application.agent_runtime.get(agent_run_id, project_id)
    items = application.agent_runtime.trace(agent_run_id, project_id)
    return to_wire(agent_trace_response(run, items))


@router.get("/api/v12/agent-runs/{agentRunId}/provider-requests", response_model=ProviderRequestPage, operation_id="kernel_get_agent_provider_requests")
def get_agent_provider_requests(
    agent_run_id: Annotated[str, Path(alias="agentRunId")],
    project_id: Annotated[str, Query(alias="projectId")],
    application: PlatformApplication = Depends(platform_application),
):
    return to_wire({
        "agent_run_id": agent_run_id,
        "items": list(
            application.agent_runtime.provider_requests(agent_run_id, project_id)
        ),
    })
