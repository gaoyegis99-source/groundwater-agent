"""Collect repeatable V6 cost curves without inventing release thresholds."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import statistics
import subprocess
import sys
import time
from typing import Any

import anyio
from platform_lightweight_agent import (
    HostRuntime,
    HostRuntimeLimits,
    InMemoryRunJournal,
    NodeContract,
    RuntimeAssembly,
    ToolExecutionResult,
    ToolRecoveryCapabilities,
    ToolReference,
    ValueBinding,
    workflow,
    tool_step,
)


PACKAGE_ROOT = Path(__file__).resolve().parents[3]
OVERHEAD_SCRIPT = PACKAGE_ROOT / "scripts" / "measure_v3_v4_overhead.py"
TOOL = ToolReference("probe.cost", "1", "sha256:" + "f" * 64)
EMPTY = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "properties": {},
    "required": [],
    "additionalProperties": False,
}
OUTPUT = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "properties": {"value": {"type": "integer"}},
    "required": ["value"],
    "additionalProperties": False,
}


def _summary(values: list[float]) -> dict[str, object]:
    ordered = sorted(values)
    p95_index = min(len(ordered) - 1, max(0, int(len(ordered) * 0.95)))
    return {
        "samples": len(values),
        "p50": round(statistics.median(ordered), 3),
        "p95": round(ordered[p95_index], 3),
        "min": round(ordered[0], 3),
        "max": round(ordered[-1], 3),
        "raw": [round(item, 3) for item in values],
    }


def _step_curves(samples: int) -> list[dict[str, object]]:
    by_step: dict[int, dict[str, list[float]]] = {}
    environment = dict(os.environ)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    for _sample in range(samples):
        completed = subprocess.run(
            [sys.executable, str(OVERHEAD_SCRIPT), "--skip-allocation-probe"],
            cwd=PACKAGE_ROOT,
            env=environment,
            text=True,
            capture_output=True,
            timeout=180,
            check=False,
        )
        if completed.returncode != 0:
            raise RuntimeError(
                f"overhead sample failed ({completed.returncode}): "
                f"{completed.stderr[-2000:]}"
            )
        report = json.loads(completed.stdout)
        for item in report["measurements"]:
            step = int(item["steps"])
            target = by_step.setdefault(
                step,
                {"preflight_cold_ms": [], "preflight_hot_ms": [], "execution_ms": []},
            )
            target["preflight_cold_ms"].append(float(item["preflight_cold_ms"]))
            target["preflight_hot_ms"].append(float(item["preflight_hot_ms"]))
            target["execution_ms"].append(float(item["local_execution_ms"]))
    return [
        {
            "steps": step,
            "preflight_cold_ms": _summary(values["preflight_cold_ms"]),
            "preflight_hot_ms": _summary(values["preflight_hot_ms"]),
            "local_execution_ms": _summary(values["execution_ms"]),
        }
        for step, values in sorted(by_step.items())
    ]


def _workflow():  # type: ignore[no-untyped-def]
    return workflow(
        "cost-concurrency",
        "1",
        (
            tool_step(
                "effect",
                TOOL,
                approval_required=False,
                contract=NodeContract(EMPTY, OUTPUT, {}),
                capabilities=ToolRecoveryCapabilities(external_side_effects=False),
            ),
        ),
        schema_version="v6-light-workflow-v4",
        input_schema=EMPTY,
        output_schema=OUTPUT,
        output_bindings={
            "/value": ValueBinding("step_output", "/value", "effect")
        },
    )


class TimedExecutor:
    def __init__(self) -> None:
        self.active = 0
        self.peak = 0
        self.lock = anyio.Lock()

    async def execute_async(self, request: Any, cancellation: Any) -> ToolExecutionResult:
        del cancellation
        async with self.lock:
            self.active += 1
            self.peak = max(self.peak, self.active)
        try:
            await anyio.sleep(0.01)
            return ToolExecutionResult(request.action_id, True, {"value": 1})
        finally:
            async with self.lock:
                self.active -= 1

    async def reconcile_async(self, request: Any) -> None:
        del request
        return None


async def _one_concurrency_sample(limit: int, sample: int) -> dict[str, object]:
    executor = TimedExecutor()
    host = HostRuntime(
        RuntimeAssembly(InMemoryRunJournal(), executor),
        limits=HostRuntimeLimits(
            max_active_runs=8,
            max_queued_runs=8,
            max_tool_calls=limit,
            max_model_requests=1,
        ),
    )
    statuses: list[str] = []
    started = time.perf_counter_ns()
    try:
        async with anyio.create_task_group() as group:

            async def invoke(index: int) -> None:
                result = await host.run_async(
                    _workflow(), {}, run_id=f"cost-{limit}-{sample}-{index}"
                )
                statuses.append(result.status)

            for index in range(8):
                group.start_soon(invoke, index)
    finally:
        host.close(mode="wait")
    elapsed = (time.perf_counter_ns() - started) / 1_000_000
    return {
        "elapsed_ms": elapsed,
        "peak": executor.peak,
        "all_succeeded": statuses == ["SUCCEEDED"] * 8,
    }


async def _concurrency_curves(samples: int) -> list[dict[str, object]]:
    results: list[dict[str, object]] = []
    for limit in (1, 2, 4, 8):
        observations = [
            await _one_concurrency_sample(limit, sample)
            for sample in range(samples)
        ]
        results.append(
            {
                "configured_limit": limit,
                "runs_per_sample": 8,
                "elapsed_ms": _summary(
                    [float(item["elapsed_ms"]) for item in observations]
                ),
                "observed_peaks": [item["peak"] for item in observations],
                "bounded": all(
                    1 <= int(item["peak"]) <= limit for item in observations
                ),
                "all_succeeded": all(
                    bool(item["all_succeeded"]) for item in observations
                ),
            }
        )
    return results


def _delivery(path: Path | None) -> dict[str, object] | None:
    if path is None:
        return None
    content = path.read_bytes()
    parsed = json.loads(content)
    return {
        "path": str(path),
        "sha256": "sha256:" + hashlib.sha256(content).hexdigest(),
        "measurement": parsed.get("measurement"),
        "install_size_bytes": parsed.get("install_size_bytes"),
        "cold_import_ms": parsed.get("cold_import_ms"),
        "cli_check_ms": parsed.get("cli_check_ms"),
        "idle_rss_bytes": parsed.get("idle_rss_bytes"),
        "acceptance": parsed.get("acceptance"),
    }


async def collect(samples: int, delivery: Path | None) -> dict[str, object]:
    step_curves = await anyio.to_thread.run_sync(_step_curves, samples)
    concurrency = await _concurrency_curves(samples)
    requirements = {
        "thirty_or_more_samples": samples >= 30,
        "step_counts_complete": [item["steps"] for item in step_curves]
        == [1, 10, 40, 100],
        "concurrency_limits_complete": [
            item["configured_limit"] for item in concurrency
        ]
        == [1, 2, 4, 8],
        "concurrency_is_bounded": all(item["bounded"] for item in concurrency),
        "all_concurrency_runs_succeeded": all(
            item["all_succeeded"] for item in concurrency
        ),
        "delivery_measurement_attached": delivery is not None,
    }
    return {
        "schema_version": "v6-defect-cost-curves-v1",
        "result": "PASS" if all(requirements.values()) else "FAIL",
        "results": [
            {
                "case": "O01",
                "variant": "installation_step_and_concurrency_curves",
                "contract_result": (
                    "PASS" if all(requirements.values()) else "FAIL"
                ),
                "requirements": requirements,
                "thresholds": None,
                "step_curves": step_curves,
                "concurrency_curves": concurrency,
                "delivery_baseline": _delivery(delivery),
            }
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--samples", type=int, default=30)
    parser.add_argument("--delivery-report", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.samples < 1:
        raise SystemExit("--samples must be positive")
    report = anyio.run(collect, args.samples, args.delivery_report)
    payload = json.dumps(report, indent=2, sort_keys=True) + "\n"
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(payload, encoding="utf-8")
    print(payload, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
