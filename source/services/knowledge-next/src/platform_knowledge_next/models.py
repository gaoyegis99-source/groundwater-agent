from datetime import date
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from platform_protocol_contracts import to_camel

ID = Annotated[
    str, Field(min_length=1, max_length=160, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$")
]
Digest = Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]


class Model(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel, extra="forbid", populate_by_name=True
    )


class Source(Model):
    source_id: ID
    kind: str = Field(min_length=1, max_length=80)
    title: str = Field(min_length=1, max_length=500)
    publisher: str = Field(min_length=1, max_length=300)
    url: str | None = Field(default=None, max_length=2048, pattern=r"^https://")
    version: str | None = Field(default=None, max_length=100)
    published_at: date | None = None
    accessed_at: date | None = None
    review_after: date
    valid_through: date | None = None
    lifecycle_status: Literal["ACTIVE", "WITHDRAWN"] = "ACTIVE"

    @model_validator(mode="after")
    def valid_source(self):
        if self.kind.startswith("OFFICIAL_") and not (self.url and self.accessed_at):
            raise ValueError("OFFICIAL_SOURCE_LOCATION_REQUIRED")
        if (
            self.published_at
            and self.accessed_at
            and self.published_at > self.accessed_at
        ):
            raise ValueError("SOURCE_DATE_ORDER_INVALID")
        return self


class DataBinding(Model):
    service_binding: Literal["data-primary"] = "data-primary"
    project_id: ID
    dataset_id: ID
    version_id: ID
    semantic_id: ID
    field_id: Annotated[str, Field(pattern=r"^f[0-9]+$")] | None = None


class Unit(Model):
    unit_id: ID
    content: str = Field(min_length=1, max_length=4000)
    source_refs: list[ID] = Field(min_length=1, max_length=16)
    locator: dict[str, str | int] = Field(default_factory=dict, max_length=8)
    required_units: list[ID] = Field(default_factory=list, max_length=16)


class Document(Model):
    knowledge_id: ID
    expected_version: ID | None = None
    kind: Literal["knowledge", "definition", "method", "case", "rule"] = "knowledge"
    title: str = Field(min_length=1, max_length=500)
    summary: str = Field(default="", max_length=4000)
    language: str = Field(default="zh-CN", max_length=35)
    content_version: str = Field(min_length=1, max_length=64)
    aliases: list[Annotated[str, Field(min_length=1, max_length=160)]] = Field(
        default_factory=list, max_length=32
    )
    model_id: ID | None = None
    model_version: str | None = Field(default=None, max_length=64)
    model_checksum: Digest | None = None
    sources: list[Source] = Field(min_length=1, max_length=16)
    units: list[Unit] = Field(min_length=1, max_length=64)
    bindings: list[DataBinding] = Field(default_factory=list, max_length=32)
    provenance: dict[str, str] = Field(default_factory=dict, max_length=8)

    @model_validator(mode="after")
    def valid_references(self):
        sources = {s.source_id for s in self.sources}
        units = {u.unit_id for u in self.units}
        if len(sources) != len(self.sources) or len(units) != len(self.units):
            raise ValueError("DUPLICATE_SOURCE_OR_UNIT")
        for u in self.units:
            if not set(u.source_refs) <= sources or not set(u.required_units) <= units:
                raise ValueError("UNKNOWN_SOURCE_OR_REQUIRED_UNIT")
        if sum(len(u.content) for u in self.units) > 256000:
            raise ValueError("DOCUMENT_TOO_LARGE")
        if any((self.model_id, self.model_version, self.model_checksum)) and not all(
            (self.model_id, self.model_version, self.model_checksum)
        ):
            raise ValueError("MODEL_IDENTITY_INCOMPLETE")
        return self


def document_content(document: Document) -> dict[str, Any]:
    """Return versioned document content without the import CAS precondition.

    ``expected_version`` protects one import attempt.  It is not part of the
    resulting knowledge version and must not make an otherwise identical
    bootstrap package appear different on the next process start.
    """

    return document.model_dump(mode="json", exclude={"expected_version"})


def stored_document_content(body: dict[str, Any]) -> dict[str, Any]:
    """Normalize versions written before CAS metadata was removed from bodies."""

    return {
        key: value
        for key, value in body.items()
        if key not in {"expected_version", "expectedVersion"}
    }


