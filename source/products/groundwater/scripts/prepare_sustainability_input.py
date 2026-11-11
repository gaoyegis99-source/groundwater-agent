"""Prepare and audit a candidate water-source assessment input on this host."""

from pathlib import Path
import sys

SOURCE = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(SOURCE / "packages/operator-pack/src"))

from moperators.operators.sustainability_preparation import main, prepare  # noqa: E402

__all__ = ["prepare"]

if __name__ == "__main__":
    raise SystemExit(main())
