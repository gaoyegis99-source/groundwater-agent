from typing import Annotated

from fastapi import APIRouter, Depends, Header, Path, Query, Request, Response
from fastapi.responses import StreamingResponse

from ...access import RequestIdentity, require_role
from ...composition import PlatformApplication
from ...work_items.owner import Change
from ..dependencies import current_identity, platform_application, require_project
from ..schemas import PROBLEM_RESPONSE
from ..wire import to_wire
from ..work_item_schemas import (
    MAX_WORK_ITEM_CURSOR,
    WorkItemActivityPage,
    WorkItemAssetAttach,
    WorkItemAuditPage,
    WorkItemChange,
    WorkItemCreate,
    WorkItemDetail,
    WorkItemLifecycleSnapshot,
    WorkItemMetadataUpdate,
    WorkItemObjectAttach,
    WorkItemPage,
    WorkItemReceipt,
    WorkItemReview,
    WorkItemSessionAttach,
)
from .work_item_stream import WORK_ITEM_EVENT_RESPONSES, events_response


router = APIRouter(
    prefix="/api/v12/work-items",
    responses={status: PROBLEM_RESPONSE for status in (401, 403, 404, 409, 422, 503)},
)
Application = Annotated[PlatformApplication, Depends(platform_application)]
ItemId = Annotated[str, Path(alias="workItemId", min_length=1, max_length=256)]
Limit = Annotated[int, Query(ge=1, le=50)]


def _researcher(
    identity: Annotated[RequestIdentity, Depends(current_identity)],
) -> RequestIdentity:
    require_role(identity, "researcher")
    return identity


Actor = Annotated[RequestIdentity, Depends(_researcher)]
ProjectQuery = Annotated[str | None, Query(alias="projectId")]


@router.get("", response_model=WorkItemPage, operation_id="kernel_list_work_items")
def list_work_items(
    application: Application,
    actor: Actor,
    project_id: ProjectQuery = None,
    include_archived: Annotated[bool, Query(alias="includeArchived")] = False,
    after: Annotated[str, Query(max_length=256)] = "",
    limit: Limit = 20,
):
    if project_id is not None:
        require_project(application, project_id)
    return to_wire(application.work_items.list_items(actor, include_archived, after, limit))


@router.post("", response_model=WorkItemReceipt, operation_id="kernel_create_work_item")
def create_work_item(request: WorkItemCreate, application: Application, actor: Actor):
    require_project(application, request.project_id)
    command = Change("CREATE", request.request_id, {
        "title": request.title, "description": request.description,
    })
    return to_wire(application.work_items.apply(command, actor))


@router.get("/{workItemId}", response_model=WorkItemDetail, operation_id="kernel_get_work_item")
async def get_work_item(
    work_item_id: ItemId, application: Application, actor: Actor,
    project_id: ProjectQuery = None,
):
    if project_id is not None:
        require_project(application, project_id)
    return to_wire(await application.work_items.detail(work_item_id, actor))


@router.get(
    "/{workItemId}/audit", response_model=WorkItemAuditPage,
    operation_id="kernel_list_work_item_audit",
)
def list_work_item_audit(
    work_item_id: ItemId, application: Application, actor: Actor,
    project_id: ProjectQuery = None,
    after: Annotated[int, Query(ge=0, le=2**63 - 1)] = 0,
    limit: Limit = 20,
):
    if project_id is not None:
        require_project(application, project_id)
    return to_wire(application.work_items.audit(work_item_id, actor, after, limit))


@router.post(
    "/{workItemId}/sessions", response_model=WorkItemReceipt,
    operation_id="kernel_link_work_item_session",
)
def link_work_item_session(
    work_item_id: ItemId, request: WorkItemSessionAttach, application: Application, actor: Actor,
):
    require_project(application, request.project_id)
    command = Change("LINK_SESSION", request.request_id, {"sessionId": request.session_id},
                     work_item_id, request.expected_revision)
    return to_wire(application.work_items.apply(command, actor))


@router.post(
    "/{workItemId}/objects", response_model=WorkItemReceipt,
    operation_id="kernel_link_work_item_object",
)
async def link_work_item_object(
    work_item_id: ItemId, request: WorkItemObjectAttach, application: Application, actor: Actor,
):
    require_project(application, request.project_id)
    command = Change("LINK_OBJECT", request.request_id, request.reference.model_dump(by_alias=True),
                     work_item_id, request.expected_revision)
    return to_wire(await application.work_items.attach_reference(command, actor))


