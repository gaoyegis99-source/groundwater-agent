"""Agent-owned Kernel v29 DDL."""

AGENT_CORE_SCHEMA = """CREATE TABLE agent_runs (
    agent_run_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    goal_revision INTEGER NOT NULL,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    status TEXT NOT NULL,
    steps_json TEXT NOT NULL,
    output_json TEXT,
    input_tokens INTEGER NOT NULL,
    output_tokens INTEGER NOT NULL,
    total_tokens INTEGER NOT NULL,
    trace_id TEXT NOT NULL,
    error TEXT,
    created_at TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    duration_ms INTEGER
);
CREATE INDEX agent_runs_project_idx ON agent_runs(project_id);
CREATE TABLE agent_plans (
    agent_run_id TEXT NOT NULL REFERENCES agent_runs(agent_run_id),
    revision INTEGER NOT NULL,
    goal TEXT NOT NULL,
    steps_json TEXT NOT NULL,
    reason TEXT NOT NULL,
    PRIMARY KEY(agent_run_id, revision)
);
CREATE TABLE agent_actions (
    tool_call_id TEXT NOT NULL,
    agent_run_id TEXT NOT NULL REFERENCES agent_runs(agent_run_id),
    position INTEGER NOT NULL,
    tool TEXT NOT NULL,
    model_id TEXT NOT NULL,
    model_version TEXT NOT NULL,
    model_checksum TEXT NOT NULL,
    workflow_id TEXT NOT NULL,
    workflow_version TEXT NOT NULL,
    workflow_checksum TEXT NOT NULL,
    plan_revision INTEGER NOT NULL,
    arguments_json TEXT NOT NULL,
    input_references_json TEXT NOT NULL,
    schema_checksum TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    status TEXT NOT NULL,
    error TEXT,
    PRIMARY KEY(agent_run_id, tool_call_id)
);
CREATE TABLE agent_run_state (
    agent_run_id TEXT PRIMARY KEY REFERENCES agent_runs(agent_run_id),
    state_json TEXT NOT NULL
);
CREATE TABLE agent_memory (
    session_id TEXT PRIMARY KEY,
    through_goal_revision INTEGER NOT NULL,
    summary_json TEXT NOT NULL
);
"""

AGENT_OBSERVABILITY_SCHEMA = """CREATE TABLE agent_traces (
    trace_id TEXT PRIMARY KEY,
    agent_run_id TEXT NOT NULL,
    payload_json TEXT NOT NULL
);
CREATE TABLE agent_spans (
    span_id TEXT PRIMARY KEY,
    trace_id TEXT NOT NULL,
    payload_json TEXT NOT NULL
);
"""

PROVIDER_REQUESTS_SCHEMA = """CREATE TABLE provider_requests (
    provider_request_id TEXT PRIMARY KEY,
    agent_run_id TEXT,
    stage TEXT NOT NULL,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    protocol TEXT NOT NULL,
    attempt INTEGER NOT NULL CHECK(attempt IN (1,2)),
    outcome TEXT NOT NULL CHECK(outcome IN ('STARTED','SUCCEEDED','FAILED','UNKNOWN')),
    error_class TEXT,
    input_tokens INTEGER NOT NULL CHECK(input_tokens >= 0),
    output_tokens INTEGER NOT NULL CHECK(output_tokens >= 0),
    total_tokens INTEGER NOT NULL CHECK(total_tokens >= 0),
    latency_ms INTEGER NOT NULL CHECK(latency_ms >= 0),
    created_at TEXT NOT NULL
);
"""

AGENT_OBSERVABILITY_SCHEMA += PROVIDER_REQUESTS_SCHEMA

CONTEXT_VIEW_SCHEMA = """
CREATE TABLE context_views (
    context_view_id TEXT PRIMARY KEY,
    request_agent_run_id TEXT NOT NULL REFERENCES message_requests(agent_run_id),
    segment_id TEXT NOT NULL,
    request_ordinal INTEGER NOT NULL CHECK(request_ordinal >= 1),
    purpose TEXT NOT NULL CHECK(purpose IN (
        'INTENT_ROUTING','AGENT_INITIAL','AGENT_RESUME','COMPACTION'
    )),
    policy_version TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN (
        'COMPILED','DISPATCHED','COMPLETE','FAILED','REJECTED'
    )),
    tokenizer TEXT NOT NULL,
    available_input_tokens INTEGER NOT NULL CHECK(available_input_tokens >= 0),
    estimated_input_tokens INTEGER NOT NULL CHECK(estimated_input_tokens >= 0),
    actual_input_tokens INTEGER CHECK(actual_input_tokens IS NULL OR actual_input_tokens >= 0),
    manifest_json TEXT NOT NULL,
    error_code TEXT,
    created_at TEXT NOT NULL,
    finished_at TEXT,
    UNIQUE(request_agent_run_id, segment_id, purpose, request_ordinal)
);
"""

V23_AGENT_INDEX_STATEMENTS = ('CREATE INDEX agent_runs_status_idx ON agent_runs(status)', 'CREATE INDEX agent_actions_run_status_idx ON agent_actions(agent_run_id, status)')
