from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

from agents.testing import ScriptedModel
from fastapi.testclient import TestClient
import httpx

from platform_kernel.access.research_auth import (
    bootstrap_admin,
    create_token,
    create_user,
    put_membership,
)
from platform_kernel.api import create_app
from platform_kernel.composition import PlatformApplication
from platform_kernel.database import connect_database


PATH = "/api/v12/work-items"
PROJECT = {"projectId": "p"}
PASSWORD = "offline test password"


def _app(kernel_settings, *, authenticate=False, upstream=None):
    settings = replace(kernel_settings(), research_auth_required=authenticate)
    return PlatformApplication(
        settings=settings,
        model=ScriptedModel([]),
        client=httpx.AsyncClient(
            transport=httpx.MockTransport(upstream or (lambda _: httpx.Response(503)))
        ),
    )


def _create(client, key="create", *, headers=None):
    response = client.post(
        PATH, json=PROJECT | {"requestId": key, "title": "  监测趋势  "}, headers=headers
    )
    assert response.status_code == 200, response.text
    return response.json()


def _command(client, path, key, revision, *, headers=None, **payload):
    return client.post(
        path,
        headers=headers,
        json=PROJECT | {"requestId": key, "expectedRevision": revision} | payload,
    )


def test_archival_and_explicit_legacy_session_link_are_independent(kernel_settings):
    app = _app(kernel_settings)
    with TestClient(create_app(application=app)) as client:
        first = client.post("/api/v12/sessions", json=PROJECT).json()["sessionId"]
        second = client.post("/api/v12/sessions", json=PROJECT).json()["sessionId"]
        archived = client.patch(f"/api/v12/sessions/{first}", json={"archived": True})
        assert archived.status_code == 200
        assert client.get(PATH).json()["items"] == []

        created = _create(client)
        assert _create(client) == created
        item_id = created["workItemId"]
        detail_path = f"{PATH}/{item_id}"
        attached = _command(client, detail_path + "/sessions", "old", 1, sessionId=first)
        assert attached.status_code == 200, attached.text
        assert attached.json() == {
            "workItemId": item_id, "revision": 2, "changed": True, "linkId": first,
        }
        new_link = _command(client, detail_path + "/sessions", "new", 2, sessionId=second)
        assert new_link.json()["revision"] == 3
        replay = _command(client, detail_path + "/sessions", "old", 1, sessionId=first)
        assert replay.json() == attached.json()
        conflict = _command(client, detail_path + "/sessions", "old", 1, sessionId=second)
        assert conflict.status_code == 409
        duplicate = _command(client, detail_path + "/sessions", "duplicate", 3, sessionId=first)
        assert duplicate.json() == attached.json() | {"revision": 3, "changed": False}

        other_path = f"{PATH}/{_create(client, 'second item')['workItemId']}"
        conflict = _command(client, other_path + "/sessions", "wrong work", 1, sessionId=first)
        assert conflict.status_code == 409
        detail = client.get(detail_path).json()
        assert detail["workItem"]["title"] == "监测趋势"
        assert detail["workItem"]["status"] == "ACTIVE"
        session_status = {session["sessionId"]: session["archivedAt"] for session in detail["sessions"]}
        assert session_status[first] and session_status[second] is None
        assert detail["tasks"] == detail["taskRuns"] == detail["agentRuns"] == detail["workflowRuns"] == []

        assert _command(client, detail_path + "/archive", "done", 2).status_code == 409
        archived_item = _command(client, detail_path + "/archive", "done", 3).json()
        assert archived_item["revision"] == 4
        assert _command(client, detail_path + "/archive", "done", 3).json() == archived_item
        assert _command(client, detail_path + "/sessions", "late", 4, sessionId=second).status_code == 409
        assert client.get(detail_path).json()["workItem"]["status"] == "ARCHIVED"
        active_sessions = client.get("/api/v12/sessions").json()["items"]
        assert second in {session["sessionId"] for session in active_sessions}
        assert [row["workItemId"] for row in client.get(PATH).json()["items"]] == [other_path.split("/")[-1]]
        page = client.get(PATH, params={"includeArchived": "true", "limit": 1}).json()
        tail = client.get(PATH, params={"includeArchived": "true", "after": page["nextAfter"]}).json()
        assert sorted(row["workItemId"] for row in page["items"] + tail["items"]) == sorted([item_id, other_path.split("/")[-1]])

        page = client.get(detail_path + "/audit", params={"limit": 2}).json()
        tail = client.get(detail_path + "/audit", params={"after": page["nextAfter"]}).json()
        entries = page["entries"] + tail["entries"]
        assert [entry["command"] for entry in entries] == ["CREATE", "LINK_SESSION", "LINK_SESSION", "LINK_SESSION", "ARCHIVE"]
        assert [entry["changed"] for entry in entries] == [True, True, True, False, True]
        assert [entry["revision"] for entry in entries] == [1, 2, 3, 3, 4]
        assert {entry["actorSubject"] for entry in entries} == {app.settings.local_identity_subject}


