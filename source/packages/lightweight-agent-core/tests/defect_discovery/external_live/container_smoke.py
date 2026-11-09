"""Opt-in, fixed-image smoke against ONLY the V6 rootless Docker service.

The embedding application owns this reviewed runner. It is not a general
Docker runner shipped by the core, nor evidence of arbitrary container safety.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import subprocess
import sys

from platform_lightweight_agent import (
    ContainerResourceLimits,
    ContainerToolDefinition,
    ContainerToolExecutor,
    NodeContract,
    SQLiteRunJournal,
    ToolRecoveryCapabilities,
    ToolReference,
    ValueBinding,
    WorkflowEngine,
    tool_step,
    workflow_v4,
)

IMAGE = "docker.io/library/busybox@sha256:66a6306db78bf2dbf3487f293aa8d6990d8e506fdffab9cc43fe422becf886e4"
LIMITS = ContainerResourceLimits(
    memory_bytes=64 * 1024 * 1024,
    cpu_millis=250,
    pids=32,
)
COMMAND = (
    "sh",
    "-ec",
    """
test "$(id -u)" = 65534
grep -q '^CapEff:[[:space:]]*0000000000000000$' /proc/self/status
grep -q '^NoNewPrivs:[[:space:]]*1$' /proc/self/status
if touch /tmp/v6-write-check 2>/dev/null; then exit 11; fi
test ! -S /var/run/docker.sock
test "$(ls /sys/class/net)" = lo
test "$(cat /sys/fs/cgroup/memory.max)" = 67108864
test "$(cat /sys/fs/cgroup/pids.max)" = 32
read quota period < /sys/fs/cgroup/cpu.max
test "$quota" != max
test "$((quota * 4))" = "$period"
printf '{"value":7,"uid":65534,"isolation":true}\n'
""",
)


class ResourceBoundaryError(RuntimeError):
    pass


class ReviewedRunner:
    def __init__(self, wrapper: Path):
        self.wrapper = wrapper
        self.container_ids = []
        self.facts = []
        self.resource_facts = None

    def check_resources(self):
        info = json.loads(self.docker("info", "--format", "{{json .}}"))
        self.resource_facts = {
            key: info.get(key)
            for key in (
                "CgroupVersion",
                "CgroupDriver",
                "MemoryLimit",
                "CpuCfsQuota",
                "PidsLimit",
            )
        }
        if (
            info.get("CgroupVersion") != "2"
            or info.get("CgroupDriver") in {None, "", "none"}
            or not all(
                info.get(key) is True
                for key in ("MemoryLimit", "CpuCfsQuota", "PidsLimit")
            )
        ):
            raise ResourceBoundaryError("RESOURCE_LIMIT_ENFORCEMENT_UNAVAILABLE")

    def ensure_resource_limits(self, *, limits):
        if limits != LIMITS:
            raise ResourceBoundaryError("RESOURCE_LIMIT_ENFORCEMENT_UNAVAILABLE")
        self.check_resources()

    def docker(self, *arguments: str, timeout: int = 30) -> str:
        result = subprocess.run(
            [str(self.wrapper), *arguments],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        if result.returncode:
            raise RuntimeError("V6_CONTAINER_COMMAND_FAILED:" + arguments[0])
        return result.stdout.strip()

    def run(
        self,
        *,
        image,
        command,
        arguments,
        action_id,
        timeout_seconds,
        resource_limits=None,
    ):
        if image != IMAGE or command != COMMAND or arguments != {}:
            raise RuntimeError("UNREVIEWED_CONTAINER_REQUEST")
        self.ensure_resource_limits(limits=resource_limits)
        name = "v6-acceptance-" + action_id[-20:]
        container = self.docker(
            "create",
            "--pull=never",
            "--name",
            name,
            "--label",
            "v6.acceptance.action_id=" + action_id,
            "--network",
            "none",
            "--read-only",
            "--user",
            "65534:65534",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--pids-limit",
            str(resource_limits.pids),
            "--memory",
            str(resource_limits.memory_bytes),
            "--cpus",
            str(resource_limits.cpu_millis / 1000),
            image,
            *command,
        )
        if not re.fullmatch(r"[0-9a-f]{64}", container):
            raise RuntimeError("INVALID_CREATED_CONTAINER_ID")
        self.container_ids.append(container)
        inspected = json.loads(self.docker("inspect", container))[0]
        host, config = inspected["HostConfig"], inspected["Config"]
        checks = {
            "pinned_image": config["Image"] == IMAGE,
            "nonroot": config["User"] == "65534:65534",
            "read_only_root": host["ReadonlyRootfs"] is True,
            "no_network": host["NetworkMode"] == "none",
            "no_mounts_or_sockets": inspected["Mounts"] == [] and not host["Binds"],
            "no_privilege": host["Privileged"] is False and host["CapDrop"] == ["ALL"],
            "no_new_privileges": "no-new-privileges" in host["SecurityOpt"],
            "resource_limits_configured": host["Memory"]
            == resource_limits.memory_bytes
            and host["PidsLimit"] == resource_limits.pids
            and host["NanoCpus"] == resource_limits.cpu_millis * 1_000_000,
            "no_host_namespaces": host["PidMode"] != "host"
            and host["IpcMode"] != "host",
            "stable_action_label": config["Labels"]["v6.acceptance.action_id"]
            == action_id,
        }
        self.facts.append(
            {"container_id": container, "action_id": action_id, "checks": checks}
        )
        if not all(checks.values()):
            raise RuntimeError("CONTAINER_BOUNDARY_REJECTED_BEFORE_START")
        output = self.docker("start", "--attach", container, timeout=timeout_seconds)
        exit_code = self.docker("inspect", "--format", "{{.State.ExitCode}}", container)
        if exit_code != "0":
            raise RuntimeError("CONTAINER_ISOLATION_PROBE_FAILED")
        return json.loads(output)

    def reconcile(self, *, image, action_id):
        # This smoke does not certify recovery of unknown container outcomes.
        return None

    def cleanup(self):
        for container in self.container_ids:
            self.docker("rm", "--force", container)


def collect(source_root: Path, evidence_root: Path):
    sys.path.insert(0, str(source_root))
    from scripts.maintenance.daemon_runtime import verify_daemon
    from scripts.maintenance.project_environment import lifecycle

    wrapper = source_root.parent / "bin/docker"
    boundary = verify_daemon(lifecycle(), wrapper)
    runner = ReviewedRunner(wrapper)
    failure, failure_code, current, cleanup = None, None, None, False
    try:
        # HostConfig records requested flags even when rootless cgroup v1
        # ignores them. Check daemon support first, then actual cgroup files
        # inside the fixed container before accepting any computed result.
        # No implicit pull, credentials, volume, socket or user-selected command.
        reference = ToolReference("acceptance.container", "1", "sha256:" + "9" * 64)
        executor = ContainerToolExecutor(runner)
        executor.register(
            ContainerToolDefinition(
                reference,
                IMAGE,
                COMMAND,
                timeout_seconds=20,
                resource_limits=LIMITS,
            )
        )
        runner.docker("image", "inspect", IMAGE)
        empty = {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": False,
        }
        output = {
            **empty,
            "properties": {
                "value": {"const": 7},
                "uid": {"const": 65534},
                "isolation": {"const": True},
            },
            "required": ["value", "uid", "isolation"],
        }
        definition = workflow_v4(
            "container-smoke",
            "1",
            [
                tool_step(
                    "compute",
                    reference,
                    approval_required=False,
                    contract=NodeContract(empty, output, {}),
                    capabilities=ToolRecoveryCapabilities(external_side_effects=True),
                )
            ],
            input_schema=empty,
            output_schema=output,
            output_bindings={
                "/" + key: ValueBinding("step_output", "/" + key, "compute")
                for key in output["required"]
            },
        )
        journal = SQLiteRunJournal(evidence_root / "runs.sqlite")
        engine = WorkflowEngine(journal, executor, {})
        current = engine.run(definition, {}, run_id="isolated-container")
        resumed = WorkflowEngine(
            SQLiteRunJournal(evidence_root / "runs.sqlite"), executor, {}
        ).resume(current.handle)
        passed = (
            current.status == resumed.status == "SUCCEEDED"
            and len(runner.facts) == 1
            and set(current.state["actions"]) == {runner.facts[0]["action_id"]}
            and resumed.state["v3"]["output"]
            == {"value": 7, "uid": 65534, "isolation": True}
        )
    except Exception as error:
        failure = type(error).__name__
        if isinstance(error, ResourceBoundaryError) or str(error) == (
            "RESOURCE_LIMIT_ENFORCEMENT_UNAVAILABLE"
        ):
            failure_code = str(error)
        passed = False
    finally:
        runner.cleanup()
        cleanup = True
    return {
        "schema_version": "v6-real-container-evidence-v1",
        "result": "PASS" if passed else "FAIL",
        "image": IMAGE,
        "daemon": boundary,
        "resource_enforcement": runner.resource_facts,
        "runtime_facts": runner.facts,
        "test_containers_removed": cleanup,
        "exception_type": failure,
        "failure_code": failure_code,
        "run_status": current.status if current else None,
        "unassessed": [
            "unknown-outcome recovery",
            "hostile image escape resistance",
            "other container runtimes",
        ],
        "results": [
            {
                "case": "X03",
                "variant": "isolated_pinned_container",
                "contract_result": "PASS" if passed else "FAIL",
            }
        ],
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--allow-local-container", action="store_true")
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--evidence-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not args.allow_local_container:
        raise SystemExit("Explicit scoped container authorization is required")
    args.evidence_root.mkdir(parents=True, exist_ok=False)
    report = collect(args.source_root.resolve(), args.evidence_root)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report))
    raise SystemExit(0 if report["result"] == "PASS" else 1)
