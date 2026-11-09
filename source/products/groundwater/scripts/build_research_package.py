"""Create a review-only V6 research package from exact evidence identities."""

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

from research.research_package import (  # noqa: E402
    ResearchAnswerV1,
    ResearchProposalV1,
    build_reproducible_research_package,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build an awaiting-confirmation reproducible groundwater research package"
    )
    parser.add_argument("--proposal", required=True, type=Path)
    parser.add_argument("--answer", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    proposal = ResearchProposalV1.model_validate_json(args.proposal.read_text())
    answer = ResearchAnswerV1.model_validate_json(args.answer.read_text())
    package = build_reproducible_research_package(proposal, answer=answer)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(package, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"status": package["status"], "package_digest": package["package_digest"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
