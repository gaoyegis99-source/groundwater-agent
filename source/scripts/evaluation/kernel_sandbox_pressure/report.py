"""Pressure scenario orchestration and bounded report construction."""
from __future__ import annotations
import asyncio
from pathlib import Path
import random
import time
from .context import ProbeRecorder
from .environment import ROOT, SCHEMA, _configuration, _digest, _environment, _install_seeded_uuid4, _now
from .scenarios.control import _run_control_probe, _run_race_probe
from .scenarios.dialogue import _run_http_load, _run_http_probe, _run_in_process
from .scenarios.provider import _run_fault_injection
from .scenarios.recovery import _run_recovery_probe

def build_report(*, root: Path = ROOT, seed: int = 20261018) -> dict[str, object]:
    random.seed(seed)
    restore_uuid4 = _install_seeded_uuid4(seed)
    probes = ProbeRecorder()
    started = time.monotonic()
    result: dict[str, object] = {
        "schemaVersion": SCHEMA,
        "startedAt": _now(),
        "seed": seed,
        "scope": [
            "in_process_kernel", "asgi_http", "http_concurrency", "races",
            "control_flows", "restart_recovery", "fault_injection",
        ],
        "load": {"users": 20, "chatUsers": 10, "executionUsers": 8, "statusUsers": 2},
        "baseline": {"head": _git_head(root), "worktree": _git_status(root)},
        "environment": _environment(),
        "configuration": _configuration(root),
        "reproducibility": {
            "seedApplied": True,
            "uuidGeneration": "seeded-counter-sha256",
            "idValuesRedactedBeforeDigest": True,
            "network": "httpx.MockTransport_and_ASGI_only",
        },
        "stages": {},
        "notAssessed": [
            "REAL_AGENT_PROVIDER", "REAL_INTENT_ROUTER", "REAL_EMBEDDING",
            "DOCKER_COMPOSE",
        ],
    }
    try:
        result["stages"]["inProcess"] = asyncio.run(_run_in_process(root, probes))
        result["configuration"]["definitionSetDigest"] = result["stages"]["inProcess"].get("definitionSetDigest")
        result["stages"]["races"] = asyncio.run(_run_race_probe(root, probes))
        result["stages"]["controlFlows"] = asyncio.run(_run_control_probe(root, probes))
        result["stages"]["recovery"] = asyncio.run(_run_recovery_probe(root, probes))
        result["stages"]["httpLoad"] = asyncio.run(_run_http_load(root, probes))
        result["stages"]["asgiHttp"] = _run_http_probe(root, probes)
        result["stages"]["faultInjection"] = asyncio.run(_run_fault_injection(root, probes))
        result["capacityMetrics"] = {
            "inProcessAccept": result["stages"]["inProcess"].get("acceptLatencySeconds"),
            "httpMessageAcceptance": result["stages"]["httpLoad"].get("messageAcceptanceSeconds"),
            "httpQueueWait": result["stages"]["httpLoad"].get("queueWaitSeconds"),
            "sse": {
                "firstSeconds": result["stages"]["asgiHttp"].get("sseFirstSeconds"),
                "reconnectSeconds": result["stages"]["asgiHttp"].get("sseReconnectSeconds"),
                "connections": result["stages"]["asgiHttp"].get("sseMetrics", {}).get("connections"),
                "reconnections": result["stages"]["asgiHttp"].get("sseMetrics", {}).get("reconnections"),
            },
        }
        result["probeSummary"] = probes.summary()
        stages = result["stages"]
        stage_ok = all(
            isinstance(stage, dict)
            and stage.get("status") == "PASS"
            and all(value is True for value in stage.get("criteria", {}).values())
            for stage in stages.values()
        )
        result["status"] = "PASS" if stage_ok and not probes.failures else "FAILED"
    except Exception as error:
        probes.fail("PROBE_RUN_FAILED", phase="runner", operation="run", detail=f"{type(error).__name__}:{error}")
        result["probeSummary"] = probes.summary()
        result["status"] = "BLOCKED[environment]" if "REQUIRED" in str(error) else "FAILED"
        result["failure"] = {"code": "PROBE_RUN_FAILED", "type": type(error).__name__}
    finally:
        restore_uuid4()
    result["durationSeconds"] = round(time.monotonic() - started, 3)
    result["teardown"] = {"temporaryStoresRemoved": True, "dockerResourcesTouched": False}
    return result


def _git_head(root: Path) -> str:
    import subprocess
    return subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, check=False, capture_output=True, text=True).stdout.strip()


def _git_status(root: Path) -> dict[str, object]:
    import subprocess
    output = subprocess.run(["git", "status", "--short"], cwd=root, check=False, capture_output=True, text=True).stdout.splitlines()
    return {"modifiedCount": len(output), "summaryDigest": _digest(output)}
