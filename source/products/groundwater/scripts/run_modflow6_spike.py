from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


PRODUCT_ROOT = Path(__file__).resolve().parents[1]
if str(PRODUCT_ROOT) not in sys.path:
    sys.path.insert(0, str(PRODUCT_ROOT))

from research.modflow6_spike import (  # noqa: E402
    Modflow6SpikeError,
    run_modflow6_spike,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run the independent FloPy 3.12.0 / MF6 6.8.1 analytical spike"
    )
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--image", required=True)
    args = parser.parse_args()
    try:
        manifest = run_modflow6_spike(args.config, args.output, args.image)
    except Modflow6SpikeError as error:
        print(str(error), file=sys.stderr)
        return 2
    print(
        json.dumps(
            {
                "result": str((args.output / "result.json").resolve()),
                "scientificStatus": manifest["acceptance"]["scientificStatus"][
                    "status"
                ],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
