from typing import Annotated, Literal

from platform_protocol_contracts import CamelModel
from pydantic import Field, StrictInt, field_validator, model_validator
from pydantic.json_schema import SkipJsonSchema

from ..work_items.lifecycle import BusinessState, PublicationState
from .schemas import DataVersionReferenceFields, _Utf8String


_Id = Annotated[_Utf8String, Field(min_length=1, max_length=256)]
_Checksum = Annotated[_Utf8String, Field(pattern=r"^sha256:[0-9a-f]{64}$")]
_Revision = Annotated[StrictInt, Field(ge=1, json_schema_extra={"x-strict": True})]
_Visibility = Literal["AVAILABLE", "UNAVAILABLE"]
MAX_WORK_ITEM_CURSOR = 2**53 - 1


def _normalize_title(value: str) -> str:
    title = value.strip()
    if not title or any(ord(character) < 32 or ord(character) == 127 for character in title):
        raise ValueError("WORK_ITEM_TITLE_INVALID")
    return title


class WorkItemRequest(CamelModel):
    project_id: _Id
    request_id: _Id


class WorkItemCreate(WorkItemRequest):
    title: _Utf8String = Field(min_length=1, max_length=120)
    description: _Utf8String | None = Field(default=None, max_length=2000)

    @field_validator("title")
    @classmethod
    def normalize_title(cls, value: str) -> str:
        return _normalize_title(value)


class WorkItemChange(WorkItemRequest):
    expected_revision: _Revision


class WorkItemMetadataUpdate(WorkItemChange):
    title: _Utf8String | SkipJsonSchema[None] = Field(default=None, min_length=1, max_length=120)
    description: _Utf8String | None = Field(default=None, max_length=2000)

    @field_validator("title")
    @classmethod
    def normalize_title(cls, value: str | None) -> str:
        if value is None:
            raise ValueError("WORK_ITEM_TITLE_INVALID")
        return _normalize_title(value)

    @model_validator(mode="after")
    def require_change(self):
        if not self.model_fields_set.intersection({"title", "description"}):
            raise ValueError("WORK_ITEM_UPDATE_EMPTY")
        return self


class WorkItemReview(WorkItemChange):
    session_id: _Id
    review_token: _Checksum


class WorkItemSessionAttach(WorkItemChange):
    session_id: _Id


class WorkItemDataReference(DataVersionReferenceFields):
    kind: Literal["data_version"]
    service: Literal["data-primary"]
    size_bytes: StrictInt = Field(ge=0, json_schema_extra={"x-strict": True})


class WorkItemResultDataReference(WorkItemDataReference):
    output_name: _Id


class BusinessObjectReference(CamelModel):
    object_kind: Literal["REGION", "WELL", "AQUIFER"]
    source: WorkItemDataReference
    field_id: _Id
    external_id: _Id


class WorkItemObjectAttach(WorkItemChange):
    reference: BusinessObjectReference


class DataAssetReference(CamelModel):
    kind: Literal["DATA"]
    data_ref: WorkItemDataReference


class KnowledgeAssetReference(CamelModel):
    kind: Literal["KNOWLEDGE"]
    document_id: _Id
    version_id: _Id
    checksum: _Checksum


class WorkflowAssetSelection(CamelModel):
    kind: Literal["WORKFLOW"]
    run_id: _Id


class ResultAssetSelection(CamelModel):
    kind: Literal["RESULT"]
    run_id: _Id
    output_name: _Id


class WorkItemAssetAttach(WorkItemChange):
    reference: Annotated[
        DataAssetReference | KnowledgeAssetReference | WorkflowAssetSelection | ResultAssetSelection,
        Field(discriminator="kind"),
    ]


class WorkflowIdentity(CamelModel):
    id: _Id
    version: _Id
    checksum: _Checksum


class WorkflowAssetReference(WorkflowAssetSelection):
    workflow_ref: WorkflowIdentity


class ResultAssetReference(ResultAssetSelection):
    publication_id: _Id
    data_ref: WorkItemResultDataReference


class WorkItemReceipt(CamelModel):
    work_item_id: _Id
    revision: _Revision
    changed: bool
    link_id: _Id | None


class WorkItemSessionReceipt(WorkItemReceipt):
    link_id: _Id


class WorkItemSummary(CamelModel):
    work_item_id: _Id
    project_id: _Id
    owner_subject: _Id
    title: str
    description: str | None
    revision: _Revision
    status: Literal["ACTIVE", "ARCHIVED"]
    created_at: str
    updated_at: str
    archived_at: str | None


class WorkItemPage(CamelModel):
    items: list[WorkItemSummary]
    next_after: _Id | None


class WorkItemSessionLink(CamelModel):
    session_id: _Id
    title: str | None
    linked_at: str
    archived_at: str | None


