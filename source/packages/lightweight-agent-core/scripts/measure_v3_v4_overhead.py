"""Measure V3/V4 definition compilation and local execution overhead.

Run after installing the workflow extra:

    uv run --extra workflow python scripts/measure_v3_v4_overhead.py

The report is intentionally a measurement aid, not a benchmark gate.  It
uses the real AnyIO/JSON-Schema workflow core, a bounded number of tiny local
tools, and no model provider, Web server, or external service.
"""

from __future__ import annotations

import argparse
import gc
import json
import platform
import sys
import time
import tracemalloc
from pathlib import Path
from typing import Any

# Allow the repository helper to run before the package itself is installed.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from platform_lightweight_agent import (
    InMemoryRunJournal,
    LocalToolExecutor,
    NodeContract,
    ToolRecoveryCapabilities,
    ToolReference,
    ValueBinding,
    WorkflowEngine,
    tool_step,
    workflow,
)
from platform_lightweight_agent.workflow_v3 import (
    preflight_cache_metrics,
    preflight_v3,
)

_DIALECT = "https://json-schema.org/draft/2020-12/schema"
_TOOL = ToolReference("measure.echo", "1", "sha256:" + "e" * 64)
_STEP_COUNTS = (1, 10, 40, 100)


def _schema() -> dict[str, Any]:
    return {
        "$schema": _DIALECT,
        "type": "object",
        "properties": {"value": {"type": "integer"}},
        "required": ["value"],
        "additionalProperties": False,
    }


def _definition(count: int, version: str):
    input_schema = _schema()
    output_schema = _schema()
    steps = tuple(
        tool_step(
            f"echo-{index}",
            _TOOL,
            approval_required=False,
            contract=NodeContract(
                input_schema,
                output_schema,
                {"/value": ValueBinding("workflow_input", "/value")},
            ),
            capabilities=ToolRecoveryCapabilities(external_side_effects=False),
        )
        for index in range(count)
    )
    return workflow(
        "measure-v3-v4",
        version,
        steps,
        schema_version="v6-light-workflow-v4",
        input_schema=input_schema,
        output_schema=output_schema,
        output_bindings={
            "/value": ValueBinding("step_output", "/value", steps[-1].step_id)
        },
    )


def _elapsed(callable_: Any) -> float:
    started = time.perf_counter_ns()
    callable_()
    return round((time.perf_counter_ns() - started) / 1_000_000, 3)


def _executor() -> LocalToolExecutor:
    executor = LocalToolExecutor()
    executor.register(_TOOL, lambda arguments: {"value": arguments["value"]})
    return executor


def _measure_timing(count: int) -> dict[str, object]:
    # A distinct immutable version forces exactly one cold compilation.  The
    # immediate second call exercises the production LRU key, not a mock.
    definition = _definition(count, f"{count}-steps")
    cold_ms = _elapsed(lambda: preflight_v3(definition))
    hot_ms = _elapsed(lambda: preflight_v3(definition))
    engine = WorkflowEngine(InMemoryRunJournal(), _executor(), {})
    execution_ms = _elapsed(
        lambda: engine.run(definition, {"value": count}, run_id=f"measure-{count}")
    )
    return {
        "steps": count,
        "preflight_cold_ms": cold_ms,
        "preflight_hot_ms": hot_ms,
        "local_execution_ms": execution_ms,
    }


def _measure_allocation_peak(count: int) -> dict[str, int]:
    """Measure one full local run without contaminating timing measurements.

    Tracing allocations distorts small-run latency substantially.  Keep this
    probe separate, give it a distinct immutable definition digest, and label
    the result as an allocation peak rather than process RSS.
    """

    definition = _definition(count, f"{count}-steps-allocation")
    engine = WorkflowEngine(InMemoryRunJournal(), _executor(), {})
    gc.collect()
    tracemalloc.start()
    try:
        engine.run(
            definition,
            {"value": count},
            run_id=f"measure-allocation-{count}",
        )
        current, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    return {
        "tracemalloc_current_bytes": current,
        "tracemalloc_peak_bytes": peak,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--skip-allocation-probe",
        action="store_true",
        help="measure latency only; omit the separate tracemalloc peak probe",
    )
    args = parser.parse_args(argv)
    results = []
    previous: dict[str, object] | None = None
    for count in _STEP_COUNTS:
        measurement = _measure_timing(count)
        if not args.skip_allocation_probe:
            measurement.update(_measure_allocation_peak(count))
        if previous is not None:
            prior_steps = int(previous["steps"])
            prior_ms = float(previous["local_execution_ms"])
            measurement["incremental_local_execution_ms_per_step"] = round(
                (float(measurement["local_execution_ms"]) - prior_ms)
                / (count - prior_steps),
                3,
            )
        results.append(measurement)
        previous = measurement
    print(
        json.dumps(
            {
                "schema_version": "v6-v3-v4-overhead-v1",
                "python": platform.python_version(),
                "measurements": results,
                "preflight_cache": preflight_cache_metrics(),
                "timing_method": "perf_counter_ns with allocation tracing disabled",
                "growth_metric": (
                    "incremental_local_execution_ms_per_step compares each "
                    "step count with the preceding count"
                ),
                "allocation_probe": (
                    "per-case tracemalloc peak; not process RSS"
                    if not args.skip_allocation_probe
                    else "not requested"
                ),
                "external_services_started": [],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
