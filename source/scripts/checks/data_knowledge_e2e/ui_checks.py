"""Mocked and live browser checks for the Data/Knowledge E2E run."""

from __future__ import annotations

import os
from pathlib import Path

from .context import Recorder
from .report import _playwright_passed


def _run_mocked_ui_checks(recorder: Recorder, work: Path) -> dict[str, int]:
    counts: dict[str, int] = {}
    for package, script in (
        ("services/data/web", "test"),
        ("services/data/web", "typecheck"),
        ("services/data/web", "build"),
        ("services/data/web", "test:ui"),
        ("services/knowledge-next/ui", "test"),
        ("services/knowledge-next/ui", "typecheck"),
        ("services/knowledge-next/ui", "build"),
        ("services/knowledge-next/ui", "test:ui"),
    ):
        command = ["npm", "--prefix", package, "run", script]
        if script == "test:ui":
            report = work / f"{package.replace('/', '-')}-playwright.json"
            recorder.run(
                [*command, "--", "--reporter=line,json"],
                env={**os.environ, "PLAYWRIGHT_JSON_OUTPUT_NAME": str(report)},
            )
            counts[package] = _playwright_passed(report)
        else:
            recorder.run(command)
    return counts


def _run_live_data_ui(
    recorder: Recorder,
    work: Path,
    *,
    gateway_url: str,
    api_token: str,
    mocked_count: int,
) -> dict[str, object]:
    report = work / "data-live-playwright.json"
    recorder.run(
        [
            "npm",
            "--prefix",
            "services/data/web",
            "run",
            "test:e2e",
            "--",
            "--reporter=line,json",
        ],
        env={
            **os.environ,
            "DATA_E2E_URL": gateway_url,
            "PLATFORM_E2E_API_TOKEN": api_token,
            "DATA_E2E_SCREENSHOT": str(work / "data-browser.png"),
            "PLAYWRIGHT_JSON_OUTPUT_NAME": str(report),
        },
        output_log=work / "data-ui-e2e.log",
    )
    return {
        "status": "PASS",
        "mockedPlaywright": mocked_count,
        "livePlaywright": _playwright_passed(report),
    }


def _run_live_knowledge_ui(
    recorder: Recorder,
    work: Path,
    *,
    gateway_url: str,
    api_token: str,
    mocked_count: int,
    knowledge_env: dict[str, str],
) -> dict[str, object]:
    report = work / "knowledge-live-playwright.json"
    recorder.run(
        [
            "npm",
            "--prefix",
            "services/knowledge-next/ui",
            "run",
            "test:e2e",
            "--",
            "--reporter=line,json",
        ],
        env={
            **knowledge_env,
            "DATA_E2E_URL": gateway_url,
            "KNOWLEDGE_UI_URL": gateway_url,
            "OPERATOR_E2E_URL": gateway_url,
            "PLATFORM_E2E_API_TOKEN": api_token,
            "PLAYWRIGHT_JSON_OUTPUT_NAME": str(report),
        },
        output_log=work / "knowledge-ui-e2e.log",
    )
    return {
        "status": "PASS",
        "mockedPlaywright": mocked_count,
        "livePlaywright": _playwright_passed(report),
    }
