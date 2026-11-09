"""Bounded subprocess recording for Data/Knowledge verification."""
from __future__ import annotations
import os
from pathlib import Path
import subprocess
import sys
import time
ROOT = Path(__file__).resolve().parents[3]

class Recorder:
    def __init__(self) -> None:
        self.commands: list[dict[str, object]] = []

    def run(
        self,
        command: list[str],
        *,
        env: dict[str, str] | None = None,
        capture: bool = False,
        check: bool = True,
        cwd: Path = ROOT,
        phase: str = "command",
        expected_return_codes: set[int] | None = None,
        output_log: Path | None = None,
    ) -> subprocess.CompletedProcess[str]:
        print("+", " ".join(command), flush=True)
        started = time.monotonic()
        merged_env = os.environ.copy()
        if env:
            merged_env.update(env)
        # The npm/Playwright child may set FORCE_COLOR after it starts. Remove
        # NO_COLOR unconditionally so that combination cannot produce a noisy
        # Node warning in the captured UI evidence.
        merged_env.pop("NO_COLOR", None)
        result = subprocess.run(
            command,
            cwd=cwd,
            env=merged_env,
            text=True,
            capture_output=capture or output_log is not None,
            check=False,
        )
        command_record: dict[str, object] = {
            "command": command,
            "returnCode": result.returncode,
            "durationSeconds": round(time.monotonic() - started, 3),
            "phase": phase,
        }
        if expected_return_codes and result.returncode in expected_return_codes:
            command_record["classification"] = (
                "EXPECTED_STARTUP_RETRY"
                if phase == "startup_wait"
                else "EXPECTED_CLEANUP_MISSING"
                if phase == "teardown"
                else "EXPECTED_NONZERO"
            )
        elif result.returncode:
            command_record["classification"] = "UNEXPECTED_NONZERO"
        if output_log is not None:
            output_log.parent.mkdir(parents=True, exist_ok=True)
            output_log.write_text(
                (result.stdout or "") + (result.stderr or ""),
                encoding="utf-8",
            )
            command_record["outputLog"] = str(output_log)
        self.commands.append(command_record)
        if check and result.returncode:
            if capture:
                sys.stderr.write(result.stdout)
                sys.stderr.write(result.stderr)
            raise subprocess.CalledProcessError(
                result.returncode,
                command,
                output=result.stdout,
                stderr=result.stderr,
            )
        return result
