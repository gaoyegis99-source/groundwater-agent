"""Measure exact UTF-8 byte boundaries across v3/v4 budget surfaces."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

import anyio
from platform_lightweight_agent import (
    AgentBudget,
    AgentDecision,
    AgentToolSpec,
    ApprovalDecision,
    InMemoryRunJournal,
    LocalToolExecutor,
    NodeContract,
    ScriptedAgentDriver,
    ToolCall,
    ToolRecoveryCapabilities,
    ToolReference,
    ValueBinding,
    WorkflowEngine,
    WorkflowExecutionPolicy,
    agent_step,
    tool_step,
    workflow,
)
from platform_lightweight_agent.spec import canonical_json


TOOL = ToolReference("probe.boundary", "1", "sha256:" + "4" * 64)
SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "properties": {"blob": {"type": "string"}},
    "required": ["blob"],
    "additionalProperties": False,
}
EMPTY = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "properties": {},
    "required": [],
    "additionalProperties": False,
}


def _payload(size: int) -> dict[str, str]:
    base = len(canonical_json({"blob": ""}).encode("utf-8"))
    if size < base + 3:
        raise ValueError("boundary payload must leave room for a multibyte value")
    value = "汉" + "x" * (size - base - 3)
    result = {"blob": value}
    if len(canonical_json(result).encode("utf-8")) != size:
        raise AssertionError("payload byte calibration failed")
    return result


class EchoExecutor:
    def __init__(self, output: dict[str, str] | None = None) -> None:
        self.output = output
        self.calls = 0

    async def execute_async(self, request: Any, cancellation: Any):  # type: ignore[no-untyped-def]
        del cancellation
        self.calls += 1
        from platform_lightweight_agent import ToolExecutionResult

        return ToolExecutionResult(
            request.action_id,
            True,
            self.output if self.output is not None else dict(request.arguments),
        )

    async def reconcile_async(self, request: Any) -> None:
        del request
        return None


def _tool_spec(
    *,
    max_input: int = 1_024,
    max_output: int = 1_024,
    retained: int | None = None,
):  # type: ignore[no-untyped-def]
    return workflow(
        "boundary-tool",
        "1",
        (
            tool_step(
                "echo",
                TOOL,
                approval_required=False,
                contract=NodeContract(
                    SCHEMA,
                    SCHEMA,
                    {"/blob": ValueBinding("workflow_input", "/blob")},
                    max_input_bytes=max_input,
                    max_output_bytes=max_output,
                ),
                capabilities=ToolRecoveryCapabilities(external_side_effects=False),
            ),
        ),
        schema_version="v6-light-workflow-v4",
        input_schema=SCHEMA,
        output_schema=SCHEMA,
        output_bindings={
            "/blob": ValueBinding("step_output", "/blob", "echo")
        },
        execution_policy=WorkflowExecutionPolicy(
            max_retained_state_bytes=retained
        ),
    )


async def _node_boundaries() -> list[dict[str, object]]:
    results: list[dict[str, object]] = []
    for surface in ("input", "output"):
        for size in (63, 64, 65):
            payload = _payload(size)
            executor = EchoExecutor(payload if surface == "output" else None)
            spec = _tool_spec(
                max_input=64 if surface == "input" else 1_024,
                max_output=64 if surface == "output" else 1_024,
            )
            snapshot = await WorkflowEngine(
                InMemoryRunJournal(), executor, {}
            ).run_async(spec, payload)
            expected_success = size <= 64
            expected_code = (
                "STEP_INPUT_TOO_LARGE"
                if surface == "input"
                else "STEP_OUTPUT_TOO_LARGE"
            )
            requirements = {
                "byte_size_is_exact": len(
                    canonical_json(payload).encode("utf-8")
                )
                == size,
                "status_matches_boundary": snapshot.status
                == ("SUCCEEDED" if expected_success else "BLOCKED"),
                "oversize_code_is_stable": expected_success
                or snapshot.state["failure"]["code"] == expected_code,
                "input_oversize_does_not_dispatch": surface != "input"
                or expected_success
                or executor.calls == 0,
                "output_oversize_does_not_retry": surface != "output"
                or expected_success
                or executor.calls == 1,
            }
            results.append(
                {
                    "case": "W02",
                    "variant": f"node_{surface}_{size}_bytes",
                    "contract_result": (
                        "PASS" if all(requirements.values()) else "FAIL"
                    ),
                    "requirements": requirements,
                    "status": snapshot.status,
                    "failure": snapshot.state.get("failure"),
                    "tool_calls": executor.calls,
                }
            )
    return results


def _agent_spec(text: str, context_limit: int):  # type: ignore[no-untyped-def]
    output = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "properties": {"message": {"type": "string"}},
        "required": ["message"],
        "additionalProperties": False,
    }
    return workflow(
        "boundary-agent-context",
        "1",
        (
            agent_step(
                "agent",
                "primary",
                tools=(),
                budget=AgentBudget(max_context_bytes=context_limit),
                contract=NodeContract(
                    SCHEMA,
                    output,
                    {"/blob": ValueBinding("workflow_input", "/blob")},
                ),
            ),
        ),
        schema_version="v6-light-workflow-v4",
        input_schema=SCHEMA,
        output_schema=output,
        output_bindings={
            "/message": ValueBinding("step_output", "/message", "agent")
        },
    )


async def _agent_context_boundaries() -> list[dict[str, object]]:
    payload = _payload(64)
    decision = AgentDecision("done", output={"message": "完成"})
    baseline = await WorkflowEngine(
        InMemoryRunJournal(),
        LocalToolExecutor(),
        {"primary": ScriptedAgentDriver([decision])},
    ).run_async(_agent_spec(payload["blob"], 1_000_000), payload)
    context_size = int(baseline.state["agents"]["agent"]["usage"]["context_bytes"])
    results: list[dict[str, object]] = []
    for delta in (-1, 0, 1):
        limit = context_size + delta
        driver = ScriptedAgentDriver([decision])
        snapshot = await WorkflowEngine(
            InMemoryRunJournal(), LocalToolExecutor(), {"primary": driver}
        ).run_async(_agent_spec(payload["blob"], limit), payload)
        expected_success = delta >= 0
        requirements = {
            "status_matches_boundary": snapshot.status
            == ("SUCCEEDED" if expected_success else "BLOCKED"),
            "oversize_code_is_stable": expected_success
            or snapshot.state["failure"]["code"] == "AGENT_CONTEXT_LIMIT",
            "measured_context_uses_utf8_payload": "汉" in payload["blob"],
        }
        results.append(
            {
                "case": "W02",
                "variant": f"agent_context_limit_{delta:+d}",
                "contract_result": (
                    "PASS" if all(requirements.values()) else "FAIL"
                ),
                "requirements": requirements,
                "context_bytes": context_size,
                "limit": limit,
                "status": snapshot.status,
            }
        )
    return results


def _agent_tool_spec(output_limit: int):  # type: ignore[no-untyped-def]
    final = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "properties": {"message": {"type": "string"}},
        "required": ["message"],
        "additionalProperties": False,
    }
    tool = AgentToolSpec(
        "echo",
        TOOL,
        "Return one exact record.",
        EMPTY,
        SCHEMA,
        capabilities=ToolRecoveryCapabilities(external_side_effects=False),
    )
    return workflow(
        "boundary-agent-tool",
        "1",
        (
            agent_step(
                "agent",
                "primary",
                tools=(tool,),
                budget=AgentBudget(max_tool_output_bytes=output_limit),
                contract=NodeContract(EMPTY, final, {}),
            ),
        ),
        schema_version="v6-light-workflow-v4",
        input_schema=EMPTY,
        output_schema=final,
        output_bindings={
            "/message": ValueBinding("step_output", "/message", "agent")
        },
    )


async def _agent_tool_boundaries() -> list[dict[str, object]]:
    payload = _payload(64)
    results: list[dict[str, object]] = []
    for limit in (63, 64, 65):
        driver = ScriptedAgentDriver(
            [
                AgentDecision("", (ToolCall(TOOL, {}),)),
                AgentDecision("done", output={"message": "完成"}),
            ]
        )
        executor = LocalToolExecutor()
        calls: list[dict[str, object]] = []
        executor.register(
            TOOL, lambda arguments: calls.append(arguments) or payload
        )
        engine = WorkflowEngine(
            InMemoryRunJournal(), executor, {"primary": driver}
        )
        waiting = await engine.run_async(_agent_tool_spec(limit), {})
        approval_id = next(iter(waiting.state["v3"]["pending_approvals"]))
        snapshot = await engine.resume_async(
            waiting.run_id,
            approvals=(ApprovalDecision(approval_id, True),),
        )
        expected_success = limit >= 64
        requirements = {
            "status_matches_boundary": snapshot.status
            == ("SUCCEEDED" if expected_success else "BLOCKED"),
            "oversize_code_is_stable": expected_success
            or snapshot.state["failure"]["code"]
            == "AGENT_TOOL_OUTPUT_TOO_LARGE",
            "tool_executes_once": len(calls) == 1,
            "payload_is_exact_utf8_bytes": len(
                canonical_json(payload).encode("utf-8")
            )
            == 64,
        }
        results.append(
            {
                "case": "W02",
                "variant": f"agent_tool_output_limit_{limit}",
                "contract_result": (
                    "PASS" if all(requirements.values()) else "FAIL"
                ),
                "requirements": requirements,
                "status": snapshot.status,
                "tool_calls": len(calls),
            }
        )
    return results


async def _retained_state_boundaries() -> list[dict[str, object]]:
    # Retained state deliberately includes lease metadata.  ``time.time()``
    # serializes to a variable-width float, so calibrating with one execution
    # and checking a second execution can move the exact boundary by a byte.
    # Freeze only this probe's runtime clock so every trial measures the same
    # logical state; production byte accounting remains unchanged.
    with patch(
        "platform_lightweight_agent.workflow_v3.time.time",
        return_value=1_700_000_000.0,
    ):
        return await _retained_state_boundaries_at_fixed_time()


async def _retained_state_boundaries_at_fixed_time() -> list[dict[str, object]]:
    payload = _payload(64)
    # The budget applies to every transition, so the threshold is the peak
    # retained state rather than the smaller terminal snapshot.  Find the
    # exact public success boundary with a bounded binary search.
    low, high = 1, 9_999
    while low < high:
        candidate = (low + high) // 2
        try:
            observed = await WorkflowEngine(
                InMemoryRunJournal(), EchoExecutor(), {}
            ).run_async(_tool_spec(retained=candidate), payload)
            succeeded = observed.status == "SUCCEEDED"
        except Exception:  # noqa: BLE001 - below initial retained boundary
            succeeded = False
        if succeeded:
            high = candidate
        else:
            low = candidate + 1
    exact = low
    results: list[dict[str, object]] = []
    for delta in (-1, 0, 1):
        limit = exact + delta
        snapshot = await WorkflowEngine(
            InMemoryRunJournal(), EchoExecutor(), {}
        ).run_async(_tool_spec(retained=limit), payload)
        expected_success = delta >= 0
        requirements = {
            "status_matches_boundary": snapshot.status
            == ("SUCCEEDED" if expected_success else "BLOCKED"),
            "oversize_code_is_stable": expected_success
            or snapshot.state["failure"]["code"]
            == "RUN_RETAINED_STATE_TOO_LARGE",
            "binary_search_found_minimum_success": delta != -1
            or snapshot.status != "SUCCEEDED",
        }
        results.append(
            {
                "case": "W02",
                "variant": f"retained_state_limit_{delta:+d}",
                "contract_result": (
                    "PASS" if all(requirements.values()) else "FAIL"
                ),
                "requirements": requirements,
                "limit": limit,
                "retained_size": snapshot._retained_size_bytes(),
                "minimum_success_limit": exact,
                "status": snapshot.status,
            }
        )
    return results


async def collect() -> dict[str, object]:
    results = await _node_boundaries()
    results.extend(await _retained_state_boundaries())
    results.extend(await _agent_context_boundaries())
    results.extend(await _agent_tool_boundaries())
    return {
        "schema_version": "v6-exact-byte-boundary-evidence-v1",
        "result": (
            "FAIL"
            if any(item["contract_result"] == "FAIL" for item in results)
            else "PASS"
        ),
        "results": results,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        report = anyio.run(collect)
        exit_code = 0
    except Exception as error:  # noqa: BLE001 - retain boundary infrastructure error
        report = {
            "schema_version": "v6-exact-byte-boundary-evidence-v1",
            "result": "ERROR",
            "results": [
                {
                    "case": "W02",
                    "variant": "exact_byte_boundary_matrix",
                    "contract_result": "ERROR",
                    "error_type": type(error).__name__,
                    "error": str(error),
                }
            ],
        }
        exit_code = 1
    payload = json.dumps(report, indent=2, sort_keys=True) + "\n"
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(payload, encoding="utf-8")
    print(payload, end="")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
