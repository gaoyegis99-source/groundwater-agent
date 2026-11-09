"""Evidence inspection and secret-leak reporting for Compose acceptance."""
from __future__ import annotations
import json
from pathlib import Path
from .environment import _exec_python, _run

def _assert_no_secret_leaks(
    command: list[str], generated: Path, directory: Path
) -> None:
    secret_paths = [directory / "agent-provider-key"]
    secret_paths.extend(
        (directory / "secrets" / name)
        for name in (
            "agent-api-key",
            "intent-router-api-key",
            "data-db-password",
            "knowledge-db-password",
        )
    )
    sentinels = {
        path.name: path.read_bytes().strip() for path in secret_paths if path.exists()
    }
    assert sentinels and all(len(value) >= 16 for value in sentinels.values())
    stored = _exec_python(
        command,
        "kernel-service",
        """
import json, sqlite3
with sqlite3.connect('/data/kernel.sqlite3') as connection:
    rows = {table: [row[0] for row in connection.execute(f'SELECT {column} FROM {table}')]
            for table, column in (
                ('agent_run_state', 'state_json'), ('agent_traces', 'payload_json'),
                ('agent_spans', 'payload_json'), ('session_events', 'payload_json'))}
print(json.dumps(rows))
""",
    )
    stored_rows = json.loads(stored)
    evidence = {
        f"kernel.{name}": "\n".join(values).encode("utf-8")
        for name, values in stored_rows.items()
    }
    logs = _run([*command, "logs", "--no-color"], capture_output=True)
    evidence["compose.logs"] = (logs.stdout + logs.stderr).encode("utf-8")
    evidence.update(
        {
            f"generated.{path.name}": path.read_bytes()
            for path in generated.iterdir()
            if path.is_file()
        }
    )
    for location, content in evidence.items():
        for label, value in sentinels.items():
            escaped = json.dumps(value.decode("utf-8"))[1:-1].encode("utf-8")
            assert value not in content and escaped not in content, (
                f"SECRET_LEAK:{label}:{location}"
            )
    print(
        "credential leakage scan PASS "
        + json.dumps(
            {
                "secretCount": len(sentinels),
                "kernelRows": {
                    name: len(values) for name, values in stored_rows.items()
                },
                "generatedFiles": len(
                    [path for path in generated.iterdir() if path.is_file()]
                ),
            }
        ),
        flush=True,
    )
