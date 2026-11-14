"""Public response contracts shared with clients through OpenAPI."""

from datetime import datetime
from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field
from platform_protocol_contracts import ToolOutcome, to_camel
from ..models import Semantics, SpatialExtent


class Response(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel, extra="forbid", populate_by_name=True
    )


class ObjectDescriptor(Response):
    object_id: str
    size: int
    checksum: str
    role: Literal["raw", "query"] | None = None
    format: str | None = None
    media_type: str | None = None
    source_checksum: str | None = None
    converter: str | None = None
    conversion: dict[str, Any] | None = None
    arrow_version: str | None = None


class PublishedOutput(Response):
    name: str
    dataset_id: str
    version_id: str
    semantic_id: str


class PublicationReceipt(Response):
    publication_id: str
    state: Literal["PUBLISHED"]
    outputs: list[PublishedOutput]


class PublicationEvent(Response):
    sequence: int
    state: str
    created_at: datetime


class PublicationOperation(Response):
    operation_id: str
    state: str
    error: dict[str, Any] | None


class PublicationResponse(Response):
    publication_id: str
    state: Literal["OPEN", "PREPARING", "READY", "PUBLISHED", "FAILED", "ABORTED"]
    manifest: dict[str, Any]
    uploads: list[str]
    operations: list[PublicationOperation]
    receipt: PublicationReceipt | None
    idempotency_key: str
    manifest_checksum: str
    created_at: datetime
    events: list[PublicationEvent]


class OperationAccepted(Response):
    operation_id: str


class QueryAccepted(OperationAccepted):
    query_id: str


class PreviewAccepted(OperationAccepted):
    preview_id: str


class OperationEvent(Response):
    sequence: int
    state: str
    generation: int
    created_at: datetime


class OperationResponse(Response):
    operation_id: str
    kind: Literal["prepare", "query", "preview"]
    target: str
    state: Literal["QUEUED", "RUNNING", "SUCCEEDED", "FAILED", "CANCELLED"]
    generation: int
    result: dict[str, Any] | None
    error: dict[str, Any] | None
    events: list[OperationEvent]
    actor: dict[str, Any]
    created_at: datetime
    updated_at: datetime


class QueryReceipt(Response):
    query_id: str
    inputs: list[dict[str, Any]]
    plan: dict[str, Any]
    plan_digest: str
    coverage: dict[str, Any]
    truncated: bool
    returned_rows: int
    result_checksum: str
    engine: dict[str, str]


class QueryPage(Response):
    fields: list[dict[str, str]]
    rows: list[list[Any]]
    next_offset: int | None
    truncated: bool
    coverage: dict[str, Any]
    engine: dict[str, str]


class QueryResponse(Response):
    query_id: str
    state: Literal["QUEUED", "RUNNING", "SUCCEEDED", "FAILED", "CANCELLED"]
    error: dict[str, Any] | None
    receipt: QueryReceipt | None
    expires_at: datetime | None
    result_available: bool
    result: QueryPage | None


class PreviewResponse(Response):
    preview_id: str
    state: Literal["QUEUED", "RUNNING", "SUCCEEDED", "FAILED", "CANCELLED"]
    error: dict[str, Any] | None
    receipt: dict[str, Any] | None
    expires_at: datetime | None
    result_available: bool
    result: dict[str, Any] | None


class DatasetSummary(Response):
    dataset_id: str
    project: str
    title: str
    tags: list[str]
    current_version: str
    created_at: datetime
    semantics: dict[str, Any]
    formats: list[str]


class DatasetPage(Response):
    items: list[DatasetSummary]
    next_cursor: str | None
    match_basis: str


class ResourceAccessResponse(Response):
    resource_kind: Literal["DATASET", "MODEL"]
    resource_id: str
    owner_subject: str
    visibility: Literal["PRIVATE", "PROJECT"]


