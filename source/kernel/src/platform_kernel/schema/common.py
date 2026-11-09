"""DDL shared by the Kernel store as a whole."""

VERSION_SCHEMA = """
CREATE TABLE schema_version(version INTEGER NOT NULL);

"""

RESEARCH_IDENTITY_SCHEMA = """
CREATE TABLE research_users(
    user_id TEXT PRIMARY KEY,
    username TEXT NOT NULL UNIQUE,
    display_name TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN (0,1)),
    created_at TEXT NOT NULL
);
CREATE TABLE research_project_memberships(
    project_id TEXT NOT NULL,
    user_id TEXT NOT NULL REFERENCES research_users(user_id) ON DELETE CASCADE,
    role TEXT NOT NULL CHECK(role IN ('admin','researcher','viewer')),
    PRIMARY KEY(project_id,user_id)
);
CREATE TABLE research_sessions(
    session_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES research_users(user_id) ON DELETE CASCADE,
    csrf_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    idle_expires_at TEXT NOT NULL,
    absolute_expires_at TEXT NOT NULL,
    revoked_at TEXT
);
CREATE INDEX research_sessions_user_idx ON research_sessions(user_id,revoked_at);
CREATE TABLE research_api_tokens(
    token_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES research_users(user_id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    token_hash TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    last_used_at TEXT,
    revoked_at TEXT
);
CREATE INDEX research_api_tokens_user_idx ON research_api_tokens(user_id,revoked_at);
"""
