from __future__ import annotations

from platform_kernel.database import append_event, connect_database
from kernel.tests.test_recommendations import _application, _dialogue, _reply


def test_session_directory_and_history_use_v20_without_recommendation_links(kernel_settings):
    app, client, _ = _application(kernel_settings, [_reply(cited=False)])
    session_id = client.post("/api/v12/sessions", json={"projectId": "p"}).json()["sessionId"]
    response = _dialogue(app, client, session_id, "比较可行方法")
    assert response.status_code == 200
    directory = client.get("/api/v12/sessions").json()["items"][0]
    assert "recommendation_id" not in directory
    assert "selection_revision" not in directory
    turn = client.get(f"/api/v12/sessions/{session_id}/messages").json()["turns"][0]
    assert turn["requestMode"] == "DIALOGUE"
    assert "recommendation_id" not in turn
    assert "selection_revision" not in turn
    with connect_database(app.database) as connection:
        assert connection.execute("SELECT version FROM schema_version").fetchone()[0] == 38


def test_session_directory_marks_verification_marker_inside_message(kernel_settings):
    app, client, _ = _application(kernel_settings, [_reply(cited=False)])
    session_id = client.post("/api/v12/sessions", json={"projectId": "p"}).json()["sessionId"]
    response = _dialogue(
        app,
        client,
        session_id,
        "请读取附件并执行 SYNTHETIC_TEST_ONLY-A7 验证",
    )
    assert response.status_code == 200

    directory = client.get("/api/v12/sessions").json()["items"]
    session = next(item for item in directory if item["sessionId"] == session_id)
    assert session["title"].startswith("【验证会话】")


def test_fresh_schema_has_no_recommendation_tables_or_columns(kernel_settings):
    app, _client, _ = _application(kernel_settings, [])
    with connect_database(app.database) as connection:
        tables = {row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
        assert "recommendations" not in tables
        assert "recommendation_selections" not in tables
        for table in ("sessions", "message_requests", "agent_requests"):
            columns = {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}
            assert "recommendation_id" not in columns
            assert "selection_revision" not in columns


def test_failed_run_with_persisted_output_still_projects_structured_reply(
    kernel_settings,
):
    app, client, _ = _application(kernel_settings, [_reply(cited=False)])
    session_id = client.post(
        "/api/v12/sessions", json={"projectId": "p"}
    ).json()["sessionId"]
    _dialogue(app, client, session_id, "比较可行方法")
    first = client.get(
        f"/api/v12/sessions/{session_id}/messages"
    ).json()["turns"][0]
    with connect_database(app.database) as connection:
        connection.execute(
            "UPDATE agent_runs SET status='FAILED', error='UNATTRIBUTED_FAILURE' "
            "WHERE agent_run_id=?",
            (first["agentRunId"],),
        )
        append_event(connection, session_id, "agent.failed", {
            "agent_run_id": first["agentRunId"],
            "goal_revision": first["goalRevision"],
            "diagnostics": None,
        })
    projected = client.get(
        f"/api/v12/sessions/{session_id}/messages"
    ).json()["turns"][0]
    assert projected["status"] == "FAILED"
    assert projected["reply"] == first["reply"]
    assert projected["reply"]["conclusion"]["markdown"] == (
        "Method comparison prepared."
    )


def test_task_directory_pages_by_latest_event_and_isolates_projects_and_owners(kernel_settings):
    from platform_kernel.interaction.actors import DEFAULT_LOCAL_ACTOR_SUBJECT
    from platform_kernel.interaction.persistence import create_session
    from platform_kernel.interaction.task_history import list_task_history
    from kernel.tests.test_session_results import _client, _insert_run

    app, client = _client(kernel_settings)
    for session, project, subject in (
        ("one", "p", DEFAULT_LOCAL_ACTOR_SUBJECT), ("two", "p", DEFAULT_LOCAL_ACTOR_SUBJECT),
        ("private", "p", "someone-else"), ("elsewhere", "another", DEFAULT_LOCAL_ACTOR_SUBJECT),
    ):
        create_session(app.database, session, project, owner_subject=subject)
        with connect_database(app.database) as db:
            agent = _insert_run(db, session, 1, project_id=project)
            db.execute("INSERT INTO messages(session_id,goal_revision,role,content) VALUES (?,1,'user',?)", (session, session))
            db.execute("INSERT INTO confirmations(confirmation_id,session_id,goal_revision,agent_run_id,status) VALUES(?,?,1,?,'PENDING')",
                       (session, session, agent))
            append_event(db, session, "confirmation.pending", {"agent_run_id": agent, "confirmation_id": session})
    with connect_database(app.database) as db:
        append_event(db, "one", "confirmation.revised", {"confirmation_id": "one"})
    listing = client.get("/api/v12/sessions/task-history", params={"limit": 1}).json()
    assert [row["title"] for row in listing["items"]] == ["one"]
    assert listing["items"][0]["status"] == "PENDING"
    rest = client.get("/api/v12/sessions/task-history", params={"limit": 1, "cursor": listing["nextCursor"]}).json()
    assert [row["title"] for row in rest["items"]] == ["two"]
    assert rest["nextCursor"] is None
    assert [row["title"] for row in list_task_history(app.database, "p", "someone-else", None, 10)["items"]] == ["private"]
