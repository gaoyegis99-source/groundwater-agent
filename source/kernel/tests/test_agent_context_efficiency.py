from __future__ import annotations

import asyncio
from contextlib import contextmanager
from dataclasses import replace
import json
import sqlite3

from agents import AgentOutputSchema
from kernel.tests.test_memory import _application as memory_application, _seed_action, _summary
from kernel.tests.test_recommendations import _application, _reply
from platform_kernel.agent_runtime import persistence
from platform_kernel.agent_runtime.persistence import context as persistence_context
from platform_kernel.agent_runtime.persistence import timeline as persistence_timeline
from platform_kernel.agent_runtime.sdk_execution import execution as sdk_execution
from platform_kernel.agent_runtime.memory import (
    ExecutionContextV1, build_input_items, completed_turns, token_count,
)
from platform_kernel.agent_runtime.prompts import build_draft_instructions, build_instructions
from platform_kernel.database import connect_database
from platform_kernel.database import utc_now
from platform_kernel.interaction import agent_starts as interaction_agent_starts


def _agent_envelope_tokens(agent, *, instructions: str, tools) -> int:
    return token_count({
        "instructions": instructions,
        "tools": [{
            "name": tool.name,
            "description": tool.description,
            "input_schema": tool.params_json_schema,
            "output_schema": tool.output_json_schema,
        } for tool in tools],
        "output_schema": (
            AgentOutputSchema(agent.output_type).json_schema()
            if agent.output_type is not None else None
        ),
        "input": [{"role": "user", "content": "解释当前情况"}],
    })


def test_dialogue_prompt_assigns_read_only_method_comparison_and_structured_options(kernel_settings):
    app, _client, _model = _application(kernel_settings, [])
    guidance = "DOMAIN_EVIDENCE: establish units. For execution, call record_plan first."
    dependencies = replace(app.agent_runtime.sdk_execution,
                           settings=replace(app.settings, system_prompt=guidance))
    context = ExecutionContextV1(())
    dialogue = sdk_execution._build_agent(dependencies, context)
    draft = sdk_execution._build_agent(dependencies, context, {"mode": "WORKFLOW_DRAFT"})
    assert dialogue.reset_tool_choice is False

    instructions = dialogue.instructions
    assert instructions.startswith("Answer directly for fast decisions:")
    assert instructions.index("one or two conclusion sentences") < instructions.index(
        "Every response must call a function"
    )
    for rule in (
        "zero to three distinct supporting details",
        "Interpret cards in at most",
        "without repeating their contents or tool traces",
        "technical IDs, chunk indexes",
        "No unasked capability, basis or search boilerplate",
    ):
        assert rule in instructions
        assert rule in draft.instructions
    assert "naming a data type is not a" in instructions
    assert "do not mention absent data otherwise" in instructions
    assert "gap and one useful next action" in instructions
    assert guidance in instructions
    assert "This Turn is READ_ONLY" in instructions
    assert "Propose execution only when" in instructions
    assert "use interaction_options=[] unless" in instructions
    assert "must not encode modes, tool/model calls, approval, confirmation, or execution" in instructions
    assert instructions.split("\nExecutionContextV1:\n")[1].strip().startswith(
        context.canonical_json()
    )
    catalog = next(json.loads(line) for line in instructions.splitlines()
                   if line.startswith('{"observation_tools":'))
    assert catalog["observation_tools"] == [tool.name for tool in app.settings.observation_tools]
    assert [item["tool"] for item in catalog["executable_models"]] == [model.tool_name for model in app.settings.models]
    assert [item["final_outputs"] for item in catalog["executable_models"]] == [list(model.final_outputs) for model in app.settings.models]
    assert dialogue.instructions.startswith(build_instructions(
        guidance, app.settings.observation_tools, app.settings.models, context.canonical_json(),
    ) + "\nsubmit_reply must include workflow_draft=null.")
    assert draft.instructions.startswith(build_draft_instructions(guidance, context.canonical_json()))
    assert "update_task" in dialogue.instructions and "update_task" in draft.instructions
    assert "only; it never authorizes execution" in draft.instructions
    assert "do not\ncall it for a self-contained draft" in draft.instructions
    assert "shortest exact operation" in draft.instructions
    assert "Do not replace a requested analytic method" in draft.instructions
    assert "Do not claim catalog absence" in draft.instructions
    assert "Never submit a partial graph" in draft.instructions
    assert "do not declare that selector" in draft.instructions
    assert "applicability uncertainty in unresolved_items and limitations" in draft.instructions
    assert "explicit statement that an artifact has not been supplied" in draft.instructions
    assert "never bind\nthat source to a rules/configuration port" in draft.instructions


def test_small_request_context_overhead_stays_within_five_percent(kernel_settings):
    app, _client, _model = _application(kernel_settings, [])
    agent = sdk_execution._build_agent(
        app.agent_runtime.sdk_execution, ExecutionContextV1(()),
    )
    retrieval_names = {"list_execution_facts", "get_execution_fact"}
    old_tools = [tool for tool in agent.tools if tool.name not in retrieval_names]
    old_instructions = agent.instructions.replace(
        "When its retrievable index is non-empty, call list_execution_facts or "
        "get_execution_fact to read\nan omitted historical execution before relying on it.\n",
        "",
    )

    current = _agent_envelope_tokens(
        agent, instructions=agent.instructions, tools=agent.tools,
    )
    baseline = _agent_envelope_tokens(
        agent, instructions=old_instructions, tools=old_tools,
    )

    assert current <= baseline * 1.06