class QualitySuiteVersionResponse(Response):
    suite_id: str
    suite_version_id: str
    version_number: int
    title: str
    rules: list[dict[str, Any]]
    table_validation_rules: dict[str, Any]
    body_digest: str
    actor: dict[str, Any]
    created_at: datetime


class QualityBindingResponse(Response):
    dataset_id: str
    revision: int
    suite_version_id: str
    enabled: bool
    actor: dict[str, Any]
    created_at: datetime


class QualityRuleResultResponse(Response):
    rule_id: str
    severity: Literal["ERROR", "WARN", "INFO"]
    status: Literal["PASS", "FAIL", "ERROR"]
    violation_count: int
    code: str | None
    evidence: dict[str, Any]


class QualityRunResponse(Response):
    quality_run_id: str
    dataset_id: str
    version_id: str
    semantic_id: str
    binding_revision: int
    suite_version_id: str
    operator_run_id: str
    operator_id: Literal["table.validate"]
    operator_version: Literal["1.0.0"]
    implementation_digest: str
    status: Literal["PASSED", "PASSED_WITH_WARNINGS", "FAILED", "ERROR"]
    summary: dict[str, Any]
    results: list[QualityRuleResultResponse]
    actor: dict[str, Any]
    created_at: datetime


class QualityRunPage(Response):
    items: list[QualityRunResponse]
    next_cursor: str | None


class QualityAlertResponse(Response):
    quality_alert_id: str
    dataset_id: str
    version_id: str
    quality_run_id: str
    rule_id: str
    severity: Literal["ERROR", "WARN", "INFO"]
    status: Literal["FAIL", "ERROR"]
    violation_count: int
    created_at: datetime


class QualityAlertPage(Response):
    items: list[QualityAlertResponse]
    next_cursor: str | None


class MlExperimentResponse(Response):
    experiment_id: str
    name: str
    description: str
    owner_subject: str
    actor: dict[str, Any]
    created_at: datetime


class ResolvedModelResponse(Response):
    model_id: str
    alias: str
    alias_revision: int
    registry_version: int
    benchmark_status: Literal["NOT_ASSESSED", "PASSED", "FAILED"]
    tags: list[str]
    legacy_stage: str | None
    data_version_id: str
    artifact_object_id: str
    artifact_checksum: str


class MlRunResponse(Response):
    experiment_run_id: str
    experiment_id: str
    input_versions: list[str]
    resolved_model_inputs: list[ResolvedModelResponse]
    parameters: dict[str, Any]
    metrics: dict[str, float]
    environment: dict[str, Any]
    operator_implementations: list[dict[str, Any]]
    actor: dict[str, Any]
    created_at: datetime


class ModelRegistryVersionResponse(Response):
    model_id: str
    registry_version: int
    data_version_id: str
    artifact_object_id: str
    artifact_checksum: str
    experiment_run_id: str | None
    benchmark_status: Literal["NOT_ASSESSED", "PASSED", "FAILED"]
    tags: list[str]
    legacy_stage: str | None
    actor: dict[str, Any]
    created_at: datetime


class ModelAliasResponse(ResolvedModelResponse):
    actor: dict[str, Any]
    created_at: datetime


class VersionSummary(Response):
    version_id: str
    created_at: datetime
    semantic_id: str


class VersionPage(Response):
    items: list[VersionSummary]
    next_cursor: str | None


class CatalogCount(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel, extra="forbid", populate_by_name=True
    )
    name: str
    count: int


class InventoryResponse(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel, extra="forbid", populate_by_name=True
    )
    dataset_count: int
    tagged_dataset_count: int
    described_dataset_count: int
    formats: list[CatalogCount]
    tags: list[CatalogCount]
    projection_basis: str


class DiscoveryField(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel, extra="forbid", populate_by_name=True
    )
    field_id: str
    name: str
    meaning: str
    unit: str | None
    quantity_kind: str | None = None
    role: str | None = None


