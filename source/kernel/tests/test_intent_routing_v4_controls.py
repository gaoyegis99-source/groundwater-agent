from __future__ import annotations

import asyncio
import json

from agents.testing import ModelStep, ScriptedModel, assistant_message
from fastapi.testclient import TestClient
import pytest

from platform_kernel.api import create_app
from platform_kernel.composition import PlatformApplication
from platform_kernel.database import connect_database
from platform_kernel.errors import ConfirmationConflictError
from platform_kernel.interaction.control_intents import rule_intent
from platform_kernel.interaction.control_proposal_commands import claim_control_proposal


def _router_model() -> ScriptedModel:
    return ScriptedModel([ModelStep(output=[assistant_message(json.dumps({
        "schemaVersion": "intent-decision.v4",
        "responseKind": "DIALOGUE",
        "semanticIntent": "DESIGN",
        "taskAction": "NONE",
        "dialoguePolicy": "READ_ONLY",
        "controlIntent": None,
        "clarificationReason": None,
    }))])])


def test_control_rule_creates_proposal_without_provider_or_new_turn(kernel_settings):
    assert rule_intent("批准") == "APPROVE_CONFIRMATION"
    router = ScriptedModel([])
    app = PlatformApplication(
        settings=kernel_settings(), model=ScriptedModel([]),
        intent_router_model=router,
    )
    session_id = app.interaction.create_session().session.session_id
    with connect_database(app.database) as connection:
        connection.execute(
            """INSERT INTO confirmations(
                   confirmation_id,session_id,goal_revision,agent_run_id,status,
                   revision,kind,created_at
               ) VALUES ('c',?,0,'run','PENDING',1,'INITIAL','now')""",
            (session_id,),
        )

    receipt = app.interaction.message(session_id, "control", "批准", "AUTO")

    assert receipt["schemaVersion"] == "message-request.v5"
    assert receipt["routing_source"] == "RULE"
    assert receipt["routing_status"] == "CONTROL_PENDING"
    assert receipt["intent"] == {
        "schemaVersion": "intent-decision.v4",
        "responseKind": "CONTROL",
        "semanticIntent": "UNSPECIFIED",
        "taskAction": "NONE",
        "dialoguePolicy": None,
        "controlIntent": "APPROVE_CONFIRMATION",
        "clarificationReason": None,
    }
    assert app.interaction.get(session_id).session.goal_revision == 0
    assert router.calls == ()
    proposal = app.interaction.messages(session_id, None, 50)["control_proposals"][0]
    app.interaction.dismiss_control_proposal(
        session_id, proposal["proposal_id"], "dismiss",
    )
    with connect_database(app.database) as connection:
        assert connection.execute(
            "SELECT status FROM confirmations WHERE confirmation_id='c'"
        ).fetchone()[0] == "PENDING"


def test_pending_control_proposal_blocks_ordinary_agent_start(kernel_settings):
    router = ScriptedModel([])
    app = PlatformApplication(
        settings=kernel_settings(), model=ScriptedModel([]),
        intent_router_model=router,
    )
    session_id = app.interaction.create_session().session.session_id
    with connect_database(app.database) as connection:
        connection.execute(
            """INSERT INTO confirmations(
                   confirmation_id,session_id,goal_revision,agent_run_id,status,
                   revision,kind,created_at
               ) VALUES ('blocked-c',?,0,'run','PENDING',1,'INITIAL','now')""",
            (session_id,),
        )
    app.interaction.message(session_id, "control-first", "批准", "AUTO")

    with pytest.raises(ConfirmationConflictError, match="CONTROL_PROPOSAL_PENDING"):
        app.interaction.message(
            session_id, "ordinary-while-pending", "请继续分析", "AUTO",
        )

    assert router.calls == ()
    assert app.interaction.get(session_id).session.goal_revision == 0


