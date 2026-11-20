from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

import httpx
from platform_protocol_contracts import decode_display_name_header
import pytest

from platform_kernel import errors
from platform_kernel.access import RequestIdentity
from platform_kernel.application.query_dtos import ConfirmedAction
from platform_kernel.contracts import DataVersionRefV2, JobStatusV6
from platform_kernel.database import connect_database, utc_now
from platform_kernel.interaction.persistence import create_session
from platform_kernel.work_items.references import ReferenceAuthority
from platform_kernel.workflow_execution.execution_client import ExecutionClient
from platform_kernel.workflow_execution.models import DispatchWork
from platform_kernel.workflow_execution.owner import WorkflowExecutionOwner
from platform_kernel.workflow_execution.persistence import save_job_status


_ALICE = RequestIdentity("alice", "王 Alice", frozenset({"admin", "researcher"}))
_BOB = RequestIdentity("bob", "Bob", frozenset({"researcher"}))
_CHECKSUM = "sha256:" + "a" * 64


def _data_ref(**changes):
    return {
        "kind": "data_version", "service": "data-primary", "datasetId": "dataset-1",
        "versionId": "version-1", "semanticId": "semantic-1", "objectId": "object-1",
        "mediaType": "text/csv", "format": "csv", "sizeBytes": 22, "checksum": _CHECKSUM,
        **changes,
    }


def _data_version(**changes):
    return {
        "datasetId": "dataset-1", "versionId": "version-1", "currentVersion": "version-2",
        "semanticId": "semantic-1", "publicationId": "publication-1", "project": "p",
        "title": "Monitoring points", "inputVersions": [], "tags": [],
        "createdAt": "2026-01-01T00:00:00Z",
        "descriptor": {"fields": [{"fieldId": "f1"}, {"fieldId": "f2"}]},
        "semantics": {"fields": {"f1": {"meaning": "well_id"}}},
        "objects": [{
            "objectId": "object-1", "role": "raw", "size": 22, "format": "csv",
            "checksum": _CHECKSUM, "mediaType": "text/csv",
        }],
        **changes,
    }


def _assert_actor(request, actor):
    assert request.method == "GET"
    assert request.headers["X-Project-ID"] == "p"
    assert request.headers["X-Platform-Subject"] == actor.subject
    assert request.headers["X-Platform-Roles"] == ",".join(sorted(actor.roles))
    assert decode_display_name_header(request.headers["X-Platform-Display-Name"]) == actor.display_name


@pytest.fixture
def authority(kernel_settings):
    @asynccontextmanager
    async def create(respond):
        settings = kernel_settings()
        database = settings.data_root / "kernel.sqlite3"
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            workflows = WorkflowExecutionOwner(
                database, settings.models, settings.services, ExecutionClient(client),
            )
            yield ReferenceAuthority(client, settings, workflows, database), workflows

    return create


def test_object_checks_field_against_its_pinned_historical_semantics(authority):
    declared = {
        "objectKind": "WELL", "source": _data_ref(), "fieldId": "f1", "externalId": "W-0031",
    }
    historical = {
        "versionId": "version-1", "semanticId": "semantic-1", "actor": {},
        "createdAt": "2026-01-01T00:00:00Z", "body": {"fields": {"f1": {"meaning": "well_id"}}},
    }

    def respond(request):
        _assert_actor(request, _ALICE)
        assert request.url.host == "data-service"
        if request.url.path == "/data/v3/versions/version-1":
            return httpx.Response(200, json=_data_version(
                semanticId="semantic-2", semantics={"fields": {"f2": {"meaning": "station"}}},
            ))
        assert request.url.path == "/data/v3/versions/version-1/semantics/semantic-1"
        return httpx.Response(200, json=historical)

    async def check():
        async with authority(respond) as (references, _):
            assert await references.resolve_object(declared, _ALICE) == declared
            assert not await references.is_visible("OBJECT", {**declared, "fieldId": "f2"}, _ALICE)
            historical["versionId"] = "other-version"
            with pytest.raises(errors.WorkItemConflictError, match="WORK_ITEM_REFERENCE_MISMATCH"):
                await references.resolve_object(declared, _ALICE)

    asyncio.run(check())


