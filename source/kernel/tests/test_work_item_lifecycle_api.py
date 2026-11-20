import asyncio
from concurrent.futures import ThreadPoolExecutor
import json

from fastapi.testclient import TestClient

from kernel.tests.test_work_items import PATH, PROJECT, _app, _command, _create
from kernel.tests.test_work_item_lifecycle import RESULT, facts, run, task
from platform_kernel.access.research_auth import bootstrap_admin, create_token, create_user, put_membership
from platform_kernel.api import create_app
from platform_kernel.database import append_event, connect_database
from platform_kernel.errors import ResourceNotFoundError


def test_metadata_update_uses_current_revision_and_preserves_legacy_queries(kernel_settings):
    application = _app(kernel_settings)
    with TestClient(create_app(application=application)) as client:
        work = _create(client)
        path = f"{PATH}/{work['workItemId']}"
        snapshot = client.get(path + "/snapshot")
        assert snapshot.status_code == 200, snapshot.text
        assert snapshot.headers["cache-control"] == "no-store"
        assert snapshot.json() == {
            "workItemId": work["workItemId"], "revision": 1, "eventCursor": 1,
            "state": "DRAFT", "reason": "NO_BUSINESS_TARGET", "tracks": [],
        }
        assert _command(client, path + "/update", "empty", 1).status_code == 422
        assert _command(client, path + "/update", "state", 1, state="COMPLETED").status_code == 422
        updated = _command(client, path + "/update", "edit", 1, title=" New title ", description="note")
        assert updated.status_code == 200, updated.text
        assert updated.json() == work | {"revision": 2}
        assert _command(client, path + "/update", "edit", 1, title="New title", description="note").json() == updated.json()
        assert _command(client, path + "/update", "edit", 1, title="different").status_code == 409
        assert _command(client, path + "/update", "old-revision", 1, title="stale").status_code == 409
        assert _command(client, path + "/update", "clear", 2, description=None).json()["revision"] == 3
        detail = client.get(path).json()
        assert detail["workItem"]["title"] == "New title" and detail["workItem"]["description"] is None
        assert "state" not in detail and "eventCursor" not in detail
        legacy = client.get(path + "/audit").json()
        assert [entry["command"] for entry in legacy["entries"]] == ["CREATE"]
        page = client.get(path + "/activity", params={"limit": 2}).json()
        assert [entry["command"] for entry in page["entries"]] == ["CREATE", "UPDATE"]
        assert page["nextAfter"] == 2
        assert client.get(path + "/activity", params={"after": 2}).json()["entries"][0]["command"] == "UPDATE"
        assert client.get(path + "/snapshot").json()["eventCursor"] == 3
        assert _command(client, path + "/archive", "archive", 3).status_code == 200
        assert _command(client, path + "/update", "archived", 4, title="disabled").status_code == 409
        assert client.get(path + "/snapshot").json()["state"] == "ARCHIVED"


def test_review_checks_exact_task_revision_and_acceptance_keeps_its_scope(kernel_settings, monkeypatch):
    application = _app(kernel_settings)
    session_id = application.interaction.create_session().session.session_id
    revision = 1

    def owned_facts(connection, work_item_id, project_id, subject):
        return (facts(session_id=session_id, task=task(revision=revision)),)

    monkeypatch.setattr("platform_kernel.work_items.owner.collect_session_facts", owned_facts, raising=False)
    with TestClient(create_app(application=application)) as client:
        path = f"{PATH}/{_create(client)['workItemId']}"
        _command(client, path + "/sessions", "link", 1, sessionId=session_id).raise_for_status()
        before = client.get(path + "/snapshot").raise_for_status().json()
        initial = before["tracks"][0]["review"]["token"]
        assert before["state"] == "WAITING_REVIEW"
        revision = 2
        stale = _command(client, path + "/review", "review", 2, sessionId=session_id, reviewToken=initial)
        assert stale.status_code == 409 and stale.json()["code"] == "WORK_ITEM_REVIEW_STALE"
        current = client.get(path + "/snapshot").json()["tracks"][0]["review"]["token"]
        assert current != initial
        approval = _command(client, path + "/review", "review", 2, sessionId=session_id, reviewToken=current)
        assert approval.status_code == 200 and approval.json()["revision"] == 3
        complete = client.get(path + "/snapshot").json()
        assert complete["state"] == "COMPLETED"
        assert complete["tracks"][0]["review"] is None and complete["tracks"][0]["acceptedAt"]
        assert _command(client, path + "/review", "review", 2, sessionId=session_id, reviewToken=current).json() == approval.json()
        assert client.get(path + "/snapshot").json()["eventCursor"] == complete["eventCursor"]
        revision = 3
        after = client.get(path + "/snapshot").json()
        assert after["state"] == "WAITING_REVIEW" and after["tracks"][0]["taskRevision"] == 3


