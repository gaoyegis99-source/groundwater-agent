"""WorkItem organization over the real monitoring-trend lifecycle."""

from __future__ import annotations

import asyncio
from contextlib import ExitStack
from dataclasses import replace
from hashlib import sha256
import json
import os

from agents.testing import ModelStep, ScriptedModel, assistant_message, function_call
from fastapi.testclient import TestClient
import httpx

from kernel.tests.conftest import kernel_settings as kernel_settings
from kernel.tests.reply_testing import submit_reply
from platform_data_service.api.app import create_app as data_app
from platform_data_service.operations.worker import Worker as DataWorker
from platform_executor_service.app import create_app as executor_app
from platform_executor_service.composition import ExecutorApplication
from platform_executor_service.database import connect_database as executor_database
from platform_kernel.agent_runtime.sdk_execution.work_item_context import PREFIX
from platform_kernel.api import create_app as kernel_app
from platform_kernel.composition import PlatformApplication
from services.data.tests.conftest import postgres as postgres, settings as data_settings  # noqa: F401
from tests.e2e.test_composed_workflow import (
    _MONITORING_TREND_REQUEST,
    ROOT,
    _assembled_kernel_settings,
    _bridge,
    _monitoring_trend_steps,
    _publish_monitoring_observations,
    _run_monitoring_trend_worker,
    _wait_for_session,
    assembly as assembly,
    native_site as native_site,
)


PROJECT = {"projectId": "p"}
TASK_GOAL = "请登记监测趋势任务并交付阈值事件摘要。"
RESULT_NAME = "threshold-event-summary"


def _new_session(api):
    response = api.post("/api/v12/sessions", json=PROJECT).raise_for_status().json()
    return response["sessionId"]


def _dialogue(api, session_id, request_id, message, *, requested_mode="DIALOGUE"):
    path = f"/api/v12/sessions/{session_id}"
    api.post(path + "/messages", json={
        "requestId": request_id, "requestedMode": requested_mode, "message": message,
    }).raise_for_status()
    return _wait_for_session(api, path, "COMPLETE")


def _create_work_item(api, request_id, title):
    response = api.post("/api/v12/work-items", json={**PROJECT, "requestId": request_id, "title": title})
    assert response.status_code == 200, response.text
    receipt = response.json()
    assert receipt["revision"] == 1 and receipt["changed"]
    return f"/api/v12/work-items/{receipt['workItemId']}"


def _command(api, path, command, request_id, revision, **payload):
    response = api.post(f"{path}/{command}", json={
        **PROJECT, "requestId": request_id, "expectedRevision": revision, **payload,
    })
    assert response.status_code == 200, response.text
    receipt = response.json()
    assert receipt["revision"] == revision + 1 and receipt["changed"]
    return receipt


def _detail(api, path):
    return api.get(path, params=PROJECT).raise_for_status().json()


def _snapshot(api, path):
    return _detail(api, path + "/snapshot")


def _activity_after(api, path, cursor):
    return api.get(path + "/activity", params={**PROJECT, "after": cursor}).raise_for_status().json()


def _track(snapshot, session_id):
    (track,) = [track for track in snapshot["tracks"] if track["sessionId"] == session_id]
    return track


def _model_context(call):
    return [
        block
        for item in call.input
        if isinstance(item.get("content"), str) and item["content"].startswith(PREFIX)
        for block in json.loads(item["content"][len(PREFIX):])["blocks"]
    ]


def _publish_well_observations(data, worker, *, typed_time=False):
    input_ref = _publish_monitoring_observations(data, worker, typed_time=typed_time)
    version_path = f"/data/v3/versions/{input_ref['versionId']}"
    version = data.get(version_path).raise_for_status().json()
    station_field_id = next(field["fieldId"] for field in version["descriptor"]["fields"] if field["name"] == "station")
    semantics = version["semantics"]
    revision = data.post(version_path + "/semantics", json={
        "expectedSemanticId": input_ref["semanticId"],
        "semantics": {**semantics, "fields": {**semantics["fields"], station_field_id: {"meaning": "监测井编号"}}},
    }).raise_for_status().json()
    return {
        "objectKind": "WELL", "source": {**input_ref, "semanticId": revision["semanticId"]},
        "fieldId": station_field_id, "externalId": "A",
    }


