"""WorkItem-owned Kernel DDL and the frozen v37 command table."""

_COMMON_STATEMENTS = (
    """CREATE TABLE work_items (
    work_item_id TEXT PRIMARY KEY NOT NULL,
    project_id TEXT NOT NULL,
    owner_subject TEXT NOT NULL,
    title TEXT NOT NULL,
    description TEXT,
    revision INTEGER NOT NULL CHECK(revision >= 1),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    archived_at TEXT,
    UNIQUE(work_item_id, project_id, owner_subject)
)""",
    """CREATE INDEX work_items_owner_idx
ON work_items(project_id, owner_subject, work_item_id)""",
    """CREATE TRIGGER work_items_scope_immutable
BEFORE UPDATE OF project_id, owner_subject ON work_items
WHEN NEW.project_id <> OLD.project_id OR NEW.owner_subject <> OLD.owner_subject
BEGIN
    SELECT RAISE(ABORT, 'WORK_ITEM_SCOPE_IMMUTABLE');
END""",
    """CREATE TABLE work_item_sessions (
    work_item_id TEXT NOT NULL REFERENCES work_items(work_item_id),
    session_id TEXT NOT NULL UNIQUE REFERENCES sessions(session_id),
    linked_at TEXT NOT NULL
)""",
    """CREATE INDEX work_item_sessions_item_idx
ON work_item_sessions(work_item_id, session_id)""",
    """CREATE TRIGGER work_item_sessions_scope_check
BEFORE INSERT ON work_item_sessions
WHEN NOT EXISTS (
    SELECT 1 FROM work_items w JOIN sessions s
      ON s.project_id=w.project_id AND s.owner_subject=w.owner_subject
    WHERE w.work_item_id=NEW.work_item_id AND s.session_id=NEW.session_id
)
BEGIN
    SELECT RAISE(ABORT, 'WORK_ITEM_SESSION_SCOPE_MISMATCH');
END""",
    """CREATE TABLE work_item_references (
    link_id TEXT PRIMARY KEY NOT NULL,
    work_item_id TEXT NOT NULL REFERENCES work_items(work_item_id),
    kind TEXT NOT NULL CHECK(kind IN ('OBJECT','DATA','KNOWLEDGE','WORKFLOW','RESULT')),
    reference_key TEXT NOT NULL,
    reference_json TEXT NOT NULL CHECK(json_valid(reference_json)),
    linked_at TEXT NOT NULL,
    UNIQUE(work_item_id, kind, reference_key)
)""",
)

WORK_ITEM_V37_STATEMENTS = _COMMON_STATEMENTS + (
    """CREATE TABLE work_item_commands (
    sequence INTEGER NOT NULL CHECK(sequence >= 1),
    project_id TEXT NOT NULL,
    owner_subject TEXT NOT NULL,
    request_id TEXT NOT NULL,
    work_item_id TEXT NOT NULL,
    command TEXT NOT NULL CHECK(command IN (
        'CREATE','LINK_SESSION','LINK_OBJECT','LINK_ASSET','ARCHIVE'
    )),
    fingerprint TEXT NOT NULL,
    revision INTEGER NOT NULL CHECK(revision >= 1),
    changed INTEGER NOT NULL CHECK(changed IN (0,1)),
    link_id TEXT,
    created_at TEXT NOT NULL,
    PRIMARY KEY(work_item_id, sequence),
    FOREIGN KEY(work_item_id, project_id, owner_subject)
        REFERENCES work_items(work_item_id, project_id, owner_subject),
    UNIQUE(project_id, owner_subject, request_id)
)""",
)

