"""Small explicit entry point for new embedded v4 workflow applications.

Older root-level names remain importable for source compatibility. Advanced
authoring, adapter, inspection, and maintenance contracts live in their
explicit submodules instead of expanding this default entry point.
"""

from importlib import import_module
from typing import TYPE_CHECKING, Any

from ._legacy_exports import EXPORT_MODULE

if TYPE_CHECKING:
    from .drivers import OpenAIAgentsDriver, PydanticAIDriver
    from .engine import (
        AsyncFunctionToolExecutor,
        FunctionToolExecutor,
        WorkflowEngine,
    )
    from .files import FileReference, FileWorkspace
    from .host import assemble_runtime
    from .journal import (
        InMemoryRunJournal,
        RunEvent,
        RunHandle,
        RunJournal,
        RunStatus,
        SQLiteRunJournal,
    )
    from .runtime_contracts import (
        CancellationToken,
        ToolContext,
        ToolExecutionRequest,
        ToolExecutionResult,
    )
    from .runtime_ports import (
        AgentDriver,
        AsyncAgentDriver,
        AsyncToolExecutor,
        ToolExecutor,
    )
    from .spec import (
        AgentDecision,
        AgentToolSpec,
        ApprovalDecision,
        NodeContract,
        ParallelBranch,
        ToolCall,
        ToolReference,
        ValueBinding,
        WorkflowExecutionPolicy,
        WorkflowSpec,
        WorkflowValidationError,
        agent_step,
        approval_step,
        condition_step,
        parallel_step,
        subworkflow_step,
        tool_step,
        workflow,
        workflow_v4,
    )

__all__ = (
    "AgentDecision",
    "AgentDriver",
    "AgentToolSpec",
    "ApprovalDecision",
    "AsyncAgentDriver",
    "AsyncFunctionToolExecutor",
    "AsyncToolExecutor",
    "CancellationToken",
    "FileReference",
    "FileWorkspace",
    "FunctionToolExecutor",
    "InMemoryRunJournal",
    "NodeContract",
    "OpenAIAgentsDriver",
    "ParallelBranch",
    "PydanticAIDriver",
    "RunEvent",
    "RunHandle",
    "RunJournal",
    "RunStatus",
    "SQLiteRunJournal",
    "ToolCall",
    "ToolContext",
    "ToolExecutionRequest",
    "ToolExecutionResult",
    "ToolExecutor",
    "ToolReference",
    "ValueBinding",
    "WorkflowEngine",
    "WorkflowExecutionPolicy",
    "WorkflowSpec",
    "WorkflowValidationError",
    "agent_step",
    "approval_step",
    "assemble_runtime",
    "condition_step",
    "parallel_step",
    "subworkflow_step",
    "tool_step",
    "workflow",
    "workflow_v4",
)


def __getattr__(name: str) -> Any:
    if name not in __all__:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(f".{EXPORT_MODULE[name]}", __package__), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