def _exact_content(data, ref):
    content = data.get(
        f"/data/v3/versions/{ref['versionId']}/objects/{ref['objectId']}/content",
    ).raise_for_status().content
    assert (len(content), "sha256:" + sha256(content).hexdigest()) == (ref["sizeBytes"], ref["checksum"])
    return content


def _task_steps(arguments, input_ref, workflow_result):
    def link_result(_call):
        return [function_call("update_task", {
            "expected_revision": 1, "evidence": [{
                "criterion_id": "threshold-summary", "agent_run_id": workflow_result["agentRunId"],
                "workflow_run_id": workflow_result["runId"],
            }],
        }, call_id="link-task-result")]

    return [
        ModelStep(output=[function_call("update_task", {
            "expected_revision": 0, "objective_quote": TASK_GOAL, "criteria": [{
                "id": "threshold-summary", "text": "阈值事件摘要", "source_quote": "阈值事件摘要",
                "kind": "RESULT", "model_id": "groundwater.monitoring-trend", "output_names": [RESULT_NAME],
            }],
        }, call_id="create-task")]),
        ModelStep(output=[submit_reply("已登记监测趋势交付任务。", call_id="registered")]),
        ModelStep(output=[function_call("update_task", {"expected_revision": 1}, call_id="associate-execution")]),
        *_monitoring_trend_steps(arguments, input_ref, selected_by_capability=True),
        ModelStep.respond(link_result),
        ModelStep(output=[submit_reply("已将已发布的摘要登记为任务证据。", call_id="recorded")]),
        ModelStep(output=[submit_reply("含水层能够储存并传导地下水。", call_id="consulted")]),
        ModelStep(output=[submit_reply("含水层的补给可来自降水入渗。", call_id="consulted-separately")]),
    ]