class WorkItemTaskLink(CamelModel):
    task_id: _Id
    session_id: _Id
    revision: _Revision
    status: Literal["ACTIVE", "COMPLETE"]


class WorkItemTaskRunLink(CamelModel):
    task_id: _Id
    task_revision: _Revision
    agent_run_id: _Id


class WorkItemAgentRunLink(CamelModel):
    agent_run_id: _Id
    session_id: _Id
    goal_revision: _Revision


class WorkItemWorkflowRunLink(CamelModel):
    run_id: _Id
    agent_run_id: _Id
    session_id: _Id
    workflow_ref: WorkflowIdentity


class WorkItemObjectLink(CamelModel):
    link_id: _Id
    linked_at: str
    state: _Visibility
    reference: BusinessObjectReference | None


class WorkItemAssetLink(CamelModel):
    link_id: _Id
    linked_at: str
    state: _Visibility
    reference: Annotated[
        DataAssetReference | KnowledgeAssetReference | WorkflowAssetReference | ResultAssetReference,
        Field(discriminator="kind"),
    ] | None


class WorkItemDetail(CamelModel):
    work_item: WorkItemSummary
    sessions: list[WorkItemSessionLink]
    tasks: list[WorkItemTaskLink]
    task_runs: list[WorkItemTaskRunLink]
    agent_runs: list[WorkItemAgentRunLink]
    workflow_runs: list[WorkItemWorkflowRunLink]
    objects: list[WorkItemObjectLink]
    assets: list[WorkItemAssetLink]


class WorkItemOpenTask(CamelModel):
    session_id: _Id
    session_title: str | None
    task_id: _Id
    revision: _Revision
    updated_at: str


class WorkItemApprovedAction(CamelModel):
    session_id: _Id
    session_title: str | None
    event_id: int
    approved_at: str
    goal_revision: int
    decision_revision: _Revision
    position: int
    model_id: _Id
    model_version: _Id
    label: str
    parameters: dict[str, str | bool | int | float]


class WorkItemSessionContext(CamelModel):
    session_id: _Id
    linked_at: str
    work_item: WorkItemSummary
    objects: list[WorkItemObjectLink]
    assets: list[WorkItemAssetLink]
    open_tasks: list[WorkItemOpenTask]
    approved_actions: list[WorkItemApprovedAction]


class WorkItemSessionContextResponse(CamelModel):
    context: WorkItemSessionContext | None


class WorkItemAuditEntry(CamelModel):
    sequence: int
    request_id: _Id
    actor_subject: _Id
    command: Literal["CREATE", "LINK_SESSION", "LINK_OBJECT", "LINK_ASSET", "ARCHIVE"]
    revision: _Revision
    changed: bool
    link_id: _Id | None
    created_at: str


class WorkItemAuditPage(CamelModel):
    entries: list[WorkItemAuditEntry]
    next_after: int | None


class WorkItemPublication(CamelModel):
    run_id: _Id
    state: PublicationState


class WorkItemReviewPreview(CamelModel):
    token: _Checksum
    result_link_ids: list[_Id]


class WorkItemLifecycleTrack(CamelModel):
    session_id: _Id
    task_id: _Id | None
    task_revision: _Revision | None
    state: BusinessState
    reason: str
    confirmation_id: _Id | None
    stop_pending: bool
    publications: list[WorkItemPublication]
    review: WorkItemReviewPreview | None
    accepted_at: str | None


class WorkItemLifecycleSnapshot(CamelModel):
    work_item_id: _Id
    revision: _Revision
    event_cursor: Annotated[int, Field(ge=0, le=MAX_WORK_ITEM_CURSOR)]
    state: BusinessState
    reason: str
    tracks: list[WorkItemLifecycleTrack]


class WorkItemCommandActivity(CamelModel):
    kind: Literal["COMMAND"]
    sequence: int = Field(ge=1, le=MAX_WORK_ITEM_CURSOR)
    created_at: str
    command: Literal["CREATE", "LINK_SESSION", "LINK_OBJECT", "LINK_ASSET", "ARCHIVE", "UPDATE", "REVIEW"]
    request_id: _Id
    actor_subject: _Id
    revision: _Revision
    changed: bool
    link_id: _Id | None
    review_session_id: _Id | None


class WorkItemSessionActivity(CamelModel):
    kind: Literal["SESSION"]
    sequence: int = Field(ge=1, le=MAX_WORK_ITEM_CURSOR)
    created_at: str
    session_id: _Id
    event_type: str


class WorkItemActivityPage(CamelModel):
    entries: list[Annotated[WorkItemCommandActivity | WorkItemSessionActivity, Field(discriminator="kind")]]
    next_after: Annotated[int, Field(ge=1, le=MAX_WORK_ITEM_CURSOR)] | None