def test_stop_proposal_requires_second_confirmation_and_reuses_stop_chain(kernel_settings):
    app = PlatformApplication(
        settings=kernel_settings(), model=ScriptedModel([]),
        intent_router_model=ScriptedModel([]),
    )
    session_id = app.interaction.create_session().session.session_id
    with connect_database(app.database) as connection:
        connection.execute(
            "UPDATE sessions SET pending_agent_run_id='run',agent_start_state='ROUTING_REQUESTED' WHERE session_id=?",
            (session_id,),
        )

    app.interaction.message(session_id, "stop", "停止分析", "AUTO")
    proposal = app.interaction.messages(session_id, None, 50)["control_proposals"][0]
    with connect_database(app.database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM run_controls").fetchone()[0] == 0

    asyncio.run(app.interaction.confirm_control_proposal(
        session_id, proposal["proposal_id"], "confirm-stop",
    ))

    with connect_database(app.database) as connection:
        control = connection.execute("SELECT * FROM run_controls").fetchone()
        assert (control["agent_run_id"], control["status"]) == ("run", "REQUESTED")
        assert connection.execute(
            "SELECT status FROM control_proposals WHERE proposal_id=?",
            (proposal["proposal_id"],),
        ).fetchone()[0] == "COMPLETE"


def test_control_without_target_is_rule_clarification(kernel_settings):
    app = PlatformApplication(
        settings=kernel_settings(), model=ScriptedModel([]),
        intent_router_model=ScriptedModel([]),
    )
    session_id = app.interaction.create_session().session.session_id

    receipt = app.interaction.message(session_id, "cancel", "取消执行", "AUTO")

    assert receipt["routing_status"] == "CONTROL_COMPLETE"
    assert receipt["intent"]["responseKind"] == "NEEDS_CLARIFICATION"
    assert receipt["clarification_reason"] == "CONTROL_TARGET_MISSING"
    assert app.interaction.messages(session_id, None, 50)["control_proposals"] == []


def test_control_word_inside_sentence_uses_model_router(kernel_settings):
    router = _router_model()
    app = PlatformApplication(
        settings=kernel_settings(), model=ScriptedModel([]),
        intent_router_model=router,
    )
    session_id = app.interaction.create_session().session.session_id

    receipt = app.interaction.message(
        session_id, "ordinary", "请说明如何停止分析误差扩大", "AUTO",
    )
    assert receipt["routing_status"] == "ROUTING_REQUESTED"
    assert router.calls == ()


def test_status_rule_projects_kernel_state_without_provider(kernel_settings):
    app = PlatformApplication(
        settings=kernel_settings(), model=ScriptedModel([]),
        intent_router_model=ScriptedModel([]),
    )
    session_id = app.interaction.create_session().session.session_id
    with connect_database(app.database) as connection:
        connection.execute(
            "UPDATE sessions SET pending_agent_run_id='run',agent_start_state='ROUTING_REQUESTED' WHERE session_id=?",
            (session_id,),
        )

    receipt = app.interaction.message(session_id, "status", "查看状态", "AUTO")

    assert receipt["routing_source"] == "RULE"
    assert receipt["semantic_intent"] == "STATUS_QUERY"
    assert receipt["dialogue_policy"] == "READ_ONLY"
    message = app.interaction.messages(session_id, None, 50)["control_messages"][0]
    assert message["status_snapshot"] == "ANALYZING"
    assert message["waiting_reason"] == "ANALYSIS_IN_PROGRESS"
    assert message["results"] == []


def test_completed_run_is_not_a_stop_target(kernel_settings):
    app = PlatformApplication(
        settings=kernel_settings(), model=ScriptedModel([]),
        intent_router_model=ScriptedModel([]),
    )
    session_id = app.interaction.create_session().session.session_id
    with connect_database(app.database) as connection:
        connection.execute(
            """INSERT INTO agent_runs VALUES(
                 'done',?,'p',0,'test','test','COMPLETE','[]',NULL,0,0,0,
                 'trace',NULL,'now','now','now',0)""", (session_id,),
        )
        connection.execute(
            "UPDATE sessions SET agent_run_id='done' WHERE session_id=?", (session_id,),
        )

    receipt = app.interaction.message(session_id, "stop-done", "停止", "AUTO")

    assert receipt["clarification_reason"] == "CONTROL_TARGET_MISSING"
    assert app.interaction.messages(session_id, None, 50)["control_proposals"] == []


def test_public_control_dismiss_endpoint_is_idempotent(kernel_settings):
    app = PlatformApplication(
        settings=kernel_settings(), model=ScriptedModel([]),
        intent_router_model=ScriptedModel([]),
    )
    with TestClient(create_app(application=app)) as client:
        session_id = client.post(
            "/api/v12/sessions", json={"projectId": "p"},
        ).json()["sessionId"]
        with connect_database(app.database) as connection:
            connection.execute(
                """INSERT INTO confirmations(
                       confirmation_id,session_id,goal_revision,agent_run_id,status,
                       revision,kind,created_at
                   ) VALUES ('public-c',?,0,'run','PENDING',1,'INITIAL','now')""",
                (session_id,),
            )
        client.post(f"/api/v12/sessions/{session_id}/messages", json={
            "requestId": "public-control", "message": "拒绝",
            "requestedMode": "AUTO",
        })
        proposal = client.get(
            f"/api/v12/sessions/{session_id}/messages"
        ).json()["controlProposals"][0]
        path = f"/api/v12/sessions/{session_id}/control-proposals/{proposal['proposalId']}/dismiss"
        first = client.post(path, json={"requestId": "dismiss-id"})
        second = client.post(path, json={"requestId": "dismiss-id"})

    assert first.status_code == second.status_code == 200
    assert first.json()["controlProposals"][0]["status"] == "DISMISSED"


def test_stale_control_confirmation_retry_never_executes_action(kernel_settings):
    app = PlatformApplication(
        settings=kernel_settings(), model=ScriptedModel([]),
        intent_router_model=ScriptedModel([]),
    )
    session_id = app.interaction.create_session().session.session_id
    with connect_database(app.database) as connection:
        connection.execute(
            """INSERT INTO confirmations(
                   confirmation_id,session_id,goal_revision,agent_run_id,status,
                   revision,kind,created_at
               ) VALUES ('stale-c',?,0,'run','PENDING',1,'INITIAL','now')""",
            (session_id,),
        )
    app.interaction.message(session_id, "stale-control", "批准", "AUTO")
    proposal = app.interaction.messages(session_id, None, 50)["control_proposals"][0]
    with connect_database(app.database) as connection:
        connection.execute(
            "UPDATE confirmations SET status='SUPERSEDED' WHERE confirmation_id='stale-c'"
        )

    for _ in range(2):
        with pytest.raises(ConfirmationConflictError):
            asyncio.run(app.interaction.confirm_control_proposal(
                session_id, proposal["proposal_id"], "same-confirmation-request",
            ))

    with connect_database(app.database) as connection:
        assert connection.execute(
            "SELECT status FROM control_proposals WHERE proposal_id=?",
            (proposal["proposal_id"],),
        ).fetchone()[0] == "STALE"
        assert connection.execute(
            "SELECT decision FROM confirmations WHERE confirmation_id='stale-c'"
        ).fetchone()[0] is None


def test_confirmed_control_proposal_is_recovered_after_restart_boundary(
    kernel_settings,
):
    app = PlatformApplication(
        settings=kernel_settings(), model=ScriptedModel([]),
        intent_router_model=ScriptedModel([]),
    )
    session_id = app.interaction.create_session().session.session_id
    with connect_database(app.database) as connection:
        connection.execute(
            """UPDATE sessions SET pending_agent_run_id='recover-run',
                      agent_start_state='ROUTING_REQUESTED' WHERE session_id=?""",
            (session_id,),
        )
    app.interaction.message(session_id, "recover-stop", "停止分析", "AUTO")
    proposal = app.interaction.messages(session_id, None, 50)["control_proposals"][0]
    claim_control_proposal(
        app.database, session_id, app.settings.project_id,
        proposal["proposal_id"], "recover-confirm",
    )

    asyncio.run(app.interaction.recover_control_proposals())

    with connect_database(app.database) as connection:
        assert connection.execute(
            "SELECT status FROM control_proposals WHERE proposal_id=?",
            (proposal["proposal_id"],),
        ).fetchone()[0] == "COMPLETE"
        control = connection.execute(
            "SELECT * FROM run_controls WHERE agent_run_id='recover-run'"
        ).fetchone()
        assert control["status"] == "REQUESTED"
