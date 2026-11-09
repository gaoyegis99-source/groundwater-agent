"""Task-owned Kernel v29 DDL."""

TASK_CORE_SCHEMA = """CREATE TABLE tasks (
    task_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL UNIQUE REFERENCES sessions(session_id),
    revision INTEGER NOT NULL,
    lifecycle TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE task_revisions (
    task_id TEXT NOT NULL REFERENCES tasks(task_id),
    revision INTEGER NOT NULL,
    agent_run_id TEXT NOT NULL,
    tool_call_id TEXT NOT NULL,
    spec_json TEXT NOT NULL,
    evidence_json TEXT NOT NULL,
    update_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY(task_id, revision),
    UNIQUE(agent_run_id, tool_call_id)
);
CREATE TABLE task_update_calls (
    agent_run_id TEXT NOT NULL REFERENCES agent_runs(agent_run_id),
    tool_call_id TEXT NOT NULL,
    update_json TEXT NOT NULL,
    PRIMARY KEY(agent_run_id, tool_call_id)
);
CREATE TABLE task_runs (
    task_id TEXT NOT NULL REFERENCES tasks(task_id),
    agent_run_id TEXT NOT NULL REFERENCES agent_runs(agent_run_id),
    task_revision INTEGER NOT NULL,
    reason TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY(task_id, agent_run_id)
);
CREATE TABLE task_completions (
    task_id TEXT NOT NULL,
    revision INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY(task_id, revision)
);
CREATE TABLE confirmation_tasks (
    confirmation_id TEXT PRIMARY KEY REFERENCES confirmations(confirmation_id),
    task_id TEXT NOT NULL,
    task_revision INTEGER NOT NULL
);

"""

TASK_PREPARATION_SCHEMA = """
CREATE TABLE task_preparations (
    preparation_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES sessions(session_id),
    agent_run_id TEXT NOT NULL UNIQUE REFERENCES agent_runs(agent_run_id),
    task_id TEXT,
    base_task_revision INTEGER,
    revision INTEGER NOT NULL CHECK(revision >= 1),
    status TEXT NOT NULL CHECK(status IN ('READY','BLOCKED')),
    snapshot_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE task_preparation_revisions (
    preparation_id TEXT NOT NULL REFERENCES task_preparations(preparation_id),
    revision INTEGER NOT NULL CHECK(revision >= 1),
    agent_run_id TEXT NOT NULL REFERENCES agent_runs(agent_run_id),
    tool_call_id TEXT NOT NULL,
    snapshot_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY(preparation_id, revision),
    UNIQUE(agent_run_id, tool_call_id)
);
CREATE INDEX task_preparations_session_idx
ON task_preparations(session_id, updated_at, revision);
"""
