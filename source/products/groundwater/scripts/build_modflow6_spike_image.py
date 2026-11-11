from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


PRODUCT_ROOT = Path(__file__).resolve().parents[1]
if str(PRODUCT_ROOT) not in sys.path:
    sys.path.insert(0, str(PRODUCT_ROOT))

from research.modflow6_spike import (  # noqa: E402
    Modflow6SpikeError,
    extract_verified_mf6_build_inputs,
    verify_mf6_docker_image,
)


SPIKE_ROOT = PRODUCT_ROOT / "spikes/modflow6"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build the independent, offline USGS MF6 6.8.1 Docker spike image"
    )
    parser.add_argument("--archive", required=True, type=Path)
    parser.add_argument("--license", required=True, type=Path)
    parser.add_argument("--disclaimer", required=True, type=Path)
    parser.add_argument("--tag", default="groundwater-mf6-spike:6.8.1")
    args = parser.parse_args()

    temporary_root = Path(tempfile.mkdtemp(prefix="groundwater-mf6-image-build-"))
    context = temporary_root / "context"
    try:
        evidence = extract_verified_mf6_build_inputs(
            args.archive.resolve(),
            args.license.resolve(),
            args.disclaimer.resolve(),
            context,
        )
        shutil.copyfile(SPIKE_ROOT / "Dockerfile", context / "Dockerfile")
        build = subprocess.run(
            [
                "docker",
                "build",
                "--network=none",
                "--pull=false",
                "--file",
                str(context / "Dockerfile"),
                "--tag",
                args.tag,
                str(context),
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=300,
        )
        if build.returncode != 0:
            print(build.stdout, file=sys.stderr)
            print(build.stderr, file=sys.stderr)
            return 2
        image = verify_mf6_docker_image(args.tag)
    except (Modflow6SpikeError, OSError, subprocess.TimeoutExpired) as error:
        print(str(error), file=sys.stderr)
        return 2
    finally:
        shutil.rmtree(temporary_root, ignore_errors=True)

    print(
        json.dumps(
            {
                "status": "PASS",
                "scope": "INDEPENDENT_ADV_02_SPIKE_ONLY",
                "release": evidence,
                "image": image,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
