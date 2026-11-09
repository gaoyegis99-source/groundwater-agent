"""Interaction-owned Kernel v31 DDL."""

SESSIONS_SCHEMA = """CREATE TABLE sessions (
    session_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    goal_revision INTEGER NOT NULL,
    agent_run_id TEXT,
    pending_agent_run_id TEXT,
    agent_start_state TEXT,
    owner_subject TEXT NOT NULL,
    title TEXT,
    archived_at TEXT
);
"""

SESSIONS_OWNER_SUBJECT_TRIGGER = """CREATE TRIGGER sessions_owner_subject_immutable
BEFORE UPDATE ON sessions
WHEN NEW.owner_subject <> OLD.owner_subject
BEGIN
    SELECT RAISE(ABORT, 'SESSION_OWNER_IMMUTABLE');
END;
"""

INTERACTION_CORE_SCHEMA = SESSIONS_SCHEMA + SESSIONS_OWNER_SUBJECT_TRIGGER + """
CREATE TABLE messages (
    message_id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL REFERENCES sessions(session_id),
    goal_revision INTEGER NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL
);
CREATE TABLE message_requests (
    agent_run_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES sessions(session_id),
    request_id TEXT NOT NULL,
    goal_revision INTEGER NOT NULL,
    message TEXT NOT NULL,
    input_references_json TEXT NOT NULL,
    requested_mode TEXT NOT NULL CHECK(requested_mode IN (
        'AUTO','DIALOGUE','WORKFLOW_DRAFT'
    )),
    routing_source TEXT NOT NULL CHECK(routing_source IN (
        'AUTO','MODEL','RULE','EXPLICIT'
    )),
    workflow_draft_id TEXT,
    corrects_request_id TEXT,
    routing_status TEXT NOT NULL CHECK(routing_status IN (
        'ROUTING_REQUESTED','ROUTING_RUNNING','DECIDED','START_REQUESTED',
        'CONTROL_PENDING','CONTROL_COMPLETE','FAILED'
    )),
    intent_decision TEXT CHECK(intent_decision IN (
        'DIALOGUE','WORKFLOW_DRAFT','CONTROL','NEEDS_CLARIFICATION'
    )),
    dialogue_policy TEXT CHECK(dialogue_policy IN (
        'READ_ONLY','EXECUTION_PROPOSAL'
    )),
    semantic_intent TEXT CHECK(semantic_intent IN (
        'CONSULT','COMPARE','DESIGN','EXECUTE_ON_DATA','CREATE_WORKFLOW',
        'REVIEW_RESULT','STATUS_QUERY','UNSPECIFIED','UNKNOWN'
    )),
    task_action TEXT CHECK(task_action IN (
        'NONE','CREATE','REVISE','CONTINUE','REVIEW'
    )),
    control_intent TEXT CHECK(control_intent IN (
        'APPROVE_CONFIRMATION','REJECT_CONFIRMATION','STOP_ANALYSIS','CANCEL_EXECUTION'
    )),
    clarification_reason TEXT CHECK(clarification_reason IN (
        'REQUEST_KIND_AMBIGUOUS','TASK_CONFLICT','CONTROL_TARGET_MISSING',
        'CONTROL_TARGET_AMBIGUOUS'
    )),
    resolved_mode TEXT CHECK(resolved_mode IN (
        'DIALOGUE','WORKFLOW_DRAFT'
    )),
    clarification_required INTEGER NOT NULL CHECK(clarification_required IN (0,1)),
    route_provider TEXT,
    route_model TEXT,
    route_trace_id TEXT,
    route_input_tokens INTEGER NOT NULL CHECK(route_input_tokens >= 0),
    route_output_tokens INTEGER NOT NULL CHECK(route_output_tokens >= 0),
    route_total_tokens INTEGER NOT NULL CHECK(route_total_tokens >= 0),
    route_attempts INTEGER NOT NULL CHECK(route_attempts >= 0),
    route_provider_requests INTEGER NOT NULL CHECK(route_provider_requests >= 0),
    unknown_cost_attempts INTEGER NOT NULL CHECK(unknown_cost_attempts >= 0),
    route_started_at TEXT,
    route_finished_at TEXT,
    route_duration_ms INTEGER CHECK(route_duration_ms IS NULL OR route_duration_ms >= 0),
    error_code TEXT,
    created_at TEXT NOT NULL,
    CHECK(
        (intent_decision IS NULL AND dialogue_policy IS NULL
            AND semantic_intent IS NULL AND task_action IS NULL
            AND control_intent IS NULL AND clarification_reason IS NULL)
        OR (intent_decision = 'DIALOGUE' AND dialogue_policy IS NOT NULL
            AND semantic_intent IS NOT NULL AND task_action IS NOT NULL
            AND control_intent IS NULL AND clarification_reason IS NULL)
        OR (intent_decision = 'WORKFLOW_DRAFT' AND dialogue_policy IS NULL
            AND semantic_intent = 'CREATE_WORKFLOW' AND task_action IS NOT NULL
            AND control_intent IS NULL AND clarification_reason IS NULL)
        OR (intent_decision = 'CONTROL' AND dialogue_policy IS NULL
            AND semantic_intent = 'UNSPECIFIED' AND task_action = 'NONE'
            AND control_intent IS NOT NULL AND clarification_reason IS NULL)
        OR (intent_decision = 'NEEDS_CLARIFICATION' AND dialogue_policy IS NULL
            AND semantic_intent IS NOT NULL AND task_action IS NOT NULL
            AND control_intent IS NULL AND clarification_reason IS NOT NULL)
    ),
    UNIQUE(session_id, request_id)
);
CREATE TABLE agent_requests (
    agent_run_id TEXT PRIMARY KEY,
    mode TEXT NOT NULL CHECK(mode IN ('DIALOGUE','WORKFLOW_DRAFT')),
    workflow_draft_id TEXT,
    dialogue_policy TEXT CHECK(dialogue_policy IN (
        'READ_ONLY','EXECUTION_PROPOSAL'
    )),
    CHECK(mode = 'DIALOGUE' OR dialogue_policy IS NULL)
);
CREATE TABLE workflow_drafts (
    draft_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES sessions(session_id),
    request_id TEXT NOT NULL,
    message TEXT NOT NULL,
    goal_revision INTEGER NOT NULL,
    agent_run_id TEXT NOT NULL UNIQUE,
    payload_json TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(session_id, request_id)
);
CREATE TABLE confirmations (
    confirmation_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES sessions(session_id),
    goal_revision INTEGER NOT NULL,
    agent_run_id TEXT NOT NULL,
    status TEXT NOT NULL,
    reason TEXT,
    decision TEXT,
    decision_subject TEXT,
    decision_state TEXT,
    revision INTEGER NOT NULL DEFAULT 1,
    kind TEXT NOT NULL DEFAULT 'INITIAL' CHECK(kind IN ('INITIAL','EDITED','RECOVERY')),
    replaces_confirmation_id TEXT REFERENCES confirmations(confirmation_id),
    source_workflow_run_id TEXT,
    request_id TEXT,
    request_fingerprint TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(session_id, request_id)
);
CREATE TABLE confirmation_actions (
    confirmation_id TEXT NOT NULL REFERENCES confirmations(confirmation_id),
    position INTEGER NOT NULL,
    tool_call_id TEXT NOT NULL,
    tool TEXT NOT NULL,
    model_id TEXT NOT NULL,
    model_version TEXT NOT NULL,
    model_checksum TEXT NOT NULL,
    workflow_id TEXT NOT NULL,
    workflow_version TEXT NOT NULL,
    workflow_checksum TEXT NOT NULL,
    input_schema_checksum TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    arguments_json TEXT NOT NULL,
    input_references_json TEXT NOT NULL,
    PRIMARY KEY(confirmation_id, position)
);

"""

