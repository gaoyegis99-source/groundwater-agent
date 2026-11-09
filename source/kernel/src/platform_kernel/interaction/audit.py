"""Content-free audit projection owned by the interaction subsystem."""

import json
from pathlib import Path

from ..database import connect_database


_AUDIT_TYPES = (
    "session.created",
    "session.metadata_updated",
    "turn.accepted",
    "confirmation.processing",
    "confirmation.confirmed",
    "confirmation.rejected",
    "confirmation.revised",
    "confirmation.recovery_created",
    "control.requested",
    "control.confirmed",
    "control.dismissed",
    "control.complete",
)


def list_audit_events(
    database: Path, project_id: str, cursor: int, limit: int
) -> dict[str, object]:
    placeholders = ",".join("?" for _ in _AUDIT_TYPES)
    with connect_database(database) as connection:
        rows = connection.execute(
            f"""SELECT e.event_id,e.session_id,e.event_type,e.payload_json,
                       e.created_at,s.owner_subject
                  FROM session_events e
                  JOIN sessions s ON s.session_id=e.session_id
                 WHERE s.project_id=? AND e.event_id>? AND e.event_type IN ({placeholders})
                 ORDER BY e.event_id LIMIT ?""",
            (project_id, cursor, *_AUDIT_TYPES, limit + 1),
        ).fetchall()
    selected = rows[:limit]
    items = []
    for row in selected:
        facts = json.loads(row["payload_json"])
        actor = (
            facts.get("decision_subject")
            or facts.get("requested_by")
            or facts.get("actor_subject")
            or facts.get("owner_subject")
            or row["owner_subject"]
        )
        items.append(
            {
                "event_id": row["event_id"],
                "session_id": row["session_id"],
                "owner_subject": row["owner_subject"],
                "event_type": row["event_type"],
                "actor_subject": actor,
                "facts": facts,
                "created_at": row["created_at"],
            }
        )
    return {
        "items": items,
        "next_cursor": selected[-1]["event_id"] if len(rows) > limit else None,
    }