def test_work_item_scope_uses_validated_role_project_and_owner(kernel_settings):
    app = _app(kernel_settings, authenticate=True)
    alice = bootstrap_admin(app.database, "p", "alice", "Alice", PASSWORD)
    bob = create_user(app.database, "bob", "Bob", PASSWORD)["userId"]
    viewer = create_user(app.database, "viewer", "Viewer", PASSWORD)["userId"]
    put_membership(app.database, "p", bob, "admin")
    put_membership(app.database, "p", viewer, "viewer")

    def credentials(subject):
        _, token = create_token(app.database, subject, "test", 1)
        return {"Authorization": f"Bearer {token}"}

    alice_headers, bob_headers, viewer_headers = [credentials(s) for s in (alice, bob, viewer)]
    bob_session = app.interaction.create_session(bob).session.session_id
    with connect_database(app.database) as conn:
        conn.execute(
            "INSERT INTO sessions(session_id,project_id,goal_revision,owner_subject) VALUES(?,?,0,?)",
            ("foreign-project-session", "other", alice),
        )
    with TestClient(create_app(application=app)) as client:
        assert client.get(PATH).status_code == 401
        assert client.get(PATH, headers=viewer_headers).status_code == 403
        item = _create(client, headers=alice_headers)
        path = f"{PATH}/{item['workItemId']}"
        assert client.get(path + "?projectId=other", headers=alice_headers).status_code == 404
        assert client.get(path, headers=alice_headers | {"X-Project-ID": "other"}).status_code == 404
        assert client.post(PATH, json={"projectId": "other", "requestId": "outside", "title": "other"}, headers=alice_headers).status_code == 404
        forged = bob_headers | {"X-Platform-Subject": alice, "X-Platform-Roles": "admin"}
        assert client.get(path, headers=forged).status_code == 404
        assert client.get(path + "/audit", headers=forged).status_code == 404
        assert _command(client, path + "/archive", "try", 1, headers=forged).status_code == 404
        assert client.get(PATH, headers=bob_headers).json()["items"] == []
        for session_id in (bob_session, "foreign-project-session"):
            response = _command(client, path + "/sessions", "outside", 1, headers=alice_headers, sessionId=session_id)
            assert response.status_code == 404
        assert client.get(path, headers=alice_headers).json()["workItem"]["revision"] == 1
        bob_item = _create(client, headers=bob_headers)
        assert bob_item["workItemId"] != item["workItemId"]
        bob_path = f"{PATH}/{bob_item['workItemId']}"
        assert _command(client, bob_path + "/archive", "done", 1, headers=bob_headers).status_code == 200
        assert _command(client, path + "/archive", "done", True, headers=alice_headers).status_code == 422
        assert _command(client, path + "/archive", "done", 1, headers=alice_headers).status_code == 200
        entries = client.get(path + "/audit", headers=alice_headers).json()["entries"]
        assert [entry["sequence"] for entry in entries] == [1, 2]


def test_concurrent_station_links_have_one_revision_winner(kernel_settings):
    app = _app(kernel_settings)
    with TestClient(create_app(application=app)) as client:
        detail_path = f"{PATH}/{_create(client)['workItemId']}"
        sessions = [client.post("/api/v12/sessions", json=PROJECT).json()["sessionId"] for _ in range(2)]

        def link(index):
            return _command(client, detail_path + "/sessions", f"try-{index}", 1, sessionId=sessions[index])

        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(link, range(2)))
        assert sorted(response.status_code for response in responses) == [200, 409]
        winner = next(response.json() for response in responses if response.status_code == 200)
        detail = client.get(detail_path).json()
        assert detail["workItem"]["revision"] == 2
        assert [session["sessionId"] for session in detail["sessions"]] == [winner["linkId"]]
        assert len(client.get(detail_path + "/audit").json()["entries"]) == 2


def test_revoked_knowledge_reference_is_redacted_even_after_archive(kernel_settings):
    reference = {"kind": "KNOWLEDGE", "documentId": "doc", "versionId": "v", "checksum": "sha256:" + "1" * 64}
    reachable = True
    visible = True
    requests = []

    def upstream(request):
        requests.append(request)
        assert request.url.path == "/knowledge/v3/versions/v"
        assert request.headers["X-Project-ID"] == "p"
        if not reachable:
            return httpx.Response(503)
        if not visible:
            return httpx.Response(404)
        return httpx.Response(200, json={"project": "p", **{k: v for k, v in reference.items() if k != "kind"}})

    with TestClient(create_app(application=_app(kernel_settings, upstream=upstream))) as client:
        path = f"{PATH}/{_create(client)['workItemId']}"
        stale = _command(client, path + "/assets", "stale", 2, reference=reference)
        assert stale.status_code == 409
        assert requests == []
        added = _command(client, path + "/assets", "knowledge", 1, reference=reference)
        assert added.status_code == 200, added.text
        assert client.get(path).json()["assets"][0]["reference"] == reference

        reachable = False
        assert client.get(path).json()["assets"][0]["state"] == "UNAVAILABLE"
        assert _command(client, path + "/assets", "knowledge", 1, reference=reference).json() == added.json()
        reachable, visible = True, False
        assert _command(client, path + "/assets", "different", 2, reference=reference).status_code == 404
        assert _command(client, path + "/archive", "archive", 2).status_code == 200
        asset = client.get(path).json()["assets"][0]
        assert asset["linkId"] == added.json()["linkId"]
        assert asset["state"] == "UNAVAILABLE"
        assert asset["reference"] is None
        assert "documentId" not in client.get(path + "/audit").text

        visible = True
        assert client.get(path).json()["assets"][0]["reference"] == reference