def test_monitoring_work_item_preserves_linked_sessions_and_published_data(
    assembly, kernel_settings, request, tmp_path, monkeypatch,
):
    settings = _assembled_kernel_settings(assembly, kernel_settings())
    data_config = replace(request.getfixturevalue("data_settings"), project="p")
    data_worker = DataWorker(data_config)
    executor_config = assembly / "executor-config.json"
    native_packages = request.getfixturevalue("native_site")
    for name, value in {
        "EXECUTOR_CONFIG_PATH": str(executor_config), "EXECUTOR_CONTROL_URL": "http://executor",
        "PYTHONDONTWRITEBYTECODE": "1", "OMP_NUM_THREADS": "1", "MKL_NUM_THREADS": "1", "OPENBLAS_NUM_THREADS": "1",
        "PYTHONPATH": os.pathsep.join((
            str(ROOT / "packages/operator-pack/src"), str(ROOT / "services/executor/src"),
            str(ROOT / "packages/protocol-contracts/src"), str(native_packages),
        )),
    }.items():
        monkeypatch.setenv(name, value)

    development = ROOT / "products/groundwater/development"
    cases = json.loads((development / "workflow-golden-cases.v2.json").read_text())
    golden = json.loads((development / "workflow-goldens/monitoring-trend.json").read_text())
    workflow_result = {}
    with ExitStack() as stack:
        data = stack.enter_context(TestClient(data_app(data_config)))
        well = _publish_well_observations(data, data_worker, typed_time=True)
        input_ref = well["source"]
        arguments = {**cases["cases"]["monitoring-trend"]["parameters"], "versionId": input_ref["versionId"]}
        transport = stack.enter_context(httpx.Client(transport=httpx.MockTransport(lambda req: _bridge(data, req))))
        executor = ExecutorApplication(
            executor_config, tmp_path / "executor", tmp_path / "objects", tmp_path / "work", "http://data", "p",
            client=transport,
        )
        executor_http = stack.enter_context(TestClient(executor_app(executor)))
        capacity = executor.control.config["workerRuntimes"]["native"]

        def route(request):
            if request.url.host == "executor-control":
                return _bridge(executor_http, request)
            assert request.url.host == "data-service", f"Unexpected request {request.url}"
            return _bridge(data, request)

        def application(steps):
            router = ScriptedModel([
                ModelStep(output=[assistant_message(decision)])
                for decision in ("DESIGN|CREATE|NONE", "EXECUTE_ON_DATA|NONE|NONE", "REVIEW_RESULT|CONTINUE|NONE")
            ])
            return PlatformApplication(
                settings=settings, model=ScriptedModel(steps), intent_router_model=router,
                client=httpx.AsyncClient(transport=httpx.MockTransport(route)),
            )

        app = application(_task_steps(arguments, input_ref, workflow_result))
        model = app.agent_runtime.sdk_execution.model
        with TestClient(kernel_app(application=app)) as api:
            old_session = _new_session(api)
            session_path = f"/api/v12/sessions/{old_session}"
            created_task = _dialogue(api, old_session, "register-task", TASK_GOAL, requested_mode="AUTO")
            task_before = api.get(session_path + "/task").raise_for_status().json()["task"]
            task_id = task_before["spec"]["taskId"]
            assert task_before["spec"]["revision"] == 1

            work = _create_work_item(api, "create-monitoring-work-item", "监测趋势工作项")
            assert _detail(api, work)["sessions"] == []
            legacy_link = _command(api, work, "sessions", "link-legacy-session", 1, sessionId=old_session)
            new_session = _new_session(api)
            _command(api, work, "sessions", "link-new-session", 2, sessionId=new_session)
            well_link = _command(api, work, "objects", "link-observed-well", 3, reference=well)
            data_link = _command(api, work, "assets", "link-observations", 4, reference={"kind": "DATA", "dataRef": input_ref})

            api.post(session_path + "/messages", json={
                "requestId": "analyze-monitoring-trend", "requestedMode": "AUTO", "inputReferences": [input_ref],
                "message": _MONITORING_TREND_REQUEST + "识别高于 10 m 的阈值事件。",
            }).raise_for_status()
            pending = _wait_for_session(api, session_path, "WAITING_CONFIRMATION")
            analysis_blocks = _model_context(model.calls[-1])
            sources = {block["source"] for block in analysis_blocks}
            assert {"CAPABILITY_SELECTION", "WORK_ITEM_REFERENCE", "CURRENT_SESSION"} <= sources
            assert pending["confirmation"]["status"] == "PENDING"
            assert pending["confirmation"]["actions"][0]["inputReferences"] == {"versionId": input_ref}
            assert _detail(api, work)["workflowRuns"] == []
            waiting = _snapshot(api, work)
            waiting_track = _track(waiting, old_session)
            assert waiting["state"] == waiting_track["state"] == "WAITING_CONFIRMATION"
            assert waiting["revision"] == 5
            assert waiting_track["taskId"] == task_id and waiting_track["taskRevision"] == 1
            assert waiting_track["confirmationId"] == pending["confirmation"]["confirmationId"]
            assert waiting_track["publications"] == [] and waiting_track["review"] is None
            with executor_database(executor.database) as connection:
                assert connection.execute("SELECT count(*) FROM executor_jobs").fetchone()[0] == 0

            approved = api.post(session_path + "/confirm", json={
                "confirmationId": pending["confirmation"]["confirmationId"],
            }).raise_for_status().json()
            run_id, = approved["workflowRunIds"]
            run_path = f"/api/v12/workflow-runs/{run_id}"
            executing = _snapshot(api, work)
            assert executing["state"] == _track(executing, old_session)["state"] == "EXECUTING"
            assert executing["eventCursor"] > waiting["eventCursor"]
            workflow_result.update(asyncio.run(_run_monitoring_trend_worker(executor_http, data_worker, api, run_path, capacity)))
            _wait_for_session(api, session_path, "COMPLETE")
            publication = api.get(run_path + "/publication", params=PROJECT).raise_for_status().json()
            assert publication["state"] == "PUBLISHED"
            assert len(workflow_result["outputs"]) == 21
            result = next(item for item in workflow_result["outputs"] if item["outputName"] == RESULT_NAME)
            assert json.loads(_exact_content(data, result)) == golden["keyValues"][RESULT_NAME]

            workflow_link = _command(api, work, "assets", "link-run", 5, reference={"kind": "WORKFLOW", "runId": run_id})
            result_link = _command(api, work, "assets", "link-threshold-summary", 6, reference={
                "kind": "RESULT", "runId": run_id, "outputName": RESULT_NAME,
            })
            _dialogue(
                api, old_session, "register-task-evidence", "请继续当前任务，将本次已发布的结果登记为任务证据。",
                requested_mode="AUTO",
            )
            review_blocks = _model_context(model.calls[-1])
            result_context, = [
                block["value"] for block in review_blocks if block["sourceId"] == result_link["linkId"]
            ]
            assert result_context["dataRef"] == result
            assert result_context["publicationId"] == publication["publicationId"]
            task_after = api.get(session_path + "/task").raise_for_status().json()["task"]
            assert task_after["spec"]["taskId"] == task_id
            assert task_after["progress"]["lifecycle"] == "COMPLETE"

            reviewing = _snapshot(api, work)
            review_track = _track(reviewing, old_session)
            assert reviewing["state"] == review_track["state"] == "RESULT_REVIEW"
            assert reviewing["revision"] == result_link["revision"]
            assert review_track["taskId"] == task_id
            assert review_track["taskRevision"] == task_after["spec"]["revision"] == 2
            assert any(item["runId"] == run_id and item["state"] == "PUBLISHED" for item in review_track["publications"])
            assert review_track["acceptedAt"] is None
            review = review_track["review"]
            assert review["resultLinkIds"] == [result_link["linkId"]]
            assert review["token"]

            _dialogue(api, new_session, "linked-ordinary-consultation", "什么是含水层？")
            consult_call = model.calls[-1]
            consult_context = {block["source"]: block["value"] for block in _model_context(consult_call)}
            assert consult_context["WORK_ITEM"]["state"] == "RESULT_REVIEW"
            assert consult_context["CURRENT_SESSION"]["sessionId"] == new_session
            assert "task" not in consult_context["CURRENT_SESSION"]
            assert review["token"] not in json.dumps(consult_call.input)
            assert {tool.name for tool in consult_call.tools}.isdisjoint({"update_task", "record_plan"})
            assert api.get(f"/api/v12/sessions/{new_session}/task").raise_for_status().json()["task"] is None
            after_consultation = _snapshot(api, work)
            assert after_consultation["state"] == "RESULT_REVIEW"
            assert _track(after_consultation, old_session) == review_track
            assert after_consultation["revision"] == reviewing["revision"]
            assert after_consultation["eventCursor"] > reviewing["eventCursor"]

            review_arguments = {"sessionId": old_session, "reviewToken": review["token"]}
            acceptance = _command(api, work, "review", "accept-monitoring-result", 7, **review_arguments)
            completed = _snapshot(api, work)
            completed_track = _track(completed, old_session)
            assert completed["state"] == completed_track["state"] == "COMPLETED"
            assert completed_track["acceptedAt"] is not None
            assert completed["revision"] == acceptance["revision"] == 8
            assert completed["eventCursor"] == after_consultation["eventCursor"] + 1
            activity = _activity_after(api, work, after_consultation["eventCursor"])
            (acceptance_entry,) = activity["entries"]
            assert acceptance_entry["sequence"] == completed["eventCursor"]
            assert acceptance_entry["kind"] == "COMMAND"
            assert acceptance_entry["command"] == "REVIEW"
            assert acceptance_entry["requestId"] == "accept-monitoring-result"
            assert acceptance_entry["reviewSessionId"] == old_session
            assert acceptance_entry["revision"] == acceptance["revision"]
            assert activity["nextAfter"] is None
            assert _command(api, work, "review", "accept-monitoring-result", 7, **review_arguments) == acceptance
            assert _activity_after(api, work, completed["eventCursor"])["entries"] == []

            consult = _new_session(api)
            _dialogue(api, consult, "ordinary-consultation", "含水层如何获得补给？")
            assert _model_context(model.calls[-1]) == []
            assert api.get(f"/api/v12/sessions/{consult}/task").raise_for_status().json()["task"] is None
            assert _snapshot(api, work) == completed
            items = api.get("/api/v12/work-items", params=PROJECT).raise_for_status().json()["items"]
            assert [item["workItemId"] for item in items] == [result_link["workItemId"]]

            assert _command(api, work, "sessions", "link-legacy-session", 1, sessionId=old_session) == legacy_link
            api.patch(session_path, json={"archived": True}).raise_for_status()
            active = _detail(api, work)
            assert active["workItem"]["status"] == "ACTIVE"
            assert active["workItem"]["revision"] == 8
            active_snapshot = _snapshot(api, work)
            assert active_snapshot["state"] == "COMPLETED"
            assert active_snapshot["eventCursor"] == completed["eventCursor"] + 1
            sessions = {item["sessionId"]: item for item in active["sessions"]}
            assert set(sessions) == {old_session, new_session}
            assert sessions[old_session]["archivedAt"] is not None and sessions[new_session]["archivedAt"] is None
            assert active["tasks"] == [{
                "taskId": task_id, "sessionId": old_session, "revision": task_after["spec"]["revision"], "status": "COMPLETE",
            }]
            assert any(item["taskId"] == task_id and item["agentRunId"] == workflow_result["agentRunId"] for item in active["taskRuns"])
            assert {item["agentRunId"] for item in active["agentRuns"]} >= {created_task["agentRunId"], workflow_result["agentRunId"]}
            assert active["workflowRuns"] == [{
                "runId": run_id, "agentRunId": workflow_result["agentRunId"], "sessionId": old_session,
                "workflowRef": {"id": workflow_result["workflowId"], "version": workflow_result["workflowVersion"],
                                "checksum": workflow_result["workflowChecksum"]},
            }]
            assert active["objects"][0]["linkId"] == well_link["linkId"]
            assert active["objects"][0]["state"] == "AVAILABLE" and active["objects"][0]["reference"] == well
            assets = {item["linkId"]: item for item in active["assets"]}
            assert {link: item["state"] for link, item in assets.items()} == dict.fromkeys(
                (data_link["linkId"], workflow_link["linkId"], result_link["linkId"]), "AVAILABLE",
            )
            assert assets[data_link["linkId"]]["reference"]["dataRef"] == input_ref
            assert assets[workflow_link["linkId"]]["reference"]["runId"] == run_id
            result_ref = assets[result_link["linkId"]]["reference"]
            assert result_ref["publicationId"] == publication["publicationId"]
            assert result_ref["dataRef"] == result

        input_path = f"/data/v3/versions/{input_ref['versionId']}"
        advanced = data.post(input_path + "/semantics", json={
            "expectedSemanticId": input_ref["semanticId"], "semantics": {"description": "后续的资料说明修订。"},
        }).raise_for_status().json()
        assert advanced["semanticId"] != input_ref["semanticId"]
        assert data.get(input_path + f"/semantics/{input_ref['semanticId']}").status_code == 200
        with TestClient(kernel_app(application=application([]))) as reopened:
            assert _detail(reopened, work) == active
            assert _snapshot(reopened, work) == active_snapshot
            assert _activity_after(reopened, work, after_consultation["eventCursor"])["entries"][0] == acceptance_entry
            assert _command(reopened, work, "review", "accept-monitoring-result", 7, **review_arguments) == acceptance
            _command(reopened, work, "archive", "archive-monitoring-work-item", 8)
            archived = _detail(reopened, work)
            assert archived["workItem"]["status"] == "ARCHIVED"
            archived_snapshot = _snapshot(reopened, work)
            assert archived_snapshot["state"] == "ARCHIVED"
            assert archived_snapshot["eventCursor"] == active_snapshot["eventCursor"] + 1
            (archive_entry,) = _activity_after(reopened, work, active_snapshot["eventCursor"])["entries"]
            assert archive_entry["sequence"] == archived_snapshot["eventCursor"]
            assert archive_entry["command"] == "ARCHIVE"
            assert all(archived[key] == value for key, value in active.items() if key != "workItem")
            assert reopened.get(session_path + "/task").raise_for_status().json()["task"] == task_after
            assert reopened.get(f"/api/v12/sessions/{new_session}").raise_for_status().json()["sessionId"] == new_session
            assert reopened.get(run_path, params=PROJECT).raise_for_status().json() == workflow_result
            audit = reopened.get(work + "/audit", params=PROJECT).raise_for_status().json()["entries"]
            assert sum(entry["requestId"] == "link-legacy-session" for entry in audit) == 1
            assert len(audit) == 8
        _exact_content(data, input_ref)
        assert json.loads(_exact_content(data, result)) == golden["keyValues"][RESULT_NAME]


