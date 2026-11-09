#!/usr/bin/env python3
"""Reject retired release and contract versions outside the one 404 fixture."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
FIXTURE = Path("tests/contracts/fixtures/retired_paths.json")
RETIRED = re.compile(
    r"(?i)(?:"
    r"(?<![a-z0-9])v6[.][12](?![a-z0-9])|"
    r"public[ _-]?api[ _-]?v(?:8|9|10)(?![a-z0-9])|"
    r"/api/v(?:8|9|10)(?:/|$)|"
    r"/(?:data|knowledge)/v(?:1|2)(?:/|$)|"
    r"/internal/execution/v[1-5](?:/|$)|"
    r"/internal/workers/v[12](?:/|$)|"
    r"legacy/" + r"v8|pytest-" + r"legacy|historical_" + r"v8"
    r")"
)

# This is a filesystem identifier for the retained V6 linked-worktree build
# pointer, not a public API or release version.  Keep this exception exact so
# prose that names a retired API version remains rejected.
_RUNTIME_IDENTIFIER = "agent-core-v6.1"


def main() -> int:
    result = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        cwd=ROOT,
        check=True,
        stdout=subprocess.PIPE,
    )
    errors: list[str] = []
    for raw_path in result.stdout.split(b"\0"):
        if not raw_path:
            continue
        relative = Path(raw_path.decode())
        if relative == FIXTURE or "node_modules" in relative.parts:
            continue
        path = ROOT / relative
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for number, line in enumerate(text.splitlines(), 1):
            checked_line = line.replace(_RUNTIME_IDENTIFIER, "agent-core-v6_1")
            if RETIRED.search(checked_line):
                errors.append(f"{relative}:{number}:{line.strip()}")
    if errors:
        print("RETIRED_VERSION_REFERENCE_FOUND")
        print("\n".join(errors))
        return 1
    print("RETIRED_VERSION_SCAN_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
