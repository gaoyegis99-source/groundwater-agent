#!/usr/bin/env python3
"""Fail-closed written-authorization and deterministic source-tree verifier."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import sys


ARCHIVE_SHA256 = "037c93c8d057d75090f1587bec14e4254c41b854b5bdb33b1dc79f20227eb719"
HEX = re.compile(r"^[0-9a-f]{64}$")


def tree_digest(root: Path) -> str:
    digest = hashlib.sha256()
    files = sorted(path for path in root.rglob("*") if path.is_file())
    if not files:
        raise ValueError("AUTHORIZED_SOURCE_EMPTY")
    for path in files:
        relative = path.relative_to(root).as_posix().encode()
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        file_digest = hashlib.sha256(path.read_bytes()).digest()
        digest.update(file_digest)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--authorization", type=Path, required=True)
    parser.add_argument("--source", type=Path, required=True)
    arguments = parser.parse_args()
    try:
        authorization = json.loads(arguments.authorization.read_text(encoding="utf-8"))
        required = {
            "authorizationId", "approvedBy", "approvedAt", "scope",
            "sourceArchiveSha256", "sourceTreeSha256",
        }
        if set(authorization) != required:
            raise ValueError("AUTHORIZATION_FIELDS_INVALID")
        if not all(
            isinstance(authorization[name], str) and authorization[name].strip()
            for name in ("authorizationId", "approvedBy", "approvedAt")
        ):
            raise ValueError("AUTHORIZATION_IDENTITY_INVALID")
        if set(authorization["scope"]) != {"modify", "deploy"}:
            raise ValueError("AUTHORIZATION_SCOPE_INSUFFICIENT")
        if authorization["sourceArchiveSha256"] != ARCHIVE_SHA256:
            raise ValueError("SOURCE_ARCHIVE_MISMATCH")
        expected = authorization["sourceTreeSha256"]
        if not isinstance(expected, str) or HEX.fullmatch(expected) is None:
            raise ValueError("SOURCE_TREE_DIGEST_INVALID")
        actual = tree_digest(arguments.source.resolve(strict=True))
        if actual != expected:
            raise ValueError("SOURCE_TREE_MISMATCH")
    except (OSError, json.JSONDecodeError, TypeError, ValueError) as error:
        print(f"DENIED: {error}", file=sys.stderr)
        return 2
    print(json.dumps({"authorized": True, "sourceTreeSha256": actual}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
