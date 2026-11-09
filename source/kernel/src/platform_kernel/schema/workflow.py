"""Workflow-owned Kernel DDL."""


WORKFLOW_SERVICE_BINDINGS_SCHEMA = """CREATE TABLE workflow_service_bindings (
    workflow_run_id TEXT PRIMARY KEY REFERENCES workflow_runs(run_id),
    target_digest TEXT NOT NULL
)"""

WORKFLOW_SCHEMA = """CREATE TABLE workflow_runs (
    run_id TEXT PRIMARY KEY,
    agent_run_id TEXT NOT NULL REFERENCES agent_runs(agent_run_id),
    session_id TEXT NOT NULL REFERENCES sessions(session_id),
    tool_call_id TEXT NOT NULL,
    confirmation_id TEXT REFERENCES confirmations(confirmation_id),
    attempt INTEGER NOT NULL DEFAULT 1 CHECK(attempt >= 1),
    retry_of_workflow_run_id TEXT REFERENCES workflow_runs(run_id),
    position INTEGER NOT NULL,
    project_id TEXT NOT NULL,
    model_id TEXT NOT NULL,
    model_version TEXT NOT NULL,
    model_checksum TEXT NOT NULL,
    workflow_id TEXT NOT NULL,
    workflow_version TEXT NOT NULL,
    workflow_checksum TEXT NOT NULL,
    execution_service TEXT NOT NULL,
    executor_id TEXT NOT NULL,
    arguments_json TEXT NOT NULL,
    input_references_json TEXT NOT NULL,
    status TEXT NOT NULL,
    remote_job_id TEXT,
    outputs_json TEXT NOT NULL,
    error TEXT,
    created_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    duration_ms INTEGER,
    UNIQUE(agent_run_id, tool_call_id, attempt)
);
CREATE INDEX workflow_runs_project_idx ON workflow_runs(project_id);
CREATE TABLE workflow_dispatches (
    workflow_run_id TEXT PRIMARY KEY REFERENCES workflow_runs(run_id),
    state TEXT NOT NULL,
    remote_job_id TEXT,
    deadline_at TEXT NOT NULL,
    next_poll_at TEXT NOT NULL,
    last_communication_error TEXT,
    claim_state TEXT
);
""" + WORKFLOW_SERVICE_BINDINGS_SCHEMA + ";\n\n"

V23_WORKFLOW_INDEX_STATEMENTS = ('CREATE INDEX workflow_runs_agent_position_idx ON workflow_runs(agent_run_id, position, attempt)', 'CREATE INDEX workflow_dispatches_due_idx ON workflow_dispatches(claim_state, state, next_poll_at)')
