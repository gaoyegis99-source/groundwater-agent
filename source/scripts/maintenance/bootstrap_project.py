#!/usr/bin/env python3
"""Create missing project launchers from tracked templates; never provision a runtime."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[2]
PROJECT = SOURCE.parent
ASSETS = Path(__file__).with_name("bootstrap-assets")


def subset(expected, actual) -> bool:
    if isinstance(expected, dict):
        return isinstance(actual, dict) and all(
            key in actual and subset(value, actual[key])
            for key, value in expected.items()
        )
    return expected == actual


def bootstrap(*, execute: bool = False, check: bool = False) -> dict:
    if SOURCE.name != "source" or SOURCE.is_symlink():
        raise ValueError("CLONE_INTO_PROJECT_SOURCE_REQUIRED")
    entries = []
    for template in sorted(ASSETS.rglob("*")):
        if template.is_symlink():
            raise ValueError("BOOTSTRAP_TEMPLATE_SYMLINK")
        if not template.is_file():
            continue
        relative = template.relative_to(ASSETS)
        destination = PROJECT / relative
        for parent in [destination, *destination.parents]:
            if parent == PROJECT:
                break
            if parent.is_symlink():
                raise ValueError("BOOTSTRAP_TARGET_SYMLINK")
        content = template.read_bytes().replace(b"__UID__", str(os.getuid()).encode())
        state = "MISSING"
        if destination.exists():
            if not destination.is_file():
                state = "CONFLICT"
            elif relative.as_posix() == "project-layout.json":
                try:
                    state = (
                        "MATCH"
                        if subset(
                            json.loads(content), json.loads(destination.read_bytes())
                        )
                        else "CONFLICT"
                    )
                except ValueError:
                    state = "CONFLICT"
            else:
                state = "MATCH" if destination.read_bytes() == content else "CONFLICT"
                if relative.parts[0] == "bin" and not os.access(destination, os.X_OK):
                    state = "CONFLICT"
        entries.append((relative, destination, content, state))
    if not entries:
        raise ValueError("BOOTSTRAP_ASSETS_MISSING")
    if any(item[3] == "CONFLICT" for item in entries):
        return {
            "status": "FAILED",
            "code": "BOOTSTRAP_CONFLICT",
            "conflicts": [str(item[0]) for item in entries if item[3] == "CONFLICT"],
        }
    missing = [item for item in entries if item[3] == "MISSING"]
    if execute:
        for relative, destination, content, _ in missing:
            destination.parent.mkdir(parents=True, exist_ok=True)
            # Exclusive create: a concurrent writer is never overwritten.
            with destination.open("xb") as stream:
                stream.write(content)
                os.fchmod(
                    stream.fileno(), 0o755 if relative.parts[0] == "bin" else 0o644
                )
    return {
        "status": "PASS"
        if execute or not missing
        else "BLOCKED"
        if check
        else "PLANNED",
        "files": [str(item[0]) for item in missing],
        "note": "No environments, instances, Docker directories, or credentials were provisioned.",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--plan", action="store_true")
    modes.add_argument("--check", action="store_true")
    modes.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    try:
        result = bootstrap(execute=args.execute, check=args.check)
    except (ValueError, OSError) as error:
        result = {"status": "FAILED", "code": type(error).__name__}
    print(json.dumps(result, sort_keys=True))
    raise SystemExit(
        0
        if result["status"] in {"PASS", "PLANNED"}
        else 75
        if result["status"] == "BLOCKED"
        else 1
    )


if __name__ == "__main__":
    main()
