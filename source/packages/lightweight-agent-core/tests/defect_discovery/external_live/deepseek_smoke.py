"""Opt-in real DeepSeek smoke: at most 3 transport attempts / USD 0.10.

Only synthetic inputs leave the machine. Never log headers, bodies or secrets.
The report is a bounded smoke, not a model-quality or multi-provider claim.
"""

from __future__ import annotations

import argparse
from decimal import Decimal
from importlib.metadata import version
import json
import os
from pathlib import Path
import time

import anyio
import httpx
from openai import AsyncOpenAI
from pydantic_ai import Agent
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.deepseek import DeepSeekProvider
from platform_lightweight_agent import (
    AgentBudget,
    AgentToolSpec,
    ApprovalDecision,
    LocalToolExecutor,
    NodeContract,
    PydanticAIDriver,
    SQLiteRunJournal,
    ToolRecoveryCapabilities,
    ToolReference,
    ValueBinding,
    WorkflowEngine,
    agent_step,
    workflow_v4,
)

MODEL = "deepseek-flash"
PRICING = "https://api-docs.deepseek.com/quick_start/pricing/"
# Peak, uncached USD / 1M tokens, verified 2026-10-28. Reserve a generous input
# ceiling including protocol overhead; actual synthetic JSON is <= 8192 bytes.
INPUT_PRICE, OUTPUT_PRICE = Decimal("0.30"), Decimal("1.20")
RESERVE = (32768 * INPUT_PRICE + 256 * OUTPUT_PRICE) / 1_000_000
CAP = Decimal("0.10")


class BudgetTransport(httpx.AsyncBaseTransport):
    def __init__(self, root: Path):
        self.delegate = httpx.AsyncHTTPTransport(retries=0)
        self.root, self.attempts, self.usage = root, 0, []
        self.responses: list[dict[str, object]] = []

    async def handle_async_request(self, request):
        payload = json.loads(request.content)
        if (
            request.method != "POST"
            or request.url.host != "api.deepseek.com"
            or request.url.scheme != "https"
            or request.url.path not in {"/chat/completions", "/v1/chat/completions"}
            or payload.get("model") != MODEL
            or payload.get("max_tokens") != 256
            or payload.get("thinking") != {"type": "disabled"}
            or len(request.content) > 8192
        ):
            raise RuntimeError("LIVE_REQUEST_OUTSIDE_APPROVED_SCOPE")
        if self.attempts >= 3 or (self.attempts + 1) * RESERVE > CAP:
            raise RuntimeError("LIVE_REQUEST_BUDGET_EXHAUSTED")
        self.attempts += 1
        # Exclusive per-attempt receipt exists before sending, even on timeout.
        with (self.root / f"attempt-{self.attempts}.json").open(
            "x", encoding="utf-8"
        ) as receipt:
            json.dump({"attempt": self.attempts, "reserved_usd": str(RESERVE)}, receipt)
        started = time.perf_counter()
        try:
            response = await self.delegate.handle_async_request(request)
            await response.aread()
        except Exception as error:
            self.responses.append(
                {
                    "attempt": self.attempts,
                    "http_status": None,
                    "latency_ms": round((time.perf_counter() - started) * 1000, 1),
                    "return_kind": "transport_error",
                    "error_type": type(error).__name__,
                }
            )
            raise
        try:
            response_body = json.loads(response.content)
        except (TypeError, ValueError):
            response_body = {}
        choices = response_body.get("choices", [])
        first_choice = choices[0] if choices and isinstance(choices[0], dict) else {}
        message = first_choice.get("message", {})
        if not isinstance(message, dict):
            message = {}
        tool_calls = message.get("tool_calls")
        tool_call_count = len(tool_calls) if isinstance(tool_calls, list) else 0
        self.responses.append(
            {
                "attempt": self.attempts,
                "http_status": response.status_code,
                "latency_ms": round((time.perf_counter() - started) * 1000, 1),
                "return_kind": (
                    "tool_calls"
                    if tool_call_count
                    else "final_text"
                    if isinstance(message.get("content"), str)
                    else "none"
                ),
                "tool_call_count": tool_call_count,
                "finish_reason": first_choice.get("finish_reason")
                if first_choice.get("finish_reason")
                in {"stop", "tool_calls", "length", "content_filter"}
                else None,
            }
        )
        if response.is_success:
            usage = response_body.get("usage", {})
            prompt, completion = (
                usage.get("prompt_tokens"),
                usage.get("completion_tokens"),
            )
            if (
                not isinstance(prompt, int)
                or not isinstance(completion, int)
                or not 0 <= prompt <= 32768
                or not 0 <= completion <= 256
            ):
                raise RuntimeError("LIVE_USAGE_CANNOT_BE_BOUNDED")
            self.usage.append(
                {
                    "input_tokens": prompt,
                    "output_tokens": completion,
                    "upper_cost_usd": str(
                        (prompt * INPUT_PRICE + completion * OUTPUT_PRICE) / 1_000_000
                    ),
                }
            )
        return response

    async def aclose(self):
        await self.delegate.aclose()


def schema(properties):
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