@router.post(
    "/{workItemId}/assets", response_model=WorkItemReceipt,
    operation_id="kernel_link_work_item_asset",
)
async def link_work_item_asset(
    work_item_id: ItemId, request: WorkItemAssetAttach, application: Application, actor: Actor,
):
    require_project(application, request.project_id)
    command = Change("LINK_ASSET", request.request_id, request.reference.model_dump(by_alias=True),
                     work_item_id, request.expected_revision)
    return to_wire(await application.work_items.attach_reference(command, actor))


@router.post(
    "/{workItemId}/archive", response_model=WorkItemReceipt,
    operation_id="kernel_archive_work_item",
)
def archive_work_item(
    work_item_id: ItemId, request: WorkItemChange, application: Application, actor: Actor,
):
    require_project(application, request.project_id)
    command = Change("ARCHIVE", request.request_id, {}, work_item_id, request.expected_revision)
    return to_wire(application.work_items.apply(command, actor))


@router.get(
    "/{workItemId}/snapshot", response_model=WorkItemLifecycleSnapshot,
    operation_id="kernel_get_work_item_snapshot",
)
async def get_work_item_snapshot(
    work_item_id: ItemId, application: Application, actor: Actor, response: Response,
    project_id: ProjectQuery = None,
):
    if project_id is not None:
        require_project(application, project_id)
    response.headers["Cache-Control"] = "no-store"
    return to_wire(await application.work_items.lifecycle(work_item_id, actor))


@router.post(
    "/{workItemId}/update", response_model=WorkItemReceipt,
    operation_id="kernel_update_work_item",
)
def update_work_item(
    work_item_id: ItemId, request: WorkItemMetadataUpdate, application: Application, actor: Actor,
):
    require_project(application, request.project_id)
    payload = request.model_dump(include={"title", "description"}, exclude_unset=True)
    if payload.get("description") == "":
        payload["description"] = None
    command = Change("UPDATE", request.request_id, payload, work_item_id, request.expected_revision)
    return to_wire(application.work_items.apply(command, actor))


@router.post(
    "/{workItemId}/review", response_model=WorkItemReceipt,
    operation_id="kernel_review_work_item",
)
async def review_work_item(
    work_item_id: ItemId, request: WorkItemReview, application: Application, actor: Actor,
):
    require_project(application, request.project_id)
    command = Change("REVIEW", request.request_id, {
        "sessionId": request.session_id, "reviewToken": request.review_token,
    }, work_item_id, request.expected_revision)
    return to_wire(await application.work_items.review(command, actor))


@router.get(
    "/{workItemId}/activity", response_model=WorkItemActivityPage,
    operation_id="kernel_list_work_item_activity",
)
def list_work_item_activity(
    work_item_id: ItemId, application: Application, actor: Actor, response: Response,
    project_id: ProjectQuery = None,
    after: Annotated[int, Query(ge=0, le=MAX_WORK_ITEM_CURSOR)] = 0,
    limit: Limit = 20,
):
    if project_id is not None:
        require_project(application, project_id)
    response.headers["Cache-Control"] = "no-store"
    _, entries = application.work_items.activity_batch(work_item_id, actor, after, limit + 1)
    return to_wire({
        "entries": entries[:limit],
        "next_after": entries[limit - 1]["sequence"] if len(entries) > limit else None,
    })


@router.get(
    "/{workItemId}/events", response_class=StreamingResponse,
    responses=WORK_ITEM_EVENT_RESPONSES, operation_id="kernel_stream_work_item_events",
)
async def stream_work_item_events(
    work_item_id: ItemId, application: Application, actor: Actor, request: Request,
    project_id: ProjectQuery = None,
    last_event_id: Annotated[int | None, Header(alias="Last-Event-ID", ge=0, le=MAX_WORK_ITEM_CURSOR)] = None,
    after_event_id: Annotated[int, Query(alias="afterEventId", ge=0, le=MAX_WORK_ITEM_CURSOR)] = 0,
):
    if project_id is not None:
        require_project(application, project_id)
    cursor = after_event_id if last_event_id is None else last_event_id
    return await events_response(application, work_item_id, actor, request, cursor)
