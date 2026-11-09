"""Measure bounded process resources across repeated local lifecycle work."""

from __future__ import annotations

import argparse
from contextlib import closing
import gc
import json
import os
from pathlib import Path
import sqlite3
import tempfile
from threading import active_count

from platform_lightweight_agent import (
    HostRuntime,
    InMemoryRunJournal,
    LocalToolExecutor,
    NodeContract,
    RuntimeAssembly,
    SQLiteRunJournal,
    ToolRecoveryCapabilities,
    ToolReference,
    ValueBinding,
    WorkflowEngine,
    approval_step,
    process_resource_diagnostics,
    tool_step,
    workflow,
)


TOOL = ToolReference("probe.resource", "1", "sha256:" + "1" * 64)
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


def _fds() -> int | None:
    directory = Path("/proc/self/fd")
    return len(tuple(directory.iterdir())) if directory.is_dir() else None


def _sqlite_fds() -> int | None:
    directory = Path("/proc/self/fd")
    if not directory.is_dir():
        return None
    count = 0
    for descriptor in directory.iterdir():
        try:
            target = os.readlink(descriptor)
        except OSError:
            continue
        if ".sqlite" in target:
            count += 1
    return count


def _resources() -> dict[str, object]:
    return {
        "file_descriptors": _fds(),
        "sqlite_file_descriptors": _sqlite_fds(),
        "threads": active_count(),
        "mutex_registry": process_resource_diagnostics().to_mapping(),
    }


def _executor() -> LocalToolExecutor:
    executor = LocalToolExecutor()
    executor.register(TOOL, lambda _arguments: {"value": 1})
    return executor


def _legacy_spec():  # type: ignore[no-untyped-def]
    return workflow(
        "resource-short",
        "1",
        [tool_step("effect", TOOL, {}, approval_required=False)],
        schema_version="v6-light-workflow-v1",
    )


def _approval_spec():  # type: ignore[no-untyped-def]
    return workflow(
        "resource-approval",
        "1",
        [
            approval_step("review", "continue?"),
            tool_step("effect", TOOL, {}, approval_required=False),
        ],
        schema_version="v6-light-workflow-v1",
    )


def _host_spec():  # type: ignore[no-untyped-def]
    return workflow(
        "resource-host",
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
        output_bindings={"/value": ValueBinding("step_output", "/value", "effect")},
    )


def collect() -> dict[str, object]:
    baseline = _resources()
    short_curve: list[dict[str, object]] = []
    spec = _legacy_spec()
    for index in range(1, 1_001):
        result = WorkflowEngine(InMemoryRunJournal(), _executor(), {}).run(
            spec, run_id=f"short-{index}"
        )
        if result.status != "SUCCEEDED":
            raise RuntimeError("short local run did not succeed")
        if index % 100 == 0:
            short_curve.append({"completed": index, **_resources()})

    approval_curve: list[dict[str, object]] = []
    with tempfile.TemporaryDirectory(prefix="v6-resource-stress-") as raw:
        journal_path = Path(raw) / "approvals.sqlite"
        for index in range(1, 51):
            run_id = f"approval-{index}"
            first = WorkflowEngine(SQLiteRunJournal(journal_path), _executor(), {})
            waiting = first.run(_approval_spec(), run_id=run_id)
            if waiting.status != "WAITING_APPROVAL":
                raise RuntimeError("approval run did not wait")
            restarted = WorkflowEngine(SQLiteRunJournal(journal_path), _executor(), {})
            complete = restarted.resume(run_id, approved=True)
            if complete.status != "SUCCEEDED":
                raise RuntimeError("approval recovery did not succeed")
            if index % 10 == 0:
                with closing(sqlite3.connect(journal_path)) as connection:
                    event_count = connection.execute(
                        "SELECT COUNT(*) FROM lightweight_agent_events"
                    ).fetchone()[0]
                approval_curve.append(
                    {"completed": index, "events": event_count, **_resources()}
                )

    host_curve: list[dict[str, object]] = []
    for index in range(1, 101):
        host = HostRuntime(RuntimeAssembly(InMemoryRunJournal(), _executor()))
        try:
            result = host.run(_host_spec(), {}, run_id=f"host-{index}")
            if result.status != "SUCCEEDED":
                raise RuntimeError("host run did not succeed")
        finally:
            host.close(mode="wait")
        if index % 10 == 0:
            host_curve.append({"completed": index, **_resources()})

    gc.collect()
    final = _resources()
    baseline_fds = baseline["file_descriptors"]
    final_fds = final["file_descriptors"]
    baseline_sqlite = baseline["sqlite_file_descriptors"]
    final_sqlite = final["sqlite_file_descriptors"]
    registry = final["mutex_registry"]
    requirements = {
        "one_thousand_short_runs": short_curve[-1]["completed"] == 1_000,
        "fifty_approval_recoveries": approval_curve[-1]["completed"] == 50,
        "one_hundred_hosts_closed": host_curve[-1]["completed"] == 100,
        "file_descriptors_return": baseline_fds is None
        or final_fds is not None
        and int(final_fds) <= int(baseline_fds) + 2,
        "sqlite_descriptors_return": baseline_sqlite is None
        or final_sqlite is not None
        and int(final_sqlite) <= int(baseline_sqlite),
        "threads_return": int(final["threads"]) <= int(baseline["threads"]) + 1,
        "mutex_registry_idle": isinstance(registry, dict)
        and registry["registered_resources"] == 0
        and registry["holders"] == 0
        and registry["waiters"] == 0,
    }
    return {
        "schema_version": "v6-resource-stress-evidence-v1",
        "result": "PASS" if all(requirements.values()) else "FAIL",
        "results": [
            {
                "case": "O02",
                "variant": "runs_approvals_and_host_lifecycle",
                "contract_result": ("PASS" if all(requirements.values()) else "FAIL"),
                "requirements": requirements,
                "baseline": baseline,
                "final": final,
                "short_run_curve": short_curve,
                "approval_recovery_curve": approval_curve,
                "host_curve": host_curve,
            }
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = collect()
    payload = json.dumps(report, indent=2, sort_keys=True) + "\n"
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(payload, encoding="utf-8")
    print(payload, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
