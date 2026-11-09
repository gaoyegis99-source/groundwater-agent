from pathlib import Path

from ..database import connect_database


def workflow_status_counts(database: Path) -> dict[str, int]:
    with connect_database(database) as connection:
        return {
            row["status"]: row["count"]
            for row in connection.execute(
                "SELECT status,count(*) AS count FROM workflow_runs GROUP BY status"
            )
        }


def reconciliation_required_count(database: Path) -> int:
    with connect_database(database) as connection:
        return int(connection.execute(
            "SELECT count(*) FROM workflow_dispatches "
            "WHERE last_communication_error='RECONCILIATION_REQUIRED'"
        ).fetchone()[0])
