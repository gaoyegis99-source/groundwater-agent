import hashlib
from datetime import datetime, timezone
from pathlib import Path
import sys

import pytest

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
    verify_import_baseline,
)
from research.research_package import (  # noqa: E402
    ResearchAnswerV1,
    ResearchProposalV1,
    build_reproducible_research_package,
)


def digest(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def manifest_for(path_name="source.md", source_digest=None):
    source_digest = source_digest or digest(b"authorised external corpus\n")
    return ControlledCorpusManifestV1.model_validate(
        {
            "corpus_id": "groundwater-rd-2026",
            "frozen_at": datetime(2026, 10, 24, tzinfo=timezone.utc),
            "sources": [
                {
                    "source_id": "manual-1",
                    "title": "Authorised hydrogeology manual",
                    "authorization_id": "licence-2026-01",
                    "authorization_status": "AUTHORIZED",
                    "document_version": "2026.1",
                    "sha256": source_digest,
                    "relative_path": path_name,
                    "time_range": {"start": "2020-01-01", "end": "2026-01-01"},
                    "coordinate_reference_system": "EPSG:4490",
                    "units": ["m"],
                    "quality_rules": ["expert-reviewed", "version-pinned"],
                    "data_bindings": [
                        {
                            "project_id": "local-data",
                            "dataset_id": "observations",
                            "version_id": "2026-01",
                            "semantic_id": "semantic-1",
                        }
                    ],
                }
            ],
            "documents": [
                {
                    "knowledge_id": "groundwater-manual",
                    "content_version": "2026.1",
                    "source_id": "manual-1",
                    "expected_unit_count": 2,
                }
            ],
            "expected_document_count": 1,
            "expected_unit_count": 2,
        }
    )


def citation():
    return {
        "evidence": {
            "version_id": "version-1",
            "unit_id": "unit-1",
            "checksum": "sha256:" + "a" * 64,
        },
        "read_receipt_id": "read-1",
        "lifecycle_revision": 1,
    }


def test_controlled_corpus_verifies_external_bytes_and_import_baseline(tmp_path):
    root = tmp_path / "private-authorised-corpus"
    root.mkdir()
    content = b"authorised external corpus\n"
    (root / "source.md").write_bytes(content)
    manifest = manifest_for(source_digest=digest(content))

    report = verify_external_sources(manifest, root)
    assert report["status"] == "PASS"
    assert report["source_count"] == 1
    assert report["rows"][0]["bytes"] == len(content)

    baseline = verify_import_baseline(
        manifest,
        [
            {
                "knowledge_id": "groundwater-manual",
                "content_version": "2026.1",
                "unit_count": 2,
            }
        ],
    )
    assert baseline["status"] == "PASS"
    assert baseline["observed"] == {"documents": 1, "units": 2}


def test_import_baseline_accepts_v6_version_response_shape():
    manifest = manifest_for()
    report = verify_import_baseline(
        manifest,
        [
            {
                "documentId": "groundwater-manual",
                "versionId": "generated-version-id",
                "body": {
                    "knowledgeId": "groundwater-manual",
                    "contentVersion": "2026.1",
                    "units": [{}, {}],
                },
            }
        ],
    )
    assert report["status"] == "PASS"


def test_controlled_corpus_rejects_digest_escape_and_repo_local_sources(tmp_path):
    root = tmp_path / "external"
    root.mkdir()
    (root / "source.md").write_text("different")
    with pytest.raises(ValueError, match="CORPUS_SOURCE_DIGEST_MISMATCH"):
        verify_external_sources(manifest_for(), root)
    with pytest.raises(ValueError, match="CORPUS_SOURCE_PATH_INVALID"):
        manifest_for("../escape.md")


def test_research_answer_and_package_require_exact_active_evidence():
    answer = ResearchAnswerV1.model_validate(
        {
            "classification": "EVIDENCED_FACT",
            "summary": "The reviewed source identifies the monitoring datum.",
            "citations": [citation()],
            "confidence": "MEDIUM",
            "limitations": ["Applies only to the frozen source version."],
        }
    )
    proposal = ResearchProposalV1.model_validate(
        {
            "proposal_id": "proposal-1",
            "question": "Assess the observed head trend.",
            "domain_package": {
                "domain_id": "groundwater",
                "domain_version": "5.0.0",
                "domain_checksum": "sha256:" + "c" * 64,
                "product_version": "v12",
            },
            "model": {
                "model_id": "groundwater-trend",
                "model_version": "1.0.0",
                "model_checksum": "sha256:" + "b" * 64,
            },
            "data_selections": [
                {
                    "project_id": "local-data",
                    "dataset_id": "observations",
                    "version_id": "2026-01",
                    "semantic_id": "semantic-1",
                }
            ],
            "knowledge_citations": [citation()],
            "parameters": {"window": 12},
        }
    )
    package = build_reproducible_research_package(proposal, answer=answer)
    assert package["status"] == "AWAITING_CONFIRMATION"
    assert package["proposal"]["executionAllowed"] is False
    assert package["package_digest"].startswith("sha256:")

    with pytest.raises(ValueError, match="EVIDENCED_FACT_REQUIRES_EXACT_EVIDENCE"):
        ResearchAnswerV1.model_validate(
            {
                "classification": "EVIDENCED_FACT",
                "summary": "Unsupported claim",
                "confidence": "HIGH",
            }
        )
    insufficient = ResearchAnswerV1.model_validate(
        {
            "classification": "INSUFFICIENT_EVIDENCE",
            "summary": "The frozen corpus does not answer this question.",
            "confidence": "NOT_DETERMINED",
        }
    )
    with pytest.raises(ValueError, match="RESEARCH_PROPOSAL_REQUIRES_EVIDENCE"):
        build_reproducible_research_package(proposal, answer=insufficient)
