"""Scan browser evidence archives before they become deliverable artifacts."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import zipfile


FORBIDDEN = (
    b"synthetic-probe-token",
    b"OPENAI_API_KEY",
    b"ANTHROPIC_API_KEY",
    b"sk-proj-",
)
REDACTION = b"[REDACTED]"


def scan(path: Path) -> dict[str, object]:
    digest = "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
    matches: dict[str, list[str]] = {}
    redaction_markers = 0
    with zipfile.ZipFile(path) as archive:
        for member in archive.infolist():
            if member.is_dir():
                continue
            content = archive.read(member)
            redaction_markers += content.count(REDACTION)
            for pattern in FORBIDDEN:
                if pattern in content:
                    matches.setdefault(pattern.decode("ascii"), []).append(
                        member.filename
                    )
    return {
        "path": str(path),
        "sha256": digest,
        "size_bytes": path.stat().st_size,
        "redaction_markers": redaction_markers,
        "forbidden_matches": matches,
        "contract_result": "PASS" if not matches else "FAIL",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("trace_directory", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    traces = [scan(path) for path in sorted(args.trace_directory.glob("*.zip"))]
    report = {
        "schema_version": "v6-browser-trace-scan-v1",
        "result": (
            "NOT_ASSESSED"
            if not traces
            else "FAIL"
            if any(item["contract_result"] == "FAIL" for item in traces)
            else "PASS"
        ),
        "forbidden_patterns": [item.decode("ascii") for item in FORBIDDEN],
        "traces": traces,
    }
    payload = json.dumps(report, indent=2, sort_keys=True) + "\n"
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(payload, encoding="utf-8")
    print(payload, end="")
    return 1 if report["result"] == "FAIL" else 0


if __name__ == "__main__":
    raise SystemExit(main())