INTERACTION_EVENT_SCHEMA = """CREATE TABLE session_events (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL REFERENCES sessions(session_id),
    event_type TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX session_events_session_idx ON session_events(session_id, event_id);

"""

RUN_CONTROLS_SCHEMA = """CREATE TABLE run_controls (
    session_id TEXT NOT NULL REFERENCES sessions(session_id),
    request_id TEXT NOT NULL,
    agent_run_id TEXT NOT NULL,
    requested_by TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    claim_owner TEXT,
    claim_generation INTEGER NOT NULL DEFAULT 0,
    claim_until TEXT,
    PRIMARY KEY(session_id, request_id)
);
"""
RUN_CONTROLS_INDEX_SQL = (
    "CREATE INDEX run_controls_claim_idx "
    "ON run_controls(status, claim_until, created_at, session_id, request_id)"
)

INTERACTION_CONTROL_SCHEMA = RUN_CONTROLS_SCHEMA + RUN_CONTROLS_INDEX_SQL + ";\n" + """
CREATE TABLE message_corrections (
    session_id TEXT NOT NULL,
    request_id TEXT NOT NULL,
    target_agent_run_id TEXT NOT NULL,
    PRIMARY KEY(session_id, request_id)
);
CREATE TABLE control_proposals (
    proposal_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES sessions(session_id),
    source_request_id TEXT NOT NULL,
    intent TEXT NOT NULL CHECK(intent IN (
        'APPROVE_CONFIRMATION','REJECT_CONFIRMATION','STOP_ANALYSIS','CANCEL_EXECUTION'
    )),
    target_type TEXT NOT NULL CHECK(target_type IN ('CONFIRMATION','AGENT_RUN')),
    target_id TEXT NOT NULL,
    task_revision INTEGER,
    status TEXT NOT NULL CHECK(status IN (
        'PENDING','CONFIRMED','DISMISSED','STALE','COMPLETE','FAILED'
    )),
    decision_request_id TEXT,
    created_at TEXT NOT NULL,
    decided_at TEXT,
    UNIQUE(session_id, source_request_id),
    UNIQUE(session_id, decision_request_id)
);
"""

V23_INTERACTION_INDEX_STATEMENTS = ('CREATE INDEX sessions_agent_start_idx ON sessions(agent_start_state) WHERE pending_agent_run_id IS NOT NULL', 'CREATE INDEX messages_session_revision_role_idx ON messages(session_id, goal_revision, role)', "CREATE INDEX message_requests_route_queue_idx ON message_requests(routing_status) WHERE requested_mode = 'AUTO'", 'CREATE INDEX message_requests_session_revision_idx ON message_requests(session_id, goal_revision)', 'CREATE INDEX control_proposals_session_status_idx ON control_proposals(session_id, status)', 'CREATE INDEX confirmations_session_revision_idx ON confirmations(session_id, goal_revision)', 'CREATE INDEX confirmations_decision_queue_idx ON confirmations(status, decision_state)', "CREATE UNIQUE INDEX confirmations_current_pending_idx ON confirmations(agent_run_id) WHERE status = 'PENDING'")