WORK_ITEM_V38_TABLE_STATEMENTS = (
    """CREATE TABLE work_item_commands (
    sequence INTEGER NOT NULL CHECK(sequence >= 1),
    project_id TEXT NOT NULL,
    owner_subject TEXT NOT NULL,
    request_id TEXT NOT NULL,
    work_item_id TEXT NOT NULL,
    command TEXT NOT NULL CHECK(command IN (
        'CREATE','LINK_SESSION','LINK_OBJECT','LINK_ASSET','ARCHIVE','UPDATE','REVIEW'
    )),
    fingerprint TEXT NOT NULL,
    revision INTEGER NOT NULL CHECK(revision >= 1),
    changed INTEGER NOT NULL CHECK(changed IN (0,1)),
    link_id TEXT,
    created_at TEXT NOT NULL,
    review_session_id TEXT REFERENCES sessions(session_id),
    review_token TEXT,
    CHECK (
        (command = 'REVIEW' AND review_session_id IS NOT NULL AND review_token IS NOT NULL)
        OR (command <> 'REVIEW' AND review_session_id IS NULL AND review_token IS NULL)
    ),
    PRIMARY KEY(work_item_id, sequence),
    FOREIGN KEY(work_item_id, project_id, owner_subject)
        REFERENCES work_items(work_item_id, project_id, owner_subject),
    UNIQUE(project_id, owner_subject, request_id)
)""",
    """CREATE INDEX work_item_commands_review_idx
ON work_item_commands(work_item_id, review_session_id, review_token, sequence)""",
    """CREATE TABLE work_item_activity (
    work_item_id TEXT NOT NULL REFERENCES work_items(work_item_id),
    sequence INTEGER NOT NULL CHECK(sequence >= 1),
    kind TEXT NOT NULL CHECK(kind IN ('COMMAND','SESSION')),
    command_sequence INTEGER,
    session_id TEXT REFERENCES sessions(session_id),
    event_type TEXT,
    created_at TEXT NOT NULL,
    CHECK (
        (kind = 'COMMAND' AND command_sequence IS NOT NULL AND session_id IS NULL AND event_type IS NULL)
        OR (kind = 'SESSION' AND command_sequence IS NULL AND session_id IS NOT NULL AND event_type IS NOT NULL)
    ),
    PRIMARY KEY(work_item_id, sequence),
    FOREIGN KEY(work_item_id, command_sequence) REFERENCES work_item_commands(work_item_id, sequence)
)""",
)

WORK_ITEM_ACTIVITY_TRIGGERS = (
    """CREATE TRIGGER work_item_command_activity
AFTER INSERT ON work_item_commands
BEGIN
    INSERT INTO work_item_activity(work_item_id, sequence, kind, command_sequence, created_at)
    SELECT NEW.work_item_id, COALESCE(MAX(sequence), 0) + 1, 'COMMAND', NEW.sequence, NEW.created_at
    FROM work_item_activity WHERE work_item_id = NEW.work_item_id;
END""",
    """CREATE TRIGGER work_item_session_activity
AFTER INSERT ON session_events
WHEN NEW.event_type NOT IN ('agent.activity', 'agent.plan_recorded')
BEGIN
    INSERT INTO work_item_activity(work_item_id, sequence, kind, session_id, event_type, created_at)
    SELECT wi.work_item_id,
           (SELECT COALESCE(MAX(a.sequence), 0) + 1 FROM work_item_activity a
            WHERE a.work_item_id = wi.work_item_id),
           'SESSION', NEW.session_id, NEW.event_type, NEW.created_at
    FROM work_item_sessions link
    JOIN sessions s ON s.session_id = link.session_id
    JOIN work_items wi ON wi.work_item_id = link.work_item_id
       AND wi.project_id = s.project_id AND wi.owner_subject = s.owner_subject
    WHERE link.session_id = NEW.session_id;
END""",
)

WORK_ITEM_V37_SCHEMA = ";\n".join(WORK_ITEM_V37_STATEMENTS) + ";\n"
WORK_ITEM_SCHEMA = ";\n".join(
    _COMMON_STATEMENTS + WORK_ITEM_V38_TABLE_STATEMENTS + WORK_ITEM_ACTIVITY_TRIGGERS
) + ";\n"
