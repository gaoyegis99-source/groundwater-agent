"""Measure actual Agent modes without invoking a model or reading deployment secrets."""
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "packages/workflow-compiler/src"))
sys.path.insert(0, str(ROOT / "kernel/src"))

import httpx  # noqa: E402
from platform_kernel.settings import IntentRouterSettings, KernelSettings  # noqa: E402
from platform_kernel.agent_runtime.memory import ExecutionContextV1, token_count  # noqa: E402
from platform_kernel.agent_runtime.observation_client import ObservationClient  # noqa: E402
from platform_kernel.agent_runtime.provider import build_model_settings  # noqa: E402
from platform_kernel.agent_runtime.context_usage import context_tokens  # noqa: E402
from platform_kernel.agent_runtime.sdk_execution import SDKExecutionDependencies  # noqa: E402
from platform_kernel.agent_runtime.sdk_execution.execution import _build_agent  # noqa: E402
from platform_kernel.agent_runtime.sdk_execution.usage_ports import CallbackActivityLog  # noqa: E402


async def measure(settings: KernelSettings) -> dict:
    available = settings.agent_context_window_tokens - settings.agent_max_output_tokens
    inputs = [{"role": "user", "content": "请分析当前项目的监测数据。"}]
    modes = {}
    async with httpx.AsyncClient(trust_env=False) as client:
        # Build the actual tools/prompts, but invoke neither tools nor a Provider.
        dependencies = SDKExecutionDependencies(
            database=Path("unused-context-measurement.sqlite3"),
            model=None,
            model_settings=build_model_settings(settings),
            settings=settings,
            observation_client=ObservationClient(client),
            agent_slots=asyncio.Semaphore(8),
            activity_log=CallbackActivityLog(lambda _session_id, _activity: None),
        )
        mode_contexts = {
            "DIALOGUE_READ_ONLY": {
                "mode": "DIALOGUE", "dialogue_policy": "READ_ONLY",
            },
            "DIALOGUE_EXECUTION_PROPOSAL": {
                "mode": "DIALOGUE", "dialogue_policy": "EXECUTION_PROPOSAL",
            },
            "WORKFLOW_DRAFT": {
                "mode": "WORKFLOW_DRAFT", "dialogue_policy": None,
            },
        }
        for mode, request_context in mode_contexts.items():
            agent = _build_agent(
                dependencies, ExecutionContextV1(()), request_context,
            )
            total = context_tokens(agent, agent.instructions, inputs)
            if settings.system_prompt not in agent.instructions:
                raise ValueError("DOMAIN_INSTRUCTIONS_NOT_EMBEDDED")
            general_instructions = agent.instructions.replace(
                settings.system_prompt, "", 1,
            )
            modes[mode] = {
                "tool_count": len(agent.tools),
                "instructions_tokens": token_count(agent.instructions),
                "general_instructions_tokens": token_count(general_instructions),
                "domain_instructions_tokens": token_count(settings.system_prompt),
                "tools_tokens": token_count([
                    {"name": item.name, "description": item.description,
                     "input_schema": item.params_json_schema,
                     "output_schema": item.output_json_schema}
                    for item in agent.tools
                ]),
                "output_schema_tokens": 0,
                "input_tokens": token_count(inputs),
                "initial_total_tokens": total,
                "remaining_input_tokens": available - total,
                "fits_input_budget": total <= available,
            }
    return {
        "product": settings.product_id,
        "manifest_checksum": settings.manifest_checksum,
        "tokenizer": "o200k_base",
        "phase": "initial",
        "context_window_tokens": settings.agent_context_window_tokens,
        "max_output_tokens": settings.agent_max_output_tokens,
        "available_input_tokens": available,
        "modes": modes,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("configs", type=Path, nargs="+")
    parser.add_argument("--context-window-tokens", type=int, required=True)
    parser.add_argument("--max-output-tokens", type=int, required=True)
    args = parser.parse_args()
    failed = False
    for path in args.configs:
        settings = KernelSettings.from_config(
            path, data_root=Path("unused-context-measurement"),
            project_id="context-measurement", agent_provider="deepseek",
            agent_protocol="responses",
            agent_base_url="http://unused.invalid/v1", agent_model="context-measurement",
            agent_api_key="unused", agent_context_window_tokens=args.context_window_tokens,
            agent_max_output_tokens=args.max_output_tokens,
            agent_request_timeout_seconds=120, agent_segment_timeout_seconds=300,
            intent_router=IntentRouterSettings.from_values(
                provider="deepseek", protocol="responses",
                base_url="http://unused.invalid/v1", model="unused-intent-router",
                api_key="unused", context_window_tokens=args.context_window_tokens,
                max_output_tokens=min(256, args.context_window_tokens - 1),
                request_timeout_seconds=120, segment_timeout_seconds=300,
            ),
        )
        result = asyncio.run(measure(settings))
        print(json.dumps(result, ensure_ascii=False))
        failed |= any(not mode["fits_input_budget"] for mode in result["modes"].values())
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())
