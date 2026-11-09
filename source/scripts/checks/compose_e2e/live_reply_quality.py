"""Real-Provider configuration and frozen reply-quality execution."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import sys

from .environment import ROOT, _run


@dataclass(frozen=True)
class LiveReplyQualityConfig:
    provider: str
    protocol: str
    base_url: str
    model: str
    key_env: str
    report: Path

    def provider_key(self) -> str:
        value = os.environ.get(self.key_env, "")
        if not value.strip() or "\n" in value or "\r" in value:
            raise ValueError("REPLY_QUALITY_PROVIDER_KEY_MISSING")
        return value

    def agent_environment(self) -> dict[str, str]:
        return {
            "PLATFORM_AGENT_PROVIDER": self.provider,
            "PLATFORM_AGENT_PROTOCOL": self.protocol,
            "PLATFORM_AGENT_BASE_URL": self.base_url,
            "PLATFORM_AGENT_MODEL": self.model,
            "PLATFORM_AGENT_CONTEXT_WINDOW_TOKENS": "131072",
            "PLATFORM_AGENT_MAX_OUTPUT_TOKENS": "4096",
            "PLATFORM_AGENT_REQUEST_TIMEOUT_SECONDS": "180",
            "PLATFORM_AGENT_SEGMENT_TIMEOUT_SECONDS": "600",
        }


def configure_kernel(kernel: dict, config: LiveReplyQualityConfig) -> None:
    agent = config.agent_environment()
    router = {
        name.replace("PLATFORM_AGENT_", "PLATFORM_INTENT_ROUTER_"): value
        for name, value in agent.items()
    }
    router["PLATFORM_INTENT_ROUTER_MAX_OUTPUT_TOKENS"] = "256"
    kernel["environment"].update(agent | router)
    kernel.get("depends_on", {}).pop("test-upstream", None)


def run_reply_quality(
    *,
    base_url: str,
    kernel_config: Path,
    config: LiveReplyQualityConfig,
) -> None:
    environment = {
        **os.environ,
        **config.agent_environment(),
        "PLATFORM_AGENT_API_KEY_FILE": "/run/secrets/agent-api-key",
    }
    config.report.unlink(missing_ok=True)
    completed = _run(
        [
            sys.executable,
            str(ROOT / "scripts/evaluation/evaluate_reply_quality.py"),
            "run",
            "--base-url",
            base_url,
            "--knowledge-url",
            base_url,
            "--kernel-config",
            str(kernel_config),
            "--output",
            str(config.report),
        ],
        env=environment,
        check=False,
        capture_output=True,
    )
    if completed.returncode:
        summary: dict[str, object] = {
            "returnCode": completed.returncode,
            "stderrTail": completed.stderr[-2000:],
            "stdoutTail": completed.stdout[-2000:],
        }
        if config.report.is_file():
            try:
                report = json.loads(config.report.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                summary["report"] = "INVALID"
            else:
                summary["report"] = {
                    "status": report.get("status"),
                    "runCount": report.get("run_count"),
                    "passCount": report.get("pass_count"),
                    "failedRuns": [
                        {
                            key: run.get(key)
                            for key in (
                                "case_id", "iteration", "failure",
                                "failure_stage", "agent_error_code",
                                "service_error_code", "evaluation_error_code",
                            )
                            if run.get(key) is not None
                        }
                        for run in report.get("runs", [])
                        if run.get("status") != "PASS"
                    ],
                }
        raise RuntimeError(
            "REPLY_QUALITY_GATE_FAILED:"
            + json.dumps(summary, ensure_ascii=False, sort_keys=True)
        )