def _data_identity(subject, role):
    return {
        "X-Project-ID": PROJECT["projectId"], "X-Platform-Subject": subject,
        "X-Platform-Display-Name": subject, "X-Platform-Roles": role,
    }


def test_work_item_rechecks_data_access_for_objects_and_assets(assembly, kernel_settings, request):
    data_config = replace(request.getfixturevalue("data_settings"), project="p", identity_mode="gateway")
    settings = replace(
        _assembled_kernel_settings(assembly, kernel_settings()),
        local_identity_subject="work-item-owner", local_identity_display_name="WorkItem owner", local_identity_roles=("researcher",),
    )
    with TestClient(data_app(data_config), headers=_data_identity("source-owner", "admin")) as data:
        well = _publish_well_observations(data, DataWorker(data_config))
        input_ref = well["source"]

        def to_data(request):
            assert request.url.host == "data-service", f"Unexpected request {request.url}"
            return _bridge(data, request)

        kernel = PlatformApplication(
            settings=settings, model=ScriptedModel([]),
            client=httpx.AsyncClient(transport=httpx.MockTransport(to_data)),
        )
        with TestClient(kernel_app(application=kernel)) as api:
            work = _create_work_item(api, "create-source-access-work-item", "监测数据访问")
            _command(api, work, "objects", "link-project-well", 1, reference=well)
            _command(api, work, "assets", "link-project-observations", 2, reference={"kind": "DATA", "dataRef": input_ref})
            available = _detail(api, work)
            assert available["objects"][0]["reference"] == well
            assert available["assets"][0]["reference"]["dataRef"] == input_ref

            access_path = f"/data/v3/datasets/{input_ref['datasetId']}/access"
            data.put(access_path, json={"visibility": "PRIVATE"}).raise_for_status()
            version_path = f"/data/v3/versions/{input_ref['versionId']}"
            researcher = _data_identity(settings.local_identity_subject, "researcher")
            assert data.get(version_path, headers=researcher).status_code == 404
            assert data.get(version_path + f"/objects/{input_ref['objectId']}/content", headers=researcher).status_code == 404
            unavailable = _detail(api, work)
            assert unavailable["workItem"] == available["workItem"]
            for section in ("objects", "assets"):
                assert unavailable[section] == [{**available[section][0], "reference": None, "state": "UNAVAILABLE"}]

            data.put(access_path, json={"visibility": "PROJECT"}).raise_for_status()
            assert _detail(api, work) == available
