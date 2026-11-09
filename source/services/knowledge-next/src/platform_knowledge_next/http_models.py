from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pydantic import Field, model_validator
from platform_protocol_contracts import CamelModel, ToolOutcome, reject_snake_wire

from .models import (
    Document,
    EvidenceBundleV1,
    EvidenceRead,
    ID,
    KnowledgePackageV1,
    LifecycleUpdate,
    Lookup,
    Retrieval,
)


class HealthResponse(CamelModel):
    status: str


class ReadinessResponse(HealthResponse):
    components: dict[str, str]


class CurrentUser(CamelModel):
    subject: str
    display_name: str
    roles: list[str]


class KnowledgePermissions(CamelModel):
    catalog_read: bool
    retrieval_create: bool
    evidence_read: bool
    evidence_validate: bool
    import_write: bool
    lifecycle_write: bool
    access_write: bool


class CapabilitiesResponse(CamelModel):
    version: str
    product_id: str
    project: str
    current_user: CurrentUser
    permissions: KnowledgePermissions
    identity: str
    write_enabled: bool
    package_schema: str = Field(alias="schema")
    database_schema: int
    retrieval_scopes: list[str]
    default_scope: str
    data_retry: dict[str, Any]
    model_profile: str
    index_generation: str
    vector_search: Literal["exact", "hnsw"]
    max_body_bytes: int
    data_configured: bool
    openapi: str


class ObservationToolContractResponse(CamelModel):
    contract: str
    schema_version: str
    tools: list[dict[str, Any]]


class ImportPage(CamelModel):
    items: list["ImportResponse"]
    next_cursor: str | None


class OperatorActivityItem(CamelModel):
    service: Literal["data", "knowledge"]
    operation_id: str
    kind: str
    state: str
    title: str
    subject: str
    created_at: datetime
    updated_at: datetime
    href: str
    details: dict[str, Any] = Field(default_factory=dict)


class OperatorActivityPage(CamelModel):
    items: list[OperatorActivityItem]
    generated_at: datetime


class CatalogCount(CamelModel):
    name: str
    count: int


class DocumentFacets(CamelModel):
    statuses: list[CatalogCount] = Field(default_factory=list)
    review_states: list[CatalogCount] = Field(default_factory=list)
    source_kinds: list[CatalogCount] = Field(default_factory=list)
    knowledge_kinds: list[CatalogCount] = Field(default_factory=list)
    binding_states: list[CatalogCount] = Field(default_factory=list)


class DocumentSummary(CamelModel):
    document_id: str
    current_version: str
    title: str
    created_at: datetime
    modified_at: datetime | None = None
    summary: str = ""
    kind: str = "knowledge"
    language: str = "zh-CN"
    status: str | None = None
    review_after: date | None = None
    valid_through: date | None = None
    review_state: str | None = None
    source_kinds: list[str] = Field(default_factory=list)
    source_count: int = 0
    binding_count: int = 0
    has_data_binding: bool = False


class DocumentPage(CamelModel):
    items: list[DocumentSummary]
    next_cursor: str | None
    total_matches: int = 0
    facets: DocumentFacets = Field(default_factory=DocumentFacets)


class VersionSummary(CamelModel):
    version_id: str
    checksum: str
    created_at: datetime


class VersionPage(CamelModel):
    items: list[VersionSummary]
    next_cursor: str | None


class LifecyclePage(CamelModel):
    items: list["LifecycleResponse"]
    next_cursor: str | None


class ImportResponse(CamelModel):
    import_id: str
    state: str
    attempt: int | None = None
    data_attempts: int
    retry_generation: int = 0
    next_retry_at: datetime | None
    retry_deadline: datetime | None
    last_error: dict[str, Any] | None
    error: dict[str, Any] | None = None
    result: dict[str, Any] | None = None
    actor: dict[str, Any]
    created_at: datetime | None = None
    updated_at: datetime | None = None


class PreflightCheck(CamelModel):
    name: str
    state: Literal["PASSED", "FAILED", "PENDING"]
    detail: str


class ImportDocumentDiff(CamelModel):
    knowledge_id: str
    title: str
    operation: Literal["CREATE", "UPDATE"]
    current_version: str | None
    expected_version: str | None
    expected_version_matches: bool
    current_content_version: str | None
    next_content_version: str
    content_version_changed: bool
    content_changed: bool
    source_count: int
    next_review_after: date
    binding_added: int
    binding_removed: int
    binding_unchanged: int


