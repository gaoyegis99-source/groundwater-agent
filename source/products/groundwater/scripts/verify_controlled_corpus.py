"""Verify a V6 authorised corpus manifest without importing source text."""

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
    verify_external_sources,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Verify byte identities for an externally held authorised research corpus"
    )
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--source-root", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    manifest = ControlledCorpusManifestV1.model_validate_json(args.manifest.read_text())
    report = verify_external_sources(manifest, args.source_root)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({key: report[key] for key in ("status", "corpus_id", "source_count")}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
