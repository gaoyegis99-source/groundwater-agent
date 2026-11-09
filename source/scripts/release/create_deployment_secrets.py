"""Create one private, non-repository secret set for an isolated deployment."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import secrets


def _write(path: Path, value: bytes) -> None:
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o444)
    with os.fdopen(fd, "wb") as output:
        output.write(value)
        # Compose bind mounts expose the file inode directly to non-owner UIDs.
        # Keep the private parent directory at 0700, while making the mounted
        # read-only secret readable independently of the operator's umask.
        os.fchmod(output.fileno(), 0o444)
        output.flush()
        os.fsync(output.fileno())


def create(
    directory: Path,
    agent_api_key_file: Path,
    intent_router_api_key_file: Path,
) -> None:
    if directory.resolve().is_relative_to(Path(__file__).resolve().parents[2]):
        raise ValueError("SECRETS_DIRECTORY_INSIDE_REPOSITORY")
    api_key = agent_api_key_file.read_bytes().rstrip(b"\r\n")
    intent_router_api_key = intent_router_api_key_file.read_bytes().rstrip(b"\r\n")
    if not api_key:
        raise ValueError("AGENT_API_KEY_EMPTY")
    if not intent_router_api_key:
        raise ValueError("INTENT_ROUTER_API_KEY_EMPTY")
    directory.mkdir(mode=0o700)
    os.chmod(directory, 0o700)
    _write(directory / "agent-api-key", api_key)
    _write(directory / "intent-router-api-key", intent_router_api_key)
    # Database credentials are deployment-local values, not provider inputs.
    # Generate both here so the directory is a complete secret set for the
    # Compose contract and no service ever falls back to an inline/default
    # password.
    _write(directory / "data-db-password", secrets.token_urlsafe(48).encode())
    _write(
        directory / "knowledge-db-password", secrets.token_urlsafe(48).encode()
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--agent-api-key-file", required=True, type=Path)
    parser.add_argument("--intent-router-api-key-file", required=True, type=Path)
    args = parser.parse_args()
    create(
        args.output_dir,
        args.agent_api_key_file,
        args.intent_router_api_key_file,
    )
    print(args.output_dir.resolve())


if __name__ == "__main__":
    main()
