"""The shell gate must fail closed when its required search tool is absent."""
import os
from pathlib import Path
import subprocess


def test_missing_ripgrep_is_a_failed_gate(tmp_path):
    script = Path(__file__).resolve().parents[2] / "scripts/checks/check-boundaries.sh"
    result = subprocess.run(
        ["/bin/bash", str(script)], cwd=tmp_path,
        env={**os.environ, "PATH": str(tmp_path)}, capture_output=True, text=True,
    )
    assert result.returncode == 127
    assert "ripgrep (rg) is required" in result.stderr
    assert result.stdout == ""