@pytest.mark.parametrize(
    ("field", "value"),
    [("project", "other"), ("datasetId", "other"), ("versionId", "other"),
     ("objectId", "other"), ("format", "tsv"), ("mediaType", "text/plain"),
     ("size", 23), ("checksum", "sha256:" + "b" * 64)],
)
def test_data_must_match_the_full_pinned_identity(authority, field, value):
    body = _data_version()
    target = body if field in {"project", "datasetId", "versionId"} else body["objects"][0]
    target[field] = value

    def respond(request):
        _assert_actor(request, _BOB)
        return httpx.Response(200, json=body)

    async def check():
        async with authority(respond) as (references, _):
            reference = {"kind": "DATA", "dataRef": _data_ref()}
            with pytest.raises(errors.WorkItemConflictError, match="WORK_ITEM_REFERENCE_MISMATCH"):
                await references.resolve_asset(reference, _BOB, frozenset())
            assert not await references.is_visible("DATA", reference, _BOB)

    asyncio.run(check())


def test_data_paths_treat_pinned_identifiers_as_single_segments(authority):
    version = "one/data?different#fragment"
    semantic = "historical/revision?override"
    reference = {"kind": "DATA", "dataRef": _data_ref(versionId=version, semanticId=semantic)}

    def respond(request):
        _assert_actor(request, _BOB)
        version_path = b"/data/v3/versions/one%2Fdata%3Fdifferent%23fragment"
        if request.url.raw_path == version_path:
            return httpx.Response(200, json=_data_version(versionId=version))
        assert request.url.raw_path == version_path + b"/semantics/historical%2Frevision%3Foverride"
        return httpx.Response(200, json={
            "versionId": version, "semanticId": semantic, "body": {"fields": {}},
            "createdAt": "2026-01-01T00:00:00Z", "actor": {},
        })

    async def check():
        async with authority(respond) as (references, _):
            assert await references.resolve_asset(reference, _BOB, frozenset()) == reference
            with pytest.raises(errors.WorkItemConflictError):
                await references.resolve_asset(
                    {"kind": "DATA", "dataRef": _data_ref(versionId="..")}, _BOB, frozenset(),
                )

    asyncio.run(check())


def test_only_result_links_accept_named_output_identity(authority):
    def respond(_request):
        pytest.fail("A claimed output name reached Data from a generic reference")

    async def check():
        async with authority(respond) as (references, _):
            named = _data_ref(outputName="invented")
            with pytest.raises(errors.WorkItemConflictError):
                await references.resolve_asset({"kind": "DATA", "dataRef": named}, _BOB, frozenset())
            with pytest.raises(errors.WorkItemConflictError):
                await references.resolve_object({
                    "objectKind": "WELL", "source": named, "fieldId": "f1", "externalId": "W-1",
                }, _BOB)

    asyncio.run(check())


def test_knowledge_rechecks_current_visibility_and_exact_version(authority):
    reference = {
        "kind": "KNOWLEDGE", "documentId": "methods", "versionId": "kv_1", "checksum": _CHECKSUM,
    }
    access_status = 200

    def respond(request):
        _assert_actor(request, _BOB)
        assert str(request.url) == "http://knowledge-next-service:8000/knowledge/v3/versions/kv_1"
        return httpx.Response(access_status, json={
            "documentId": "methods", "versionId": "kv_1", "checksum": _CHECKSUM,
            "body": {}, "importId": "import_1", "verifiedAt": None,
            "createdAt": "2026-01-01T00:00:00Z", "lifecycleRevision": 1,
            "status": "ACTIVE", "validThrough": None, "reviewAfter": "2027-01-01",
            "lifecycleActor": {},
        })

    async def check():
        nonlocal access_status
        async with authority(respond) as (references, _):
            assert await references.resolve_asset(reference, _BOB, frozenset()) == reference
            assert not await references.is_visible("KNOWLEDGE", {**reference, "documentId": "other"}, _BOB)
            access_status = 404
            assert not await references.is_visible("KNOWLEDGE", reference, _BOB)
            with pytest.raises(errors.ResourceNotFoundError, match="WORK_ITEM_REFERENCE_NOT_FOUND"):
                await references.resolve_asset(reference, _BOB, frozenset())
            access_status = 503
            assert not await references.is_visible("KNOWLEDGE", reference, _BOB)
            with pytest.raises(errors.UpstreamServiceError, match="WORK_ITEM_REFERENCE_UNAVAILABLE"):
                await references.resolve_asset(reference, _BOB, frozenset())

    asyncio.run(check())


