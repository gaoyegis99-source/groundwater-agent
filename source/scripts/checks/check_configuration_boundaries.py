"""Keep V6 deployment configuration at named composition boundaries.

This is intentionally a small static guard, not a replacement for the runtime
validators.  It prevents the two regressions that made local maintenance hard:
new hard-coded local actors and a second Executor capacity/Compose parser.
"""

from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SOURCE_ROOTS = (
    "kernel/src",
    "services/data/src",
    "services/executor/src",
    "services/knowledge-next/src",
    "platform/assembler",
    "packages/protocol-contracts/src",
    "scripts/operations",
)
IDENTITY_LITERAL = "local-researcher"
IDENTITY_LITERAL_ALLOWLIST = frozenset(
    {
        "packages/protocol-contracts/src/platform_protocol_contracts/identity.py",
        # The assembler transports a compatible Compose-template default but
        # must not import the runtime identity package.  It performs no
        # parsing or validation; every runtime consumer uses LocalIdentity.
        "platform/assembler/assembler_runtime.py",
    }
)
EXECUTOR_CAPACITY_ALLOWLIST = frozenset(
    {
        "platform/assembler/executor_profile.py",
    }
)
EXECUTOR_CAPACITY_INPUTS = (
    "EXECUTOR_NATIVE_SLOTS",
    "EXECUTOR_QGIS_SLOTS",
    "EXECUTOR_NATIVE_MEMORY_BYTES",
    "EXECUTOR_QGIS_MEMORY_BYTES",
)


def _source_paths(root: Path) -> list[Path]:
    paths: list[Path] = []
    for relative in SOURCE_ROOTS:
        directory = root / relative
        if directory.is_dir():
            paths.extend(
                path
                for path in directory.rglob("*.py")
                if not {"__pycache__", "build", "dist"}.intersection(path.parts)
            )
    return sorted(paths)


def configuration_violations(root: Path = ROOT) -> list[dict[str, str]]:
    """Return deterministic, machine-readable configuration-boundary findings."""

    findings: list[dict[str, str]] = []
    for path in _source_paths(root):
        relative = path.relative_to(root).as_posix()
        source = path.read_text(encoding="utf-8")
        if (
            IDENTITY_LITERAL in source
            and relative not in IDENTITY_LITERAL_ALLOWLIST
        ):
            findings.append(
                {
                    "code": "HARDCODED_LOCAL_IDENTITY",
                    "path": relative,
                }
            )
        if any("${" + name in source for name in EXECUTOR_CAPACITY_INPUTS):
            findings.append(
                {
                    "code": "COMPOSE_EXECUTOR_CAPACITY_SUBSTITUTION",
                    "path": relative,
                }
            )
        if (
            any(name in source for name in EXECUTOR_CAPACITY_INPUTS)
            and relative not in EXECUTOR_CAPACITY_ALLOWLIST
        ):
            findings.append(
                {
                    "code": "DUPLICATE_EXECUTOR_CAPACITY_PARSER",
                    "path": relative,
                }
            )
    return sorted(findings, key=lambda item: (item["path"], item["code"]))


def main() -> int:
    findings = configuration_violations()
    print(json.dumps({"status": "PASS" if not findings else "FAILED", "findings": findings}))
    return 0 if not findings else 1


if __name__ == "__main__":
    raise SystemExit(main())