def test_activity_records_commands_and_committed_facts_without_payload(
    kernel_settings,
):
    application = _app(kernel_settings)
    owned = application.interaction.create_session().session.session_id
    unrelated = application.interaction.create_session().session.session_id
    with TestClient(create_app(application=application)) as client:
        path = f"{PATH}/{_create(client)['workItemId']}"
        _command(client, path + "/sessions", "link", 1, sessionId=owned).raise_for_status()
        with connect_database(application.database) as connection:
            append_event(connection, unrelated, "workflow.succeeded", {})
            append_event(connection, owned, "agent.activity", {"text": "noise"})
            append_event(connection, owned, "task.revised", {"private": "never_copy_to_activity"})
        response = client.get(path + "/activity")
        assert response.status_code == 200, response.text
        assert "never_copy_to_activity" not in response.text
        activity = response.json()["entries"]
        assert [(entry["sequence"], entry["kind"]) for entry in activity] == [(1, "COMMAND"), (2, "COMMAND"), (3, "SESSION")]
        assert (activity[2]["sessionId"], activity[2]["eventType"]) == (owned, "task.revised")
        assert client.get(path + "/snapshot").json()["eventCursor"] == 3


def test_sse_replays_by_owned_cursor_and_signals_restore(kernel_settings):
    from platform_kernel.access import RequestIdentity
    from platform_kernel.api.routes.work_item_stream import events_response

    application = _app(kernel_settings)
    user = RequestIdentity(application.settings.local_identity_subject, "User", frozenset({"researcher"}))
    with TestClient(create_app(application=application)) as client:
        work = _create(client)
        path = f"{PATH}/{work['workItemId']}"
        _command(client, path + "/update", "second", 1, title="updated").raise_for_status()
        assert client.get(path + "/events", params={"afterEventId": -1}).status_code == 422
        assert client.get(path + "/events", params={"projectId": "other"}).status_code == 404

    class OpenRequest:
        async def is_disconnected(self):
            return False

    async def first(after):
        response = await events_response(application, work["workItemId"], user, OpenRequest(), after)
        iterator = response.body_iterator
        try:
            assert response.headers["cache-control"] == "no-store"
            return await anext(iterator)
        finally:
            await iterator.aclose()

    event = asyncio.run(first(1))
    headers, payload = event.split("data: ", maxsplit=1)
    assert headers == "id: 2\nevent: work_item.activity\n"
    assert json.loads(payload)["command"] == "UPDATE"
    assert asyncio.run(first(20)) == "event: work_item.reset\ndata: {}\n\n"


def test_actual_sse_route_resumes_from_header_then_matches_snapshot(kernel_settings, monkeypatch):
    monkeypatch.setattr("platform_kernel.api.routes.work_item_stream._STREAM_SECONDS", 0.1)
    application = _app(kernel_settings)
    with TestClient(create_app(application=application)) as client:
        path = f"{PATH}/{_create(client)['workItemId']}"
        _command(client, path + "/update", "edited", 1, title="edited").raise_for_status()
        replay = client.get(path + "/events", params={"afterEventId": 2}, headers={"Last-Event-ID": "0"})
        assert replay.status_code == 200 and replay.headers["content-type"].startswith("text/event-stream")
        assert replay.headers["x-accel-buffering"] == "no"
        assert [line for line in replay.text.splitlines() if line.startswith("id:")] == ["id: 1", "id: 2"]
        before = client.get(path + "/snapshot").raise_for_status().json()
        _command(client, path + "/archive", "archive", 2).raise_for_status()
        resumed = client.get(path + "/events", params={"afterEventId": before["eventCursor"]})
        assert [line for line in resumed.text.splitlines() if line.startswith("id:")] == ["id: 3"]
        after = client.get(path + "/snapshot").json()
        assert (after["eventCursor"], after["state"], after["revision"]) == (3, "ARCHIVED", 3)


