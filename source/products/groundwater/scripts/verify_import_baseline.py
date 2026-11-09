"""Compare a completed V6 Knowledge import with a frozen corpus manifest.

The input is a review export of VersionResponse objects (or their compact
equivalent), never a source document.  This preserves the repository-external
corpus boundary while making the 99-document/291-unit gate reproducible.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PRODUCT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = PRODUCT_ROOT.parents[1]
for path in (
    PRODUCT_ROOT,
    SOURCE_ROOT / "services/knowledge-next/src",
    SOURCE_ROOT / "packages/protocol-contracts/src",
):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from research.research_corpus import (  # noqa: E402
    ControlledCorpusManifestV1,
    verify_import_baseline,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Compare completed Knowledge version views with a frozen authorised corpus"
    )
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument(
        "--versions",
        required=True,
        type=Path,
        help="JSON array of compact rows or V6 VersionResponse objects",
    )
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    manifest = ControlledCorpusManifestV1.model_validate_json(args.manifest.read_text())
    rows = json.loads(args.versions.read_text())
    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
        raise SystemExit("--versions must contain a JSON array of objects")
    report = verify_import_baseline(manifest, rows)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(
        json.dumps(
            {key: report[key] for key in ("status", "corpus_id", "expected", "observed")}
        )
    )
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