def _seed_workflow(workflows):
    database = workflows.database
    create_session(database, "session", "p", _ALICE.subject)
    model = workflows.models["groundwater.summary"]
    action = ConfirmedAction(
        tool_call_id="call", position=0, tool=model.tool_name, model_id=model.model_id,
        model_version=model.model_version, model_checksum=model.model_checksum,
        workflow_id=model.workflow_id, workflow_version=model.workflow_version,
        workflow_checksum=model.workflow_checksum, input_schema_checksum=model.input_schema_checksum,
        arguments={"resource_id": "version-1"},
    )
    with connect_database(database) as connection:
        now = utc_now()
        connection.execute(
            """INSERT INTO agent_runs(
                   agent_run_id,session_id,project_id,goal_revision,provider,model,
                   status,steps_json,input_tokens,output_tokens,total_tokens,trace_id,
                   created_at,started_at
               ) VALUES ('agent','session','p',1,'fixed','model','WAITING_EXECUTION','[]',0,0,0,'trace',?,?)""",
            (now, now),
        )
        (run,) = workflows.enqueue_confirmed_batch(
            connection, agent_run_id="agent", session_id="session", project_id="p",
            confirmation_id=None, actions=(action,),
        )
    save_job_status(database, DispatchWork(run, "SUBMITTED", "future"), JobStatusV6(
        job_id="job-1", request_id=run.run_id, status="SUCCEEDED", stage="COMPLETE",
        outputs=(DataVersionRefV2.from_dict(_data_ref(outputName="summary")),),
    ))
    return run.run_id


def test_workflow_requires_membership_and_run_ownership(authority):
    def respond(_request):
        pytest.fail("Resolving the frozen identity contacted an external service")

    async def check():
        async with authority(respond) as (references, workflows):
            run_id = _seed_workflow(workflows)
            reference = {"kind": "WORKFLOW", "runId": run_id}
            with pytest.raises(errors.ResourceNotFoundError, match="WORKFLOW_RUN_NOT_FOUND"):
                await references.resolve_asset(reference, _ALICE, frozenset())
            links = frozenset({run_id})
            with pytest.raises(errors.ResourceNotFoundError, match="WORKFLOW_RUN_NOT_FOUND"):
                await references.resolve_asset(reference, _BOB, links)
            resolved = await references.resolve_asset(reference, _ALICE, links)
            assert resolved == {
                **reference,
                "workflowRef": {"id": "groundwater.summary", "version": "1.0.0", "checksum": "sha256:" + "1" * 64},
            }
            assert await references.is_visible("WORKFLOW", resolved, _ALICE)
            assert not await references.is_visible("WORKFLOW", resolved, _BOB)
            assert not await references.is_visible("WORKFLOW", {
                **resolved, "workflowRef": {**resolved["workflowRef"], "checksum": _CHECKSUM},
            }, _ALICE)

    asyncio.run(check())


def test_result_requires_committed_publication_and_rechecks_its_link_on_read(authority):
    state = "READY"
    current_data = _data_version()
    run_id = ""

    def respond(request):
        if request.url.host == "executor-service":
            assert request.url.path == "/internal/execution/v6/jobs/job-1/publication"
            return httpx.Response(200, json={
                "jobId": "job-1", "requestId": run_id, "publicationId": "publication-1", "state": state,
            })
        _assert_actor(request, _ALICE)
        assert request.url.path == "/data/v3/versions/version-1"
        return httpx.Response(200, json=current_data)

    async def check():
        nonlocal run_id, state
        async with authority(respond) as (references, workflows):
            run_id = _seed_workflow(workflows)
            reference = {"kind": "RESULT", "runId": run_id, "outputName": "summary"}
            links = frozenset({run_id})
            with pytest.raises(errors.WorkItemConflictError, match="WORK_ITEM_RESULT_NOT_PUBLISHED"):
                await references.resolve_asset(reference, _ALICE, links)
            state = "PUBLISHED"
            resolved = await references.resolve_asset(reference, _ALICE, links)
            assert resolved == {
                **reference, "publicationId": "publication-1", "dataRef": _data_ref(outputName="summary"),
            }
            assert await references.is_visible("RESULT", resolved, _ALICE)
            assert not await references.is_visible("RESULT", resolved, _BOB)
            assert not await references.is_visible("RESULT", {**resolved, "outputName": "other"}, _ALICE)
            current_data["publicationId"] = "other"
            assert not await references.is_visible("RESULT", resolved, _ALICE)
            with pytest.raises(errors.WorkItemConflictError, match="WORK_ITEM_REFERENCE_MISMATCH"):
                await references.resolve_asset(reference, _ALICE, links)

    asyncio.run(check())
