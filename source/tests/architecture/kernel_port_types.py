"""Static checks that the wired Kernel owners satisfy application ports."""

from platform_kernel.agent_runtime.owner import AgentRuntimeOwner
from platform_kernel.application.ports import AgentRuntimePort, WorkflowExecutionPort
from platform_kernel.workflow_execution.owner import WorkflowExecutionOwner


def check_owner_ports(
    agent: AgentRuntimeOwner,
    workflow: WorkflowExecutionOwner,
) -> tuple[AgentRuntimePort, WorkflowExecutionPort]:
    return agent, workflow
