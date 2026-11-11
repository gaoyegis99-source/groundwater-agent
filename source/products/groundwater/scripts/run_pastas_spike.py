"""Run the pinned, isolated Pastas experiment; do not publish a product model."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


PRODUCT_ROOT = Path(__file__).resolve().parents[1]
if str(PRODUCT_ROOT) not in sys.path:
    sys.path.insert(0, str(PRODUCT_ROOT))

from research.pastas_spike import PastasSpikeError, run_pastas_spike  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run the independent Pastas 2.0.1 single-well spike"
    )
    parser.add_argument("--input", required=True, type=Path, help="Daily input CSV")
    parser.add_argument(
        "--config", required=True, type=Path, help="Pinned spike config"
    )
    parser.add_argument(
        "--output",
        required=True,
        type=Path,
        help="New output directory; existing paths are refused",
    )
    args = parser.parse_args()
    try:
        manifest = run_pastas_spike(args.input, args.config, args.output)
    except PastasSpikeError as error:
        print(
            json.dumps(
                {"status": "FAILED", "code": str(error)},
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
        return 2
    print(
        json.dumps(
            {
                "status": manifest["status"],
                "scope": manifest["scope"],
                "output": str(args.output),
                "validation_metrics": manifest["artifacts"]["metrics"],
            },
            ensure_ascii=False,
        )
    )
    return 0 if manifest["status"] == "PASS" else 3


if __name__ == "__main__":
    raise SystemExit(main())