async def collect(root):
    started = time.perf_counter()
    transport = BudgetTransport(root)
    reference = ToolReference("smoke.echo", "1", "sha256:" + "8" * 64)
    tool_input, output, empty = (
        schema({"value": {"type": "string"}}),
        schema({"answer": {"type": "string"}}),
        schema({}),
    )
    tool = AgentToolSpec(
        "echo",
        reference,
        "Return an exact synthetic string. Request value alpha.",
        tool_input,
        output,
        capabilities=ToolRecoveryCapabilities(external_side_effects=False),
    )
    definition = workflow_v4(
        "deepseek-smoke",
        "1",
        [
            agent_step(
                "agent",
                "deepseek",
                max_turns=3,
                tools=(tool,),
                budget=AgentBudget(
                    max_model_requests=3, max_tool_calls=1, max_context_bytes=8192
                ),
                contract=NodeContract(empty, output, {}),
            )
        ],
        input_schema=empty,
        output_schema=output,
        output_bindings={"/answer": ValueBinding("step_output", "/answer", "agent")},
    )
    calls = []
    executor = LocalToolExecutor()

    def echo(arguments):
        calls.append(dict(arguments))
        return {"answer": "echo:" + arguments["value"]}

    executor.register(reference, echo)
    instructions = "Call echo exactly once with value alpha. After its approved result is available, return structured answer equal to that tool's answer. Do not call echo again."
    result, waiting, failure = None, None, None
    async with httpx.AsyncClient(
        transport=transport, timeout=30, trust_env=False, follow_redirects=False
    ) as http:
        client = AsyncOpenAI(
            api_key=os.environ["DEEPSEEK_API_KEY"],
            base_url="https://api.deepseek.com",
            max_retries=0,
            http_client=http,
        )

        def driver():
            model = OpenAIChatModel(
                MODEL, provider=DeepSeekProvider(openai_client=client)
            )
            agent = Agent(
                model,
                instructions=instructions,
                model_settings={
                    # DeepSeek documents max_tokens; this SDK otherwise maps
                    # its generic limit to max_completion_tokens.
                    "extra_body": {"thinking": {"type": "disabled"}, "max_tokens": 256},
                },
            )
            return PydanticAIDriver(
                agent=agent, instructions=instructions, model_name=MODEL
            )

        try:
            journal_path = root / "runs.sqlite"
            engine = WorkflowEngine(
                SQLiteRunJournal(journal_path), executor, {"deepseek": driver()}
            )
            waiting = await engine.run_async(definition, {}, run_id="deepseek-smoke")
            if waiting.status == "WAITING_APPROVAL" and not calls:
                approvals = tuple(
                    ApprovalDecision(key, True)
                    for key in waiting.state["v3"]["pending_approvals"]
                )
                # Reconstruct runtime/journal at the approval boundary, retaining
                # the original frozen driver binding and execution identity.
                engine = WorkflowEngine(
                    SQLiteRunJournal(journal_path), executor, {"deepseek": driver()}
                )
                result = await engine.resume_async(waiting.handle, approvals=approvals)
            else:
                result = waiting
        except Exception as error:
            failure = type(error).__name__  # Never expose provider exception bodies.
    passed = (
        waiting is not None
        and waiting.status == "WAITING_APPROVAL"
        and result is not None
        and result.status == "SUCCEEDED"
        and result.execution_id == waiting.execution_id
        and calls == [{"value": "alpha"}]
        and result.state["v3"]["output"] == {"answer": "echo:alpha"}
    )
    return {
        "schema_version": "v6-real-provider-evidence-v1",
        "result": "PASS" if passed else "FAIL",
        "provider": "DeepSeek",
        "model": MODEL,
        "sdk_versions": {
            "pydantic-ai-slim": version("pydantic-ai-slim"),
            "openai": version("openai"),
            "httpx": version("httpx"),
        },
        "pricing_source": PRICING,
        "max_requests": 3,
        "max_cost_usd": str(CAP),
        "transport_attempts": transport.attempts,
        "reserved_cost_usd": str(transport.attempts * RESERVE),
        "usage": transport.usage,
        "responses": transport.responses,
        "latency_ms": round((time.perf_counter() - started) * 1000, 1),
        "cost_note": "peak uncached upper estimate, not an invoice; timeouts retain their full reservation",
        "results": [
            {
                "case": "M04",
                "variant": "real_provider_smoke",
                "contract_result": "PASS" if passed else "FAIL",
                "approval_persisted": waiting is not None
                and waiting.status == "WAITING_APPROVAL",
                "status": result.status if result else None,
                "failure_code": (result.state.get("failure") or {}).get("code")
                if result
                else None,
                "tool_calls": len(calls),
                "error_type": failure,
            }
        ],
        "limitations": [
            "one model and one synthetic scenario",
            "not a quality or load benchmark",
            "no cross-driver checkpoint migration",
        ],
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--allow-model-requests", action="store_true")
    parser.add_argument("--evidence-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not args.allow_model_requests or not os.environ.get("DEEPSEEK_API_KEY"):
        raise SystemExit(
            "Explicit authorization and a local DEEPSEEK_API_KEY are required"
        )
    args.evidence_root.mkdir(parents=True, exist_ok=False)
    report = anyio.run(collect, args.evidence_root)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report))
    raise SystemExit(0 if report["result"] == "PASS" else 1)
