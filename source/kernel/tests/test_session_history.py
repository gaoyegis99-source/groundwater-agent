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
        assert connection.execute("SELECT version FROM schema_version").fetchone()[0] == 34


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
