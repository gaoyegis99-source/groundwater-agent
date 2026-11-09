#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import platform
import subprocess


def sha256(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("authorization", type=Path)
    parser.add_argument("source_root", type=Path)
    parser.add_argument("binary_root", type=Path)
    arguments = parser.parse_args()

    authorization = json.loads(
        arguments.authorization.read_text(encoding="utf-8")
    )
    source_root = arguments.source_root.resolve(strict=True)
    binary_root = arguments.binary_root.resolve(strict=True)
    if not source_root.is_dir() or not binary_root.is_dir():
        raise ValueError("SOLVER_MANIFEST_ROOT_INVALID")
    binaries = {}
    for name in ("mf2005", "mfnwt", "mt3dusgs", "pest"):
        path = binary_root / name
        if not path.is_file() or path.is_symlink():
            raise ValueError(f"SOLVER_BINARY_INVALID:{name}")
        binaries[name] = {
            "sha256": sha256(path),
            "sizeBytes": path.stat().st_size,
        }
    compiler = subprocess.run(
        ["gfortran", "--version"], check=True, text=True, capture_output=True
    ).stdout.splitlines()[0]
    manifest = {
        "schemaVersion": "groundwater-solver-manifest.v1",
        "authorizationId": authorization["authorizationId"],
        "sourceArchiveSha256": "sha256:" + authorization["sourceArchiveSha256"],
        "sourceTreeSha256": "sha256:" + authorization["sourceTreeSha256"],
        "sourceFileCount": sum(
            1 for path in source_root.rglob("*") if path.is_file()
        ),
        "platform": f"{platform.system().lower()}/{platform.machine()}",
        "compiler": compiler,
        "compilerFlags": ["-O2", "-fno-fast-math", "-frecursive"],
        "binaries": binaries,
    }
    print(
        json.dumps(
            manifest,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