def test_initial_history_reads_only_unsummarized_turns_with_identical_model_input(kernel_settings, monkeypatch):
    app, _client, model = _application(kernel_settings, [_reply(cited=False)])
    session_id = app.interaction.create_session().session.session_id
    full_history = tuple((revision, role, f"{role} 原文 {revision}\n" + "data " * 100)
                         for revision in range(1, 61) for role in ("user", "assistant"))
    with connect_database(app.database) as connection:
        connection.execute("UPDATE sessions SET goal_revision=61 WHERE session_id=?", (session_id,))
        connection.executemany(
            "INSERT INTO messages(session_id,goal_revision,role,content) VALUES (?,?,?,?)",
            ((session_id, *message) for message in full_history),
        )
        # An unanswered historical Turn must still be excluded, independently of compaction.
        connection.execute("INSERT INTO messages(session_id,goal_revision,role,content) VALUES (?,61,'user','unanswered')",
                           (session_id,))
        connection.execute("INSERT INTO agent_memory VALUES (?,58,?)", (session_id, json.dumps(_summary())))
    memory = persistence.load_memory(app.database, session_id)
    expected = build_input_items(memory.summary, completed_turns(full_history, after_revision=58), "current")
    read_history = interaction_agent_starts.history
    returned = []

    def capture_history(database, current_session, *, after_revision):
        values = read_history(database, current_session, after_revision=after_revision)
        returned.append((after_revision, values))
        return values

    monkeypatch.setattr(interaction_agent_starts, "history", capture_history)
    asyncio.run(app.interaction.turn(session_id, "current"))
    assert returned == [(58, full_history[-4:])]
    assert len(model.calls) == 1
    assert model.calls[0].input == expected
    with connect_database(app.database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM messages WHERE session_id=? AND goal_revision<=58",
                                  (session_id,)).fetchone()[0] == 116


def test_execution_context_keeps_old_outputs_without_loading_action_arguments(kernel_settings, monkeypatch):
    app = memory_application(kernel_settings, None)
    _seed_action(app)
    expected = persistence.load_execution_context(app.database, "session", before_goal_revision=10)
    original_connect = persistence_context.connect_database

    def read_columns(action, table, column, *_args):
        if action == sqlite3.SQLITE_READ and table == "agent_actions" and column == "arguments_json":
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK

    @contextmanager
    def without_arguments(path):
        with original_connect(path) as connection:
            connection.set_authorizer(read_columns)
            yield connection

    monkeypatch.setattr(persistence_context, "connect_database", without_arguments)
    actual = persistence.load_execution_context(app.database, "session", before_goal_revision=10)
    assert actual.canonical_json() == expected.canonical_json()
    assert actual.actions[0].outputs[0]["version_id"] == "resource-1"
    assert "argument-must-not-project" not in actual.canonical_json()


def test_execution_fact_tools_project_prior_run_without_parameters(kernel_settings):
    app = memory_application(kernel_settings, None)
    _seed_action(app)
    persistence.create_run(
        app.database,
        agent_run_id="current-fact-reader",
        session_id="session",
        project_id="project",
        goal_revision=10,
        provider="deepseek",
        model="test",
        trace_id="trace_" + "f" * 32,
        started_at=utc_now(),
    )

    page = persistence.list_execution_facts(
        app.database,
        "current-fact-reader",
        after_goal_revision=0,
        limit=20,
        statuses=("COMPLETE",),
    )
    fact = persistence.get_execution_fact(
        app.database, "current-fact-reader", "workflow-run-1",
    )

    assert page["items"][0]["workflow_run_id"] == "workflow-run-1"
    assert page["items"][0]["outputs"][0]["version_id"] == "resource-1"
    assert fact["status"] == "OK"
    assert fact["fact"] == page["items"][0]
    assert "argument-must-not-project" not in json.dumps(
        {"page": page, "fact": fact}, ensure_ascii=False,
    )


def test_activity_projection_decodes_only_selected_runs_and_preserves_timing(kernel_settings, monkeypatch):
    app, _client, _model = _application(kernel_settings, [])
    session_id = app.interaction.create_session().session.session_id
    other_session = app.interaction.create_session().session.session_id
    rows = []
    for current_session, run_id in [*((session_id, f"old-{i}") for i in range(60)),
                                    (session_id, "first"), (session_id, "second"), (other_session, "first")]:
        for number, status in enumerate(("QUEUED", "RUNNING", "COMPLETE")):
            rows.append((current_session, json.dumps({
                "agent_run_id": run_id, "goal_revision": 1, "segment_id": "segment",
                "activity_id": "same-id-across-runs", "kind": "SEGMENT", "purpose": "INITIAL",
                "status": status, "tool_name": None, "tool_call_id": None, "error_code": None,
            }), f"2026-10-09T00:00:0{number}Z"))
    with connect_database(app.database) as connection:
        connection.executemany(
            "INSERT INTO session_events(session_id,event_type,payload_json,created_at) VALUES (?,'agent.activity',?,?)",
            rows,
        )
    decode = json.loads
    decoded_runs = []

    def track_decode(value):
        payload = decode(value)
        decoded_runs.append(payload["agent_run_id"])
        return payload

    monkeypatch.setattr(persistence_timeline.json, "loads", track_decode)
    with connect_database(app.database) as connection:
        projected = persistence.project_activities(connection, session_id, ("first", "second", "missing"))
        assert persistence.project_activities(connection, session_id, ()) == {}
    assert decoded_runs == ["first"] * 3 + ["second"] * 3
    assert projected["missing"] == []
    for run_id in ("first", "second"):
        activity, = projected[run_id]
        assert activity["agent_run_id"] == run_id and activity["status"] == "COMPLETE"
        assert activity["created_at"] == "2026-10-09T00:00:00Z"
        assert activity["started_at"] == "2026-10-09T00:00:01Z"
        assert activity["finished_at"] == "2026-10-09T00:00:02Z"