class KnowledgePackageV1(Model):
    schema_version: Literal["knowledge-package.v1"] = "knowledge-package.v1"
    project_id: ID
    idempotency_key: ID
    documents: list[Document] = Field(min_length=1, max_length=128)

    @model_validator(mode="after")
    def unique_documents(self):
        if len({d.knowledge_id for d in self.documents}) != len(self.documents):
            raise ValueError("DUPLICATE_DOCUMENT")
        if any(
            b.project_id != self.project_id for d in self.documents for b in d.bindings
        ):
            raise ValueError("BINDING_PROJECT_MISMATCH")
        return self


class Budget(Model):
    max_units: int = Field(default=8, ge=1, le=16)
    max_chars: int = Field(default=16000, ge=1, le=32000)


class RetrievalMetadataFilter(Model):
    source_kinds: list[str] = Field(default_factory=list, max_length=8)
    language: str | None = Field(default=None, max_length=35)
    review_due: bool | None = None


class Retrieval(Model):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "allOf": [
                {
                    "if": {
                        "properties": {"scope": {"const": "general"}},
                        "required": ["scope"],
                    },
                    "then": {"properties": {"binding": {"type": "null"}}},
                },
                {
                    "if": {
                        "properties": {"scope": {"enum": ["bound", "combined"]}},
                        "required": ["scope"],
                    },
                    "then": {
                        "required": ["binding"],
                        "properties": {"binding": {"type": "object"}},
                    },
                },
            ]
        },
    )
    scope: Literal["general", "bound", "combined"] | None = None
    query: str = Field(min_length=1, max_length=512)
    mode: Literal["hybrid", "lexical"] = "hybrid"
    purpose: str = Field(default="", max_length=256)
    kind: str | None = Field(default=None, max_length=80)
    binding: DataBinding | None = None
    budget: Budget = Field(default_factory=Budget)
    top_k: int = Field(default=5, ge=1, le=16)
    min_score: float = Field(default=0, ge=0, le=1)
    metadata_filters: RetrievalMetadataFilter = Field(
        default_factory=RetrievalMetadataFilter
    )

    @model_validator(mode="after")
    def valid_scope(self):
        if self.scope == "general" and self.binding is not None:
            raise ValueError("GENERAL_SCOPE_FORBIDS_BINDING")
        if self.scope in ("bound", "combined") and self.binding is None:
            raise ValueError("SCOPE_REQUIRES_BINDING")
        return self

    @property
    def effective_scope(self):
        return self.scope or ("combined" if self.binding else "general")


class EvidenceRef(Model):
    version_id: ID
    unit_id: ID
    checksum: Digest


class EvidenceRead(Model):
    refs: list[EvidenceRef] = Field(min_length=1, max_length=16)
    budget: Budget = Field(default_factory=Budget)


class Lookup(Model):
    key: str | None = Field(default=None, min_length=1, max_length=160)
    binding: DataBinding | None = None
    budget: Budget = Field(default_factory=Budget)

    @model_validator(mode="after")
    def has_selector(self):
        if self.key is None and self.binding is None:
            raise ValueError("LOOKUP_SELECTOR_REQUIRED")
        return self


class LifecycleUpdate(Model):
    expected_revision: int = Field(ge=1)
    status: Literal["ACTIVE", "WITHDRAWN"]
    valid_through: date | None = None
    review_after: date
    reason: str = Field(min_length=1, max_length=1000)


class EvidenceItem(Model):
    scope: Literal["GENERAL", "DATA_BOUND"] | None = None
    binding_match: Literal["GENERAL", "EXACT", "NOT_CHECKED", "MISMATCH"] | None = None
    status: Literal[
        "OK", "STALE", "WITHDRAWN", "EXPIRED", "NOT_FOUND", "NEEDS_EXPANSION"
    ]
    version_id: str
    unit_id: str
    checksum: Digest
    evidence_id: str | None = None
    knowledge_id: str | None = None
    content: str | None = None
    locator: dict[str, str | int] | None = None
    sources: list[Source] | None = None
    bindings: list[DataBinding] | None = None
    bindings_verified_at: str | None = None
    lifecycle_revision: int | None = None
    review_due: bool | None = None
    required_units: list[str] = Field(default_factory=list)
    document_title: str | None = None
    relevance_score: float | None = Field(default=None, ge=0, le=1)
    rank: int | None = Field(default=None, ge=1)
    ranking_reason: str | None = None
    ranking_signals: dict[str, Any] | None = None


class EvidenceBundleV1(Model):
    schema_version: Literal["evidence-bundle.v1"] = "evidence-bundle.v1"
    status: Literal["OK", "NO_MATCH", "PARTIAL"]
    receipt_id: str
    knowledge_revision: int
    index_generation: str
    observed_at: str
    items: list[EvidenceItem]
    coverage: dict[str, Any]
    budget: dict[str, int]
    timings_ms: dict[str, float]
