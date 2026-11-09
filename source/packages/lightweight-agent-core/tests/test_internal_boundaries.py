from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

from platform_lightweight_agent import (
    FunctionToolExecutor,
    InMemoryRunJournal,
    WorkflowEngine,
)
from platform_lightweight_agent.drivers import OpenAIAgentsDriver, PydanticAIDriver
from platform_lightweight_agent import engine, runtime_contracts, runtime_ports
from platform_lightweight_agent.runtime_services import WorkflowRuntimeServices


def test_async_scheduler_uses_the_explicit_engine_service_boundary() -> None:
    source = (
        Path(__file__).parents[1]
        / "src"
        / "platform_lightweight_agent"
        / "workflow_v3.py"
    ).read_text(encoding="utf-8")
    assert re.search(r"\bengine\._[A-Za-z0-9_]+", source) is None

    engine = WorkflowEngine(InMemoryRunJournal(), FunctionToolExecutor(), {})
    assert isinstance(engine.workflow_services, WorkflowRuntimeServices)
    assert engine.workflow_services.journal is not None


def test_native_sdk_drivers_have_separate_adapter_owners() -> None:
    assert OpenAIAgentsDriver.__module__.endswith(".driver_openai_agents")
    assert PydanticAIDriver.__module__.endswith(".driver_pydantic_ai")


def test_runtime_contracts_and_ports_are_neutral_legacy_reexports() -> None:
    for name in (
        "ActionAuthorization",
        "AgentContext",
        "AgentDriverFailure",
        "CancellationToken",
        "ToolContext",
        "ToolExecutionRequest",
        "ToolExecutionResult",
        "WorkflowRunError",
    ):
        assert getattr(engine, name) is getattr(runtime_contracts, name)
    for name in (
        "ActionPolicy",
        "AgentDriver",
        "AsyncAgentDriver",
        "AsyncToolExecutor",
        "ToolExecutor",
        "ToolRegistrationExecutor",
    ):
        assert getattr(engine, name) is getattr(runtime_ports, name)


def test_importing_a_runtime_port_does_not_import_the_engine_or_sdk() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; "
            "from platform_lightweight_agent import AgentDriver, ToolExecutionResult; "
            "assert AgentDriver.__module__.endswith('.runtime_ports'); "
            "assert ToolExecutionResult.__module__.endswith('.runtime_contracts'); "
            "assert 'platform_lightweight_agent.engine' not in sys.modules; "
            "assert not [name for name in sys.modules "
            "if name.startswith(('anyio', 'jsonschema', 'pydantic', 'agents'))]",
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
