"""The installed Native worker launches the installed static operator entrypoint."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def test_operator_child_execs_static_operator_task(tmp_path: Path) -> None:
    payload, result = tmp_path / "payload.json", tmp_path / "result.json"
    payload.write_text(
        json.dumps(
            {
                "runtime": "native",
                "operatorId": "does.not.exist",
                "operatorVersion": "1.0.0",
                "inputs": {},
                "parameters": {},
                "outputDirectory": str(tmp_path),
            }
        ),
        encoding="utf-8",
    )
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "platform_executor_service.operator_child",
            "60",
            str(16 * 1024**3),
            str(payload),
            str(result),
        ],
        cwd=tmp_path,
        capture_output=True,
        timeout=20,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr.decode()
    assert json.loads(result.read_text(encoding="utf-8"))["error"]["code"] == (
        "OPERATOR_NOT_IMPLEMENTED"
    )
