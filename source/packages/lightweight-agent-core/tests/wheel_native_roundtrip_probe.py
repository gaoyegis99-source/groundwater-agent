"""Run the same native Agent approval round-trip from one installed SDK wheel.

This file is executed from a directory outside the source tree. Its script
directory has no importable product package; the wheel is the only runtime.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
import socket
import sys

import platform_lightweight_agent as installed
from platform_lightweight_agent import (
    AgentBudget,
    AgentToolSpec,
    ApprovalDecision,
    LocalToolExecutor,
    NodeContract,
    OpenAIAgentsDriver,
    PydanticAIDriver,
    SQLiteRunJournal,
    ToolRecoveryCapabilities,
    ToolReference,
    ValueBinding,
    WorkflowEngine,
    agent_step,
    workflow_v4,
)


def _schema(properties: dict[str, object]) -> dict[str, object]:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


def _driver(adapter: str, phase: str):
    if adapter == "pydantic-ai":
        from pydantic_ai import Agent
        from pydantic_ai.messages import ModelResponse, ToolCallPart
        from pydantic_ai.models.function import FunctionModel

        def respond(messages, info):
            del messages
            if phase == "tool":
                assert [tool.name for tool in info.function_tools] == ["echo"]
                return ModelResponse(
                    parts=[
                        ToolCallPart(
                            "echo", {"value": "alpha"}, tool_call_id="wheel-tool"
                        )
                    ]
                )
            assert info.function_tools == []
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        info.output_tools[0].name,
                        {"answer": "echo:alpha"},
                        tool_call_id="wheel-final",
                    )
                ]
            )

        return PydanticAIDriver(agent=Agent(FunctionModel(respond)))

    import agents
    from agents.testing import (
        ModelStep,
        ScriptedModel,
        assistant_message,
        function_call,
    )

    agents.set_tracing_disabled(True)
    output = (
        (function_call("echo", {"value": "alpha"}, call_id="wheel-tool"),)
        if phase == "tool"
        else (assistant_message('{"answer":"echo:alpha"}'),)
    )
    return OpenAIAgentsDriver(
        agent=agents.Agent(
            name="wheel-native", model=ScriptedModel([ModelStep(output=output)])
        )
    )


async def _roundtrip(adapter: str, path: Path) -> None:
    empty = _schema({})
    tool_input = _schema({"value": {"type": "string"}})
    output = _schema({"answer": {"type": "string"}})
    reference = ToolReference("wheel.echo", "1", "sha256:" + "7" * 64)
    definition = workflow_v4(
        "wheel-native-roundtrip",
        "1",
        [
            agent_step(
                "agent",
                "native",
                max_turns=2,
                tools=(
                    AgentToolSpec(
                        "echo",
                        reference,
                        "Return the synthetic value.",
                        tool_input,
                        output,
                        capabilities=ToolRecoveryCapabilities(
                            external_side_effects=False
                        ),
                    ),
                ),
                budget=AgentBudget(max_model_requests=3, max_tool_calls=1),
                contract=NodeContract(empty, output, {}),
            )
        ],
        input_schema=empty,
        output_schema=output,
        output_bindings={"/answer": ValueBinding("step_output", "/answer", "agent")},
    )
    calls: list[dict[str, object]] = []

    def engine(phase: str) -> WorkflowEngine:
        executor = LocalToolExecutor()

        def echo(arguments):
            calls.append(dict(arguments))
            return {"answer": "echo:" + arguments["value"]}

        executor.register(reference, echo)
        return WorkflowEngine(
            SQLiteRunJournal(path), executor, {"native": _driver(adapter, phase)}
        )

    waiting = await engine("tool").run_async(
        definition, {}, run_id=f"wheel-native-{adapter}"
    )
    assert waiting.status == "WAITING_APPROVAL", waiting.status
    assert calls == []
    approval_id = next(iter(waiting.state["v3"]["pending_approvals"]))
    completed = await engine("final").resume_async(
        waiting.handle, approvals=(ApprovalDecision(approval_id, True),)
    )
    assert completed.status == "SUCCEEDED", completed.status
    assert completed.state["v3"]["output"] == {"answer": "echo:alpha"}
    assert calls == [{"value": "alpha"}]
    assert completed.execution_id == waiting.execution_id
    durable = SQLiteRunJournal(path).get(completed.run_id)
    assert durable is not None and durable.status == "SUCCEEDED"
    serialized = json.dumps(durable.to_mapping())
    assert "ModelResponse" not in serialized and "RunResult" not in serialized


def main() -> None:
    assert installed.__file__ is not None and "site-packages" in installed.__file__

    def deny_network(*_args, **_kwargs):
        raise AssertionError("SDK attempted network I/O in offline wheel test")

    socket.socket.connect = deny_network
    asyncio.run(_roundtrip(sys.argv[1], Path(sys.argv[2])))
    print("ok")


if __name__ == "__main__":
    main()