def test_work_item_lifecycle_endpoints_enforce_project_and_actor(kernel_settings):
    application = _app(kernel_settings, authenticate=True)
    password = "isolated testing password"
    alice = bootstrap_admin(application.database, "p", "alice", "Alice", password)
    bob = create_user(application.database, "bob", "Bob", password)["userId"]
    viewer = create_user(application.database, "viewer", "Viewer", password)["userId"]
    put_membership(application.database, "p", bob, "admin")
    put_membership(application.database, "p", viewer, "viewer")

    def headers(subject):
        _, token = create_token(application.database, subject, "test", 1)
        return {"Authorization": f"Bearer {token}"}

    own, other, read_only = [headers(user) for user in (alice, bob, viewer)]
    with TestClient(create_app(application=application)) as client:
        path = f"{PATH}/{_create(client, headers=own)['workItemId']}"
        for endpoint in ("snapshot", "activity", "events"):
            url = path + "/" + endpoint
            assert client.get(url, headers=other | {"X-Platform-Subject": alice}).status_code == 404
            assert client.get(url, headers=read_only).status_code == 403
            assert client.get(url, headers=own, params={"projectId": "wrong"}).status_code == 404
        payload = {"sessionId": "foreign", "reviewToken": "sha256:" + "0" * 64}
        assert _command(client, path + "/review", "try", 1, headers=other, **payload).status_code == 404
        assert _command(client, path + "/review", "try", 1, headers=own, **payload).status_code == 404
        assert client.post(path + "/update", headers=own, json=PROJECT | {
            "projectId": "wrong", "requestId": "wrong", "expectedRevision": 1, "title": "update",
        }).status_code == 404


def test_racing_updates_commit_one_revision_and_activity(kernel_settings):
    with TestClient(create_app(application=_app(kernel_settings))) as client:
        path = f"{PATH}/{_create(client)['workItemId']}"

        def update(number):
            return _command(client, path + "/update", f"concurrent-{number}", 1, title=f"Title {number}")

        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(update, (1, 2)))
        assert sorted(response.status_code for response in outcomes) == [200, 409]
        current = client.get(path + "/snapshot").json()
        assert (current["revision"], current["eventCursor"]) == (2, 2)


def test_publication_and_current_access_are_rechecked_before_acceptance(kernel_settings, monkeypatch):
    application = _app(kernel_settings)
    session_id = application.interaction.create_session().session.session_id
    revision = 1
    publication_state = "PENDING"
    readable = True
    revise_during_check = False

    def read_facts(connection, work_item_id, project_id, subject):
        return (facts(session_id=session_id, task=task(kind="RESULT", revision=revision),
                      workflows=(run("SUCCEEDED"),)),)

    async def publication(run_id, actor):
        assert run_id == "run"
        return publication_state

    async def is_visible(kind, reference, actor):
        assert kind == "RESULT" and reference["outputName"] == "output"
        return readable

    async def require_result(reference, actor):
        nonlocal revision
        if not readable:
            raise ResourceNotFoundError("WORK_ITEM_REFERENCE_NOT_FOUND")
        if revise_during_check:
            revision += 1

    monkeypatch.setattr("platform_kernel.work_items.owner.collect_session_facts", read_facts)
    monkeypatch.setattr(application.work_items.references, "publication_state", publication)
    monkeypatch.setattr(application.work_items.references, "is_visible", is_visible)
    monkeypatch.setattr(application.work_items.references, "require_result", require_result)
    with TestClient(create_app(application=application)) as client:
        work = _create(client)
        path = f"{PATH}/{work['workItemId']}"
        _command(client, path + "/sessions", "link", 1, sessionId=session_id).raise_for_status()
        pending = client.get(path + "/snapshot").json()
        assert (pending["state"], pending["reason"]) == ("BLOCKED", "PUBLICATION_PENDING")
        publication_state = "PUBLISHED"
        published = client.get(path + "/snapshot").json()
        assert published["eventCursor"] == pending["eventCursor"]
        assert published["state"] == "RESULT_REVIEW" and published["tracks"][0]["review"] is None
        with connect_database(application.database) as connection:
            connection.execute(
                """INSERT INTO work_item_references(link_id, work_item_id, kind, reference_key, reference_json, linked_at)
                   VALUES (?,?,'RESULT',?,?,?)""",
                (RESULT.link_id, work["workItemId"], RESULT.link_id, json.dumps(RESULT.reference), "2026-01-01T00:00:00Z"),
            )

        token = client.get(path + "/snapshot").json()["tracks"][0]["review"]["token"]
        readable = False
        denied = _command(client, path + "/review", "accept", 2, sessionId=session_id, reviewToken=token)
        assert denied.status_code == 404
        assert client.get(path + "/snapshot").json()["tracks"][0]["review"] is None
        readable, revise_during_check = True, True
        changed = _command(client, path + "/review", "accept", 2, sessionId=session_id, reviewToken=token)
        assert changed.status_code == 409 and changed.json()["code"] == "WORK_ITEM_REVIEW_STALE"
        assert client.get(path + "/snapshot").json()["eventCursor"] == pending["eventCursor"]
        revise_during_check = False
        current = client.get(path + "/snapshot").json()["tracks"][0]["review"]["token"]
        _command(client, path + "/review", "accept", 2, sessionId=session_id, reviewToken=current).raise_for_status()
        readable = False
        assert client.get(path + "/snapshot").json()["state"] == "COMPLETED"
