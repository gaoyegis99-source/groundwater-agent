"""CLI parsing for isolated Data and Knowledge verification."""

from __future__ import annotations

import argparse
from pathlib import Path

from .scenarios import run


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run isolated Data and Knowledge full-function verification."
    )
    parser.add_argument(
        "--run-id",
        help="Lowercase alphanumeric isolation suffix (default: random)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="JSON report path (default: XDG validation cache for this worktree)",
    )
    parser.add_argument(
        "--network-subnet",
        help="Optional confirmed-free private subnet for the isolated network",
    )
    return parser.parse_args()


def main() -> int:
    return run(_parse_args())
