"""Emit a small, dependency-free V6 lightweight-kernel baseline as JSON.

Run this in a clean environment after installing only the base package:

    python packages/lightweight-agent-core/scripts/measure_baseline.py

It intentionally starts no Web, Data, Knowledge, Temporal, GIS, or container
service. Values are measurements, not universal release thresholds.
"""

from __future__ import annotations

import importlib.metadata
import json
import platform
import resource
import sys
import time
import tracemalloc


def _rss_bytes() -> int:
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    # macOS reports bytes; Linux reports KiB.
    return int(peak if sys.platform == "darwin" else peak * 1024)


def main() -> int:
    started = time.perf_counter_ns()
    tracemalloc.start()
    from platform_lightweight_agent import (
        InMemoryRunJournal,
        LocalToolExecutor,
        ToolReference,
        WorkflowEngine,
        tool_step,
        workflow,
    )

    imported = time.perf_counter_ns()
    tool = ToolReference("baseline.echo", "1", "sha256:" + "0" * 64)
    executor = LocalToolExecutor()
    executor.register(tool, lambda arguments: {"echo": dict(arguments)})
    engine = WorkflowEngine(InMemoryRunJournal(), executor, {})
    result = engine.run(
        workflow(
            "baseline",
            "1",
            [tool_step("echo", tool, {"ok": True}, approval_required=False)],
            schema_version="v6-light-workflow-v1",
        ),
        run_id="baseline-run",
    )
    completed = time.perf_counter_ns()
    current, peak = tracemalloc.get_traced_memory()
    try:
        package_version = importlib.metadata.version("platform-lightweight-agent-core")
    except importlib.metadata.PackageNotFoundError:
        package_version = "source-tree"
    print(
        json.dumps(
            {
                "schema_version": "v6-lightweight-baseline-v1",
                "python": platform.python_version(),
                "platform": platform.platform(),
                "package_version": package_version,
                "base_declared_dependencies": [],
                "service_processes_started": [],
                "import_ms": round((imported - started) / 1_000_000, 3),
                "first_local_workflow_ms": round(
                    (completed - imported) / 1_000_000, 3
                ),
                "tracemalloc_current_bytes": current,
                "tracemalloc_peak_bytes": peak,
                "process_peak_rss_bytes": _rss_bytes(),
                "run_status": result.status,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
