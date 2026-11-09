"""Prepare and run the current V12 browser acceptance gate."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess

from webui_seed import seed_browser_data


def run_browser(
    base_url: str,
    root: Path,
    report: Path,
) -> None:
    seed_browser_data(base_url)
    report.mkdir(parents=True, exist_ok=True)
    environment = {
        **os.environ,
        "PLATFORM_BASE_URL": base_url,
        "PLATFORM_WEBUI_REPORT": str(report.resolve()),
    }
    # Node warns when both variables are inherited.  The controller already
    # treats FORCE_COLOR as authoritative; apply the same policy to the nested
    # Playwright process so the final Compose log is warning-free.
    # Playwright may set FORCE_COLOR itself after npm starts, so checking only
    # the inherited value is insufficient.  Remove NO_COLOR unconditionally
    # for this Node subprocess to avoid the runtime warning.
    environment.pop("NO_COLOR", None)
    subprocess.run(
        ["npm", "run", "test:e2e"],
        cwd=root / "web",
        check=True,
        env=environment,
    )
    print(f"v12 WebUI browser gate PASS: {report}", flush=True)
