#!/usr/bin/env python3
"""Stable CLI for the internal Kernel pressure driver."""

from __future__ import annotations

import sys
from pathlib import Path

if __package__ in {None, ""}:
    source_root = Path(__file__).resolve().parents[2]
    if str(source_root) not in sys.path:
        sys.path.insert(0, str(source_root))

from scripts.evaluation.kernel_sandbox_pressure.command import main


if __name__ == "__main__":
    raise SystemExit(main())