class DiscoveryReason(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel, extra="forbid", populate_by_name=True
    )
    kind: str
    term: str | None = None
    values: list[str] | None = None


class DiscoveryQuality(Response):
    quality_run_id: str
    suite_version_id: str
    status: Literal["PASSED", "PASSED_WITH_WARNINGS", "FAILED", "ERROR"]


class DiscoveryItem(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel, extra="forbid", populate_by_name=True
    )
    dataset_id: str
    title: str
    tags: list[str]
    current_version: str
    semantic_id: str
    formats: list[str]
    description: str
    grain: str | None
    time_range: str | None
    crs: str | None
    vertical_datum: str | None = None
    spatial_extent: SpatialExtent | None = None
    quality: DiscoveryQuality | None = None
    matched_fields: list[DiscoveryField]
    score: int
    match_reasons: list[DiscoveryReason]
    unknowns: list[str]


class DiscoveryResponse(Response):
    model_config = ConfigDict(
        alias_generator=to_camel, extra="forbid", populate_by_name=True
    )
    items: list[DiscoveryItem]
    next_cursor: str | None
    total_matches: int
    match_basis: str


class VersionResponse(Response):
    version_id: str
    dataset_id: str
    current_version: str
    publication_id: str
    semantic_id: str
    title: str
    descriptor: dict[str, Any]
    semantics: Semantics
    objects: list[ObjectDescriptor]
    input_versions: list[str]
    created_at: datetime
    tags: list[str]
    project: str


class OperatorActivityItem(Response):
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


class OperatorActivityPage(Response):
    items: list[OperatorActivityItem]
    generated_at: datetime


class DatasetQualityPoint(Response):
    version_id: str
    created_at: datetime
    row_count: int | None
    quality: dict[str, Any]


class DatasetLineageEdge(Response):
    output_version_id: str
    input_version_id: str
    input_dataset_id: str
    input_title: str
    created_at: datetime
    is_current: bool


class DatasetGovernanceResponse(Response):
    dataset_id: str
    title: str
    current_version: str
    owner_subject: str
    quality_trend: list[DatasetQualityPoint]
    lineage: list[DatasetLineageEdge]
    activity: list[OperatorActivityItem]
    generated_at: datetime


class HealthResponse(Response):
    status: str


class ReadinessResponse(HealthResponse):
    components: dict[str, str]


class CurrentUser(Response):
    subject: str
    display_name: str
    roles: list[str]


class DataPermissions(Response):
    catalog_read: bool
    query_create: bool
    preview_create: bool
    object_download: bool
    user_upload_create: bool
    managed_publication_write: bool
    semantic_write: bool
    access_write: bool
    governance_read: bool
    quality_write: bool
    mlops_write: bool


class CapabilitiesResponse(Response):
    version: str
    product_id: str
    project: str
    current_user: CurrentUser
    permissions: DataPermissions
    identity_mode: str
    write_enabled: bool
    formats: list[str]
    format_media_types: dict[str, str]
    query: dict[str, Any]
    preview: dict[str, Any]
    limits: dict[str, int]
    contracts: dict[str, Any]
    openapi: str


class ToolResponse(Response):
    data: dict[str, Any]
    outcome: ToolOutcome


class QueryCancelResponse(Response):
    query_id: str
    state: str


class PreviewCancelResponse(Response):
    preview_id: str
    state: str


class SemanticRevisionResponse(Response):
    semantic_id: str
    version_id: str
    body: dict[str, Any]
    actor: dict[str, Any]
    created_at: datetime


class SemanticRevisionSummary(Response):
    semantic_id: str
    created_at: datetime
    actor: dict[str, Any]


class SemanticRevisionPage(Response):
    items: list[SemanticRevisionSummary]
    next_cursor: str | None


class SemanticUpdateResponse(Response):
    semantic_id: str
    version_id: str


class PublicationAbortResponse(Response):
    publication_id: str
    state: Literal["ABORTED"]
