"""WorkItem organization over the real monitoring-trend lifecycle."""

from __future__ import annotations

import asyncio
from contextlib import ExitStack
from dataclasses import replace
from hashlib import sha256
import json
import os
from time import perf_counter

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
from platform_kernel.access.research_auth import create_user, put_membership
from platform_kernel.agent_runtime.sdk_execution.work_item_context import PREFIX
from platform_kernel.api import create_app as kernel_app
from platform_kernel.composition import PlatformApplication
from scripts.development.workflow_profile import describe_workflow_result
from services.data.tests.conftest import postgres as postgres, settings as data_settings  # noqa: F401
from tests.e2e.test_composed_workflow import (
    _MONITORING_TREND_REQUEST,
    ROOT,
    _assembled_kernel_settings,
    _bridge,
    _monitoring_preparation_result,
    _monitoring_trend_steps,
    _publish_monitoring_observations,
    _run_native_workflow_worker,
    _wait_for_session,
    assembly as assembly,
    native_site as native_site,
)


PROJECT = {"projectId": "p"}
TASK_GOAL = "请登记监测趋势任务并交付阈值事件摘要。"
RESULT_NAME = "threshold-event-summary"
RESEARCHER = {"username": "monitoring-researcher", "password": "monitoring-local-acceptance"}


def _login_researcher(api, subject):
    login = api.post("/api/v12/auth/login", json=RESEARCHER).raise_for_status()
    api.headers["X-CSRF-Token"] = login.headers["X-CSRF-Token"]
    identity = login.json()["currentUser"]
    assert identity["subject"] == subject and identity["roles"] == ["researcher"]
    assert api.get("/api/v12/auth/me").raise_for_status().json() == identity
    capabilities = api.get("/api/v12/capabilities").raise_for_status().json()
    assert capabilities["projectId"] == PROJECT["projectId"]
    assert capabilities["authenticationRequired"] and capabilities["currentUser"] == identity
    ingress = api.get("/internal/identity/verify", headers={"X-Project-ID": capabilities["projectId"]})
    assert ingress.status_code == 204, ingress.text
    assert ingress.headers["X-Platform-Subject"] == subject
    return {name: ingress.headers[name] for name in (
        "X-Project-ID", "X-Platform-Subject", "X-Platform-Display-Name", "X-Platform-Roles",
    )}


def _new_session(api):
    response = api.post("/api/v12/sessions", json=PROJECT).raise_for_status().json()
    return response["sessionId"]


