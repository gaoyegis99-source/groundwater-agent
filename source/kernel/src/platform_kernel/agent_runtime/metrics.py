from pathlib import Path

from ..database import connect_database


def agent_status_counts(database: Path) -> dict[str, int]:
    with connect_database(database) as connection:
        return {
            row["status"]: row["count"]
            for row in connection.execute(
                "SELECT status,count(*) AS count FROM agent_runs GROUP BY status"
            )
        }