class ImportPreflightResponse(CamelModel):
    project_matches: bool
    can_submit: bool
    documents: list[ImportDocumentDiff]
    checks: list[PreflightCheck]
    generated_at: datetime


class VersionResponse(CamelModel):
    version_id: str
    document_id: str
    body: Document
    checksum: str
    import_id: str
    verified_at: datetime | None
    created_at: datetime
    lifecycle_revision: int
    status: str
    valid_through: date | None
    review_after: date
    lifecycle_actor: dict[str, Any]


class LifecycleBatchDiff(CamelModel):
    document_id: str
    title: str
    version_id: str
    current_revision: int
    current_status: Literal["ACTIVE", "WITHDRAWN"]
    target_status: Literal["ACTIVE", "WITHDRAWN"]
    current_valid_through: date | None
    target_valid_through: date | None
    current_review_after: date
    target_review_after: date
    changed: bool


class LifecycleBatchPreflightResponse(CamelModel):
    can_submit: bool
    items: list[LifecycleBatchDiff]
    generated_at: datetime


class LifecycleBatchResponse(CamelModel):
    items: list[VersionResponse]
    updated_at: datetime


class LifecycleResponse(CamelModel):
    version_id: str
    revision: int
    status: str
    valid_through: date | None
    review_after: date
    reason: str
    actor: dict[str, Any]
    created_at: datetime


class ReceiptResponse(CamelModel):
    receipt_id: str
    request_hash: str
    kind: str
    body: dict[str, Any]
    created_at: datetime


class ResourceAccessResponse(CamelModel):
    resource_kind: Literal["KNOWLEDGE"]
    resource_id: str
    owner_subject: str
    visibility: Literal["PRIVATE", "PROJECT"]


class ToolActor(CamelModel):
    subject: str = Field(min_length=1, max_length=128)
    roles: list[str] = Field(default_factory=lambda: ["researcher"], min_length=1)


class ToolRequest(CamelModel):
    call_id: str = Field(min_length=1, max_length=160)
    project_id: str = Field(min_length=1, max_length=160)
    arguments: dict
    actor: ToolActor | None = None


class ToolResponse(CamelModel):
    data: EvidenceBundleV1
    outcome: ToolOutcome


class StrictWireModel:
    @model_validator(mode="before")
    @classmethod
    def strict_wire_fields(cls, value):
        reject_snake_wire(value, cls)
        return value


class ResourceAccessUpdateRequest(StrictWireModel, CamelModel):
    visibility: Literal["PRIVATE", "PROJECT"]


class KnowledgePackageRequest(StrictWireModel, KnowledgePackageV1):
    pass


class RetryImportRequest(StrictWireModel, CamelModel):
    request_id: ID


class LifecycleUpdateRequest(StrictWireModel, LifecycleUpdate):
    pass


class LifecycleBatchPreflightRequest(StrictWireModel, CamelModel):
    document_ids: list[ID] = Field(min_length=1, max_length=100)
    status: Literal["ACTIVE", "WITHDRAWN"]
    valid_through: date | None = None
    review_after: date
    reason: str = Field(min_length=1, max_length=1000)

    @model_validator(mode="after")
    def unique_documents(self):
        if len(set(self.document_ids)) != len(self.document_ids):
            raise ValueError("DUPLICATE_DOCUMENT_ID")
        return self


class LifecycleBatchItem(StrictWireModel, LifecycleUpdate):
    document_id: ID
    version_id: ID


class LifecycleBatchRequest(StrictWireModel, CamelModel):
    items: list[LifecycleBatchItem] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def unique_documents_and_versions(self):
        document_ids = [item.document_id for item in self.items]
        version_ids = [item.version_id for item in self.items]
        if len(set(document_ids)) != len(document_ids):
            raise ValueError("DUPLICATE_DOCUMENT_ID")
        if len(set(version_ids)) != len(version_ids):
            raise ValueError("DUPLICATE_VERSION_ID")
        return self


class RetrievalRequest(StrictWireModel, Retrieval):
    pass


class EvidenceReadRequest(StrictWireModel, EvidenceRead):
    pass


class LookupRequest(StrictWireModel, Lookup):
    pass