def _dialogue(api, session_id, request_id, message, *, requested_mode="DIALOGUE", input_references=()):
    path = f"/api/v12/sessions/{session_id}"
    api.post(path + "/messages", json={
        "requestId": request_id, "requestedMode": requested_mode, "message": message,
        **({"inputReferences": list(input_references)} if input_references else {}),
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


def _exact_content(data, ref, *, headers):
    content = data.get(
        f"/data/v3/versions/{ref['versionId']}/objects/{ref['objectId']}/content",
        headers=headers,
    ).raise_for_status().content
    assert (len(content), "sha256:" + sha256(content).hexdigest()) == (ref["sizeBytes"], ref["checksum"])
    return content


def _inspect_observations(data, input_ref, headers):
    discovery = data.post("/data/v3/discovery", json={"fieldTerms": ["value"], "units": ["m"]}, headers=headers)
    candidate, = discovery.raise_for_status().json()["items"]
    assert (candidate["datasetId"], candidate["currentVersion"], candidate["semanticId"]) == (
        input_ref["datasetId"], input_ref["versionId"], input_ref["semanticId"],
    )
    assert candidate["timeRange"] == "2020-01-01/2025-12-31"
    assert candidate["crs"] is None and "crs" in candidate["unknowns"]
    assert candidate["quality"] is None
    path = f"/data/v3/versions/{input_ref['versionId']}"
    version = data.get(path, headers=headers).raise_for_status().json()
    assert version["project"] == PROJECT["projectId"]
    assert version["semanticId"] == input_ref["semanticId"]
    value = next(field for field in version["descriptor"]["fields"] if field["name"] == "value")
    semantics = version["semantics"]
    assert semantics["fields"][value["fieldId"]]["meaning"] == "groundwater monitoring level"
    assert semantics["fields"][value["fieldId"]]["unit"] == "m"
    assert semantics["timezone"] == "UTC" and semantics["timeRange"] == candidate["timeRange"]
    original, = [obj for obj in version["objects"] if obj["role"] == "raw"]
    assert (original["objectId"], original["checksum"], original["size"]) == (
        input_ref["objectId"], input_ref["checksum"], input_ref["sizeBytes"],
    )


def _task_steps(arguments, input_ref, workflow_result):
    def clarify(call):
        prepared = _monitoring_preparation_result(call, "prepare-missing-threshold")
        assert prepared["status"] == "BLOCKED", prepared
        return [submit_reply("需要用户给定的阈值才能识别事件。", unresolved=[("本次阈值是多少米？", ())])]

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
        ModelStep(output=[function_call("prepare_task_inputs", {
            "target_tool": "run_monitoring_trend", "arguments": {}, "selection_policy": "PINNED",
        }, call_id="prepare-missing-threshold")]),
        ModelStep.respond(clarify),
        ModelStep(output=[function_call("update_task", {"expected_revision": 1}, call_id="associate-execution")]),
        *_monitoring_trend_steps(arguments, input_ref, selected_by_capability=True),
        ModelStep.respond(link_result),
        ModelStep(output=[submit_reply("已将已发布的摘要登记为任务证据。", call_id="recorded")]),
        ModelStep(output=[submit_reply("含水层能够储存并传导地下水。", call_id="consulted")]),
        ModelStep(output=[submit_reply("已收到用于后续分析的阈值摘要。", call_id="reused-delivery")]),
        ModelStep(output=[submit_reply("含水层的补给可来自降水入渗。", call_id="consulted-separately")]),
    ]


def test_monitoring_work_item_completes_authenticated_journey(
    assembly, kernel_settings, request, tmp_path, monkeypatch, record_property,
):
    settings = replace(_assembled_kernel_settings(assembly, kernel_settings()), research_auth_required=True)
    data_config = replace(request.getfixturevalue("data_settings"), project=PROJECT["projectId"], identity_mode="gateway")
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
    workflow_platform = json.loads((assembly / "workflow-platform.json").read_text())
    profile = next(
        item for item in workflow_platform["profiles"]
        if item["ref"]["id"] == "groundwater.monitoring-trend.seasonal-and-threshold"
    )
    workflow_result = {}
    durations = {}
    publication_started = {}
    publication_committed = {}
    with ExitStack() as stack:
        data = stack.enter_context(TestClient(data_app(data_config), headers=_data_identity("fixture-owner", "admin")))
        well = _publish_well_observations(data, data_worker, typed_time=True)
        input_ref = well["source"]
        arguments = {**cases["cases"]["monitoring-trend"]["parameters"], "versionId": input_ref["versionId"]}

        def executor_to_data(request):
            started = perf_counter()
            response = _bridge(data, request)
            path = request.url.path
            if request.method == "POST" and response.is_success:
                if path == "/data/v3/publications":
                    publication_started.setdefault(response.json()["publicationId"], started)
                elif path.startswith("/data/v3/publications/") and path.endswith("/commit"):
                    publication_committed.setdefault(path.split("/")[-2], perf_counter())
            return response

        transport = stack.enter_context(httpx.Client(transport=httpx.MockTransport(executor_to_data)))
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
                for decision in (
                    "DESIGN|CREATE|NONE", "EXECUTE_ON_DATA|NONE|NONE", "EXECUTE_ON_DATA|NONE|NONE",
                    "REVIEW_RESULT|CONTINUE|NONE",
                )
            ])
            return PlatformApplication(
                settings=settings, model=ScriptedModel(steps), intent_router_model=router,
                client=httpx.AsyncClient(transport=httpx.MockTransport(route)),
            )

        app = application(_task_steps(arguments, input_ref, workflow_result))
        model = app.agent_runtime.sdk_execution.model
        subject = create_user(
            app.database, RESEARCHER["username"], "Monitoring researcher", RESEARCHER["password"],
        )["userId"]
        put_membership(app.database, PROJECT["projectId"], subject, "researcher")
        with TestClient(kernel_app(application=app)) as api:
            assert api.get("/api/v12/work-items", params=PROJECT).status_code == 401
            journey_started = perf_counter()
            data_auth = _login_researcher(api, subject)
            legacy_session = _new_session(api)
            assert _detail(api, f"/api/v12/sessions/{legacy_session}/work-item")["context"] is None
            started = perf_counter()
            work = _create_work_item(api, "create-monitoring-work-item", "监测趋势工作项")
            assert _detail(api, work)["sessions"] == []
            session_link = _command(api, work, "sessions/create", "create-monitoring-session", 1)
            analysis_session = session_link["linkId"]
            session_path = f"/api/v12/sessions/{analysis_session}"
            work_context = _detail(api, session_path + "/work-item")["context"]
            assert work_context["workItem"]["workItemId"] == session_link["workItemId"]
            durations["create_work_item_to_linked_session_s"] = perf_counter() - started
            legacy_link = _command(api, work, "sessions", "link-legacy-session", 2, sessionId=legacy_session)

            created_task = _dialogue(api, analysis_session, "register-task", TASK_GOAL, requested_mode="AUTO")
            task_before = api.get(session_path + "/task").raise_for_status().json()["task"]
            task_id = task_before["spec"]["taskId"]
            assert task_before["spec"]["revision"] == 1
            _inspect_observations(data, input_ref, data_auth)
            well_link = _command(api, work, "objects", "link-observed-well", 3, reference=well)
            data_link = _command(api, work, "assets", "link-observations", 4, reference={"kind": "DATA", "dataRef": input_ref})

            first_analysis_call = len(model.calls)
            started = perf_counter()
            api.post(session_path + "/messages", json={
                "requestId": "analyze-monitoring-trend", "requestedMode": "AUTO", "inputReferences": [input_ref],
                "message": _MONITORING_TREND_REQUEST + "阈值比较=高于，识别超过该阈值的事件。",
            }).raise_for_status()
            blocked = _wait_for_session(api, session_path, "COMPLETE")
            durations["analysis_to_clarification_s"] = perf_counter() - started
            assert blocked["confirmation"] is None and blocked["workflowRunIds"] == []
            blocked_preparation = blocked["preparation"]
            assert blocked_preparation["status"] == "BLOCKED"
            blocked_entry, = blocked_preparation["entries"]
            issue, = blocked_entry["blockingIssues"]
            assert (issue["code"], issue["kind"], issue["parameter"]) == (
                "CAPABILITY_PARAMETER_REQUIRED", "MISSING_PARAMETER", "eventThreshold",
            )
            assert "eventThreshold" not in blocked_entry["parameters"]
            assert blocked["agentOutput"]["unresolvedItems"]
            blocked_work = _snapshot(api, work)
            assert blocked_work["state"] == _track(blocked_work, analysis_session)["state"] == "WAITING_INPUT"

            started = perf_counter()
            supplemental = api.post(session_path + "/messages", json={
                "requestId": "supply-monitoring-threshold", "requestedMode": "AUTO", "message": "高于 10 m",
            }).raise_for_status().json()
            pending = _wait_for_session(api, session_path, "WAITING_CONFIRMATION")
            durations["supplement_to_frozen_confirmation_s"] = perf_counter() - started
            assert pending["goalRevision"] == blocked["goalRevision"] + 1
            assert pending["workflowRunIds"] == []
            preparation = _detail(api, session_path + "/preparation")
            assert preparation["status"] == "READY" and preparation["freshForCurrentTurn"]
            ready, = preparation["entries"]
            assert ready["parameterSources"]["eventThreshold"] == "USER"
            assert ready["parameters"]["eventThreshold"] == 10
            assert {"FIELD_METADATA", "FIELD_UNIT", "TIME_FIELD_PARSEABILITY", "TIME_RANGE"} <= {
                check["kind"] for check in ready["checks"] if check["status"] == "PASS"
            }
            trace = _detail(api, f"/api/v12/agent-runs/{supplemental['agentRunId']}/trace")["trace"]
            assert trace["businessCapability"]["profileRef"] == profile["ref"]
            assert trace["businessCapability"]["resultContractRef"] == profile["resultContractRef"]
            executable_tools = {definition.tool_name for definition in settings.models}
            assert all(
                {tool.name for tool in call.tools} & executable_tools == {"run_monitoring_trend"}
                for call in model.calls[first_analysis_call:]
            )
            analysis_blocks = _model_context(model.calls[-1])
            sources = {block["source"] for block in analysis_blocks}
            assert {"CAPABILITY_SELECTION", "WORK_ITEM_REFERENCE", "CURRENT_SESSION"} <= sources
            assert pending["confirmation"]["status"] == "PENDING"
            action, = pending["confirmation"]["actions"]
            assert action["inputReferences"] == {"versionId": input_ref}
            assert action["arguments"] == arguments
            assert action["modelChecksum"] == profile["modelRef"]["checksum"]
            assert action["workflowChecksum"] == profile["workflowRef"]["checksum"]
            assert _detail(api, work)["workflowRuns"] == []
            waiting = _snapshot(api, work)
            waiting_track = _track(waiting, analysis_session)
            assert waiting["state"] == waiting_track["state"] == "WAITING_CONFIRMATION"
            assert waiting["revision"] == 5
            assert waiting_track["taskId"] == task_id and waiting_track["taskRevision"] == 1
            assert waiting_track["confirmationId"] == pending["confirmation"]["confirmationId"]
            assert waiting_track["publications"] == [] and waiting_track["review"] is None
            with executor_database(executor.database) as connection:
                assert connection.execute("SELECT count(*) FROM executor_jobs").fetchone()[0] == 0

            confirmed_at = perf_counter()
            approved = api.post(session_path + "/confirm", json={
                "confirmationId": pending["confirmation"]["confirmationId"],
            }).raise_for_status().json()
            run_id, = approved["workflowRunIds"]
            run_path = f"/api/v12/workflow-runs/{run_id}"
            executing = _snapshot(api, work)
            assert executing["state"] == _track(executing, analysis_session)["state"] == "EXECUTING"
            assert executing["eventCursor"] > waiting["eventCursor"]
            assert _detail(api, run_path + "/publication")["publicationId"] is None
            workflow_result.update(asyncio.run(
                _run_native_workflow_worker(executor_http, data_worker, api, run_path, capacity),
            ))
            run_observed = perf_counter()
            assert workflow_result["status"] == "SUCCEEDED"
            durations["confirmation_to_succeeded_run_s"] = run_observed - confirmed_at
            publication = _detail(api, run_path + "/publication")
            publication_observed = perf_counter()
            durations["succeeded_run_to_published_read_s"] = publication_observed - run_observed
            assert publication["state"] == "PUBLISHED"
            publication_id = publication["publicationId"]
            durations["data_publication_begin_to_commit_s"] = (
                publication_committed[publication_id] - publication_started[publication_id]
            )
            _wait_for_session(api, session_path, "COMPLETE")
            published = describe_workflow_result(assembly, profile["ref"], workflow_result, publication)
            assert published["resultStatus"] == "COMPLETE" and published["businessAcceptance"] == "NOT_ASSESSED"
            outputs = {item["name"]: item for item in published["outputs"]}
            assert set(outputs) == set(golden["outputs"])
            result = outputs[RESULT_NAME]["dataRef"]
            summary = json.loads(_exact_content(data, result, headers=data_auth))
            assert summary == golden["keyValues"][RESULT_NAME] and summary["limitations"]
            for output_name in ("completeness-summary", "gap-run-summary", "outlier-summary"):
                diagnostic = json.loads(_exact_content(data, outputs[output_name]["dataRef"], headers=data_auth))
                assert diagnostic == golden["keyValues"][output_name]
            result_version = data.get(
                f"/data/v3/versions/{result['versionId']}", headers=data_auth,
            ).raise_for_status().json()
            assert result_version["publicationId"] == publication_id
            assert result_version["inputVersions"] == [input_ref["versionId"]]
            data_publication = executor.control.data_client.api.get_publication(publication_id).model_dump(by_alias=True)
            assert data_publication["manifest"]["inputVersions"] == [input_ref["versionId"]]
            provenance = data_publication["manifest"]["provenance"]
            assert provenance["workflow_checksum"] == action["workflowChecksum"]
            assert provenance["model_checksum"] == action["modelChecksum"]
            evidence = api.get(session_path + "/messages", params={"goalRevision": pending["goalRevision"]}).raise_for_status().json()
            turn, = [item for item in evidence["turns"] if item["agentRunId"] == workflow_result["agentRunId"]]
            dispatch, = [item for item in turn["executionTasks"] if item["workflowRunId"] == run_id]
            frozen, = [item for item in turn["confirmations"] if item["confirmationId"] == dispatch["confirmationId"]]
            assert frozen["status"] == "CONFIRMED"
            assert frozen["actions"] == [action]

            workflow_link = _command(api, work, "assets", "link-run", 5, reference={"kind": "WORKFLOW", "runId": run_id})
            result_link = _command(api, work, "assets", "link-threshold-summary", 6, reference={
                "kind": "RESULT", "runId": run_id, "outputName": RESULT_NAME,
            })
            _dialogue(
                api, analysis_session, "register-task-evidence", "请继续当前任务，将本次已发布的结果登记为任务证据。",
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
            review_track = _track(reviewing, analysis_session)
            assert reviewing["state"] == review_track["state"] == "RESULT_REVIEW"
            assert reviewing["revision"] == result_link["revision"]
            assert review_track["taskId"] == task_id
            assert review_track["taskRevision"] == task_after["spec"]["revision"] == 2
            assert any(item["runId"] == run_id and item["state"] == "PUBLISHED" for item in review_track["publications"])
            assert review_track["acceptedAt"] is None
            review = review_track["review"]
            assert review["resultLinkIds"] == [result_link["linkId"]]
            assert review["token"]

            _dialogue(api, legacy_session, "linked-ordinary-consultation", "什么是含水层？")
            consult_call = model.calls[-1]
            consult_context = {block["source"]: block["value"] for block in _model_context(consult_call)}
            assert consult_context["WORK_ITEM"]["state"] == "RESULT_REVIEW"
            assert consult_context["CURRENT_SESSION"]["sessionId"] == legacy_session
            assert "task" not in consult_context["CURRENT_SESSION"]
            assert review["token"] not in json.dumps(consult_call.input)
            assert {tool.name for tool in consult_call.tools}.isdisjoint({"update_task", "record_plan"})
            assert api.get(f"/api/v12/sessions/{legacy_session}/task").raise_for_status().json()["task"] is None
            after_consultation = _snapshot(api, work)
            assert after_consultation["state"] == "RESULT_REVIEW"
            assert _track(after_consultation, analysis_session) == review_track
            assert after_consultation["revision"] == reviewing["revision"]
            assert after_consultation["eventCursor"] > reviewing["eventCursor"]

            review_arguments = {"sessionId": analysis_session, "reviewToken": review["token"]}
            for revision, token in ((6, review["token"]), (7, "sha256:" + "0" * 64)):
                rejected = api.post(work + "/review", json={
                    **PROJECT, "requestId": f"reject-stale-delivery-{revision}", "expectedRevision": revision,
                    "sessionId": analysis_session, "reviewToken": token,
                })
                assert rejected.status_code == 409, rejected.text
            assert _snapshot(api, work) == after_consultation
            acceptance = _command(api, work, "review", "accept-monitoring-result", 7, **review_arguments)
            completed = _snapshot(api, work)
            accepted_at = perf_counter()
            durations["published_read_to_review_accepted_s"] = accepted_at - publication_observed
            completed_track = _track(completed, analysis_session)
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
            assert acceptance_entry["reviewSessionId"] == analysis_session
            assert acceptance_entry["revision"] == acceptance["revision"]
            assert activity["nextAfter"] is None
            assert _command(api, work, "review", "accept-monitoring-result", 7, **review_arguments) == acceptance
            assert _activity_after(api, work, completed["eventCursor"])["entries"] == []

            followup = _command(api, work, "sessions/create", "create-delivery-follow-up", completed["revision"])
            followup_session = followup["linkId"]
            destination = f"/api/v12/sessions/{followup_session}"
            destination_context = _detail(api, destination + "/work-item")["context"]
            assert destination_context["workItem"]["workItemId"] == result_link["workItemId"]
            exact_delivery, = [asset["reference"] for asset in destination_context["assets"] if asset["linkId"] == result_link["linkId"]]
            assert exact_delivery["runId"] == run_id
            assert exact_delivery["publicationId"] == publication["publicationId"]
            assert exact_delivery["outputName"] == RESULT_NAME and exact_delivery["dataRef"] == result
            reused_message = "请读取我附加的已发布阈值摘要，为后续分析核对可用资料。"
            _dialogue(
                api, followup_session, "reuse-accepted-delivery", reused_message,
                input_references=[exact_delivery["dataRef"]],
            )
            destination_history = _detail(api, destination + "/messages")
            reused, = [item for item in destination_history["items"] if item["content"] == reused_message]
            assert reused["inputReferences"] == [result]
            durations["accepted_to_exact_reuse_s"] = perf_counter() - accepted_at
            assert api.get(destination + "/task").raise_for_status().json()["task"] is None
            after_reuse = _snapshot(api, work)
            assert after_reuse["state"] == "COMPLETED"
            assert _track(after_reuse, analysis_session) == completed_track
            assert after_reuse["eventCursor"] > completed["eventCursor"]

            consult = _new_session(api)
            _dialogue(api, consult, "ordinary-consultation", "含水层如何获得补给？")
            assert _model_context(model.calls[-1]) == []
            assert api.get(f"/api/v12/sessions/{consult}/task").raise_for_status().json()["task"] is None
            assert _snapshot(api, work) == after_reuse
            items = api.get("/api/v12/work-items", params=PROJECT).raise_for_status().json()["items"]
            assert [item["workItemId"] for item in items] == [result_link["workItemId"]]

            assert _command(api, work, "sessions", "link-legacy-session", 2, sessionId=legacy_session) == legacy_link
            api.patch(session_path, json={"archived": True}).raise_for_status()
            active = _detail(api, work)
            assert active["workItem"]["status"] == "ACTIVE"
            assert active["workItem"]["revision"] == followup["revision"]
            active_snapshot = _snapshot(api, work)
            assert active_snapshot["state"] == "COMPLETED"
            assert active_snapshot["eventCursor"] == after_reuse["eventCursor"] + 1
            sessions = {item["sessionId"]: item for item in active["sessions"]}
            assert set(sessions) == {analysis_session, followup_session, legacy_session}
            assert sessions[analysis_session]["archivedAt"] is not None and sessions[followup_session]["archivedAt"] is None
            assert active["tasks"] == [{
                "taskId": task_id, "sessionId": analysis_session,
                "revision": task_after["spec"]["revision"], "status": "COMPLETE",
            }]
            assert any(item["taskId"] == task_id and item["agentRunId"] == workflow_result["agentRunId"] for item in active["taskRuns"])
            assert {item["agentRunId"] for item in active["agentRuns"]} >= {created_task["agentRunId"], workflow_result["agentRunId"]}
            assert active["workflowRuns"] == [{
                "runId": run_id, "agentRunId": workflow_result["agentRunId"], "sessionId": analysis_session,
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
            assert api.post("/api/v12/auth/logout").status_code == 204
            assert api.get(work, params=PROJECT).status_code == 401

        input_path = f"/data/v3/versions/{input_ref['versionId']}"
        advanced = data.post(input_path + "/semantics", json={
            "expectedSemanticId": input_ref["semanticId"], "semantics": {"description": "后续的资料说明修订。"},
        }).raise_for_status().json()
        assert advanced["semanticId"] != input_ref["semanticId"]
        assert data.get(input_path + f"/semantics/{input_ref['semanticId']}").status_code == 200
        with TestClient(kernel_app(application=application([]))) as reopened:
            assert reopened.get(work, params=PROJECT).status_code == 401
            started = perf_counter()
            reopened_data_auth = _login_researcher(reopened, subject)
            assert reopened_data_auth == data_auth
            assert _detail(reopened, work) == active
            assert _snapshot(reopened, work) == active_snapshot
            durations["relogin_to_recovered_snapshot_s"] = perf_counter() - started
            recovered_activity = _activity_after(reopened, work, after_consultation["eventCursor"])["entries"]
            recovered_reviews = [
                entry for entry in recovered_activity
                if entry["kind"] == "COMMAND" and entry["command"] == "REVIEW"
            ]
            assert recovered_reviews == [acceptance_entry]
            assert _command(reopened, work, "review", "accept-monitoring-result", 7, **review_arguments) == acceptance
            _command(reopened, work, "archive", "archive-monitoring-work-item", active["workItem"]["revision"])
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
            recovered_history = _detail(reopened, destination + "/messages")
            assert {key: value for key, value in recovered_history.items() if key != "snapshotAt"} == {
                key: value for key, value in destination_history.items() if key != "snapshotAt"
            }
            assert reopened.get(run_path, params=PROJECT).raise_for_status().json() == workflow_result
            audit = reopened.get(work + "/audit", params=PROJECT).raise_for_status().json()["entries"]
            assert sum(entry["requestId"] == "link-legacy-session" for entry in audit) == 1
            _exact_content(data, input_ref, headers=reopened_data_auth)
            assert json.loads(_exact_content(data, result, headers=reopened_data_auth)) == summary
            durations["login_to_archived_evidence_s"] = perf_counter() - journey_started
    record_property("monitoring_journey", json.dumps({
        "preparations": [blocked_preparation["status"], preparation["status"]],
        "execution": workflow_result["status"], "publication": publication["state"],
        "acceptance": completed["state"], "archive": archived_snapshot["state"],
        "published_outputs": len(outputs), "exact_output_reuses": 1,
        "local_elapsed_seconds": {name: round(seconds, 3) for name, seconds in durations.items()},
    }, sort_keys=True))


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
