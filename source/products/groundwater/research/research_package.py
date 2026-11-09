"""Evidence-bounded answers and confirmation-gated groundwater research packages.

The module is intentionally transport- and model-provider-neutral.  It makes
the safe states explicit before a UI, Kernel adapter, or future proposal route
chooses to render them: evidence can support a fact, a hypothesis can remain a
hypothesis, and missing material cannot be upgraded into a confident answer.
"""

from __future__ import annotations

import hashlib
import json
from typing import Annotated, Any, Literal

from pydantic import Field, model_validator

from platform_knowledge_next.models import DataBinding, Digest, EvidenceRef, ID, Model


def _digest(value: object) -> str:
    return "sha256:" + hashlib.sha256(
        json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
        ).encode("utf-8")
    ).hexdigest()


class ResearchCitationV1(Model):
    evidence: EvidenceRef
    read_receipt_id: ID
    evidence_status: Literal["OK"] = "OK"
    lifecycle_revision: Annotated[int, Field(ge=1)]


class ResearchAnswerV1(Model):
    schema_version: Literal["groundwater-research-answer.v1"] = "groundwater-research-answer.v1"
    classification: Literal[
        "EVIDENCED_FACT", "HYPOTHESIS", "INSUFFICIENT_EVIDENCE", "ANALYSIS_RECOMMENDATION"
    ]
    summary: Annotated[str, Field(min_length=1, max_length=8000)]
    citations: list[ResearchCitationV1] = Field(default_factory=list, max_length=64)
    confidence: Literal["HIGH", "MEDIUM", "LOW", "NOT_DETERMINED"]
    limitations: list[Annotated[str, Field(min_length=1, max_length=1000)]] = Field(
        default_factory=list, max_length=32
    )
    clarification_questions: list[Annotated[str, Field(min_length=1, max_length=1000)]] = Field(
        default_factory=list, max_length=16
    )

    @model_validator(mode="after")
    def evidence_boundary(self):
        identities = [
            (citation.evidence.version_id, citation.evidence.unit_id, citation.evidence.checksum)
            for citation in self.citations
        ]
        if len(identities) != len(set(identities)):
            raise ValueError("DUPLICATE_RESEARCH_CITATION")
        if self.classification == "EVIDENCED_FACT" and not self.citations:
            raise ValueError("EVIDENCED_FACT_REQUIRES_EXACT_EVIDENCE")
        if self.classification == "INSUFFICIENT_EVIDENCE":
            if self.citations or self.confidence != "NOT_DETERMINED":
                raise ValueError("INSUFFICIENT_EVIDENCE_CANNOT_ASSERT_FACT")
        if self.classification == "HYPOTHESIS" and self.confidence == "HIGH":
            raise ValueError("HYPOTHESIS_CANNOT_BE_HIGH_CONFIDENCE")
        return self


class GroundwaterModelIdentity(Model):
    model_id: ID
    model_version: ID
    model_checksum: Digest


class GroundwaterDomainPackageIdentity(Model):
    """Immutable domain/product declaration that governs the research input."""

    domain_id: Literal["groundwater"] = "groundwater"
    domain_version: ID
    domain_checksum: Digest
    product_version: ID


class ResearchProposalV1(Model):
    """Frozen inputs for a future execution, never an execution authorisation."""

    schema_version: Literal["groundwater-research-proposal.v1"] = (
        "groundwater-research-proposal.v1"
    )
    proposal_id: ID
    question: Annotated[str, Field(min_length=1, max_length=4000)]
    domain_package: GroundwaterDomainPackageIdentity
    model: GroundwaterModelIdentity
    data_selections: list[DataBinding] = Field(min_length=1, max_length=64)
    knowledge_citations: list[ResearchCitationV1] = Field(min_length=1, max_length=64)
    parameters: dict[str, Any] = Field(default_factory=dict, max_length=128)
    requires_human_confirmation: Literal[True] = True
    execution_allowed: Literal[False] = False

    @model_validator(mode="after")
    def frozen_inputs_are_unique(self):
        bindings = [binding.model_dump_json() for binding in self.data_selections]
        citations = [citation.evidence.model_dump_json() for citation in self.knowledge_citations]
        if len(bindings) != len(set(bindings)):
            raise ValueError("DUPLICATE_RESEARCH_DATA_SELECTION")
        if len(citations) != len(set(citations)):
            raise ValueError("DUPLICATE_RESEARCH_CITATION")
        if not all(citation.evidence_status == "OK" for citation in self.knowledge_citations):
            raise ValueError("RESEARCH_PROPOSAL_REQUIRES_ACTIVE_EVIDENCE")
        return self

    @property
    def input_digest(self) -> str:
        return _digest(self.model_dump(mode="json", by_alias=True))


def build_reproducible_research_package(
    proposal: ResearchProposalV1, *, answer: ResearchAnswerV1
) -> dict[str, Any]:
    """Return a reviewable package; it cannot execute a model or write data."""

    if answer.classification == "INSUFFICIENT_EVIDENCE":
        raise ValueError("RESEARCH_PROPOSAL_REQUIRES_EVIDENCE")
    cited = {
        (citation.evidence.version_id, citation.evidence.unit_id, citation.evidence.checksum)
        for citation in answer.citations
    }
    required = {
        (citation.evidence.version_id, citation.evidence.unit_id, citation.evidence.checksum)
        for citation in proposal.knowledge_citations
    }
    if not required <= cited:
        raise ValueError("RESEARCH_PROPOSAL_CITATION_NOT_IN_ANSWER")
    package = {
        "schema_version": "groundwater-reproducible-research-package.v1",
        "status": "AWAITING_CONFIRMATION",
        "proposal": proposal.model_dump(mode="json", by_alias=True),
        "proposal_input_digest": proposal.input_digest,
        "answer": answer.model_dump(mode="json", by_alias=True),
    }
    package["package_digest"] = _digest(package)
    return package
