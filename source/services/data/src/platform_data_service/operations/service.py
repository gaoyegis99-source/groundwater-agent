from psycopg.types.json import Jsonb
from ..adapters.database import event
from ..errors import DataProblemError
from .. import openlineage


def get(c, project, oid):
    row = c.execute(
        """SELECT id,kind,target,state,generation,result,error,actor,created_at,updated_at
        FROM operations WHERE id=%s AND project=%s""",
        (oid, project),
    ).fetchone()
    if not row:
        raise DataProblemError("OPERATION_NOT_FOUND", 404)
    row["events"] = c.execute(
        "SELECT seq,state,generation,created_at FROM operation_events WHERE operation_id=%s ORDER BY seq",
        (oid,),
    ).fetchall()
    return row


def get_owned(c, project, oid, actor):
    row = get(c, project, oid)
    if row["actor"].get("subject") != actor["subject"]:
        raise DataProblemError("OPERATION_NOT_FOUND", 404)
    return row


def claim(c, project, lease_seconds):
    op = c.execute(
        """SELECT * FROM operations WHERE project=%s AND
        (state='QUEUED' OR (state='RUNNING' AND lease_until<now()))
        ORDER BY created_at FOR UPDATE SKIP LOCKED LIMIT 1""",
        (project,),
    ).fetchone()
    if op is None:
        return None
    # Re-execution uses frozen inputs and a fresh attempt directory; never replays a commit.
    op = c.execute(
        """UPDATE operations SET state='RUNNING',generation=generation+1,
        lease_until=now()+%s*interval '1 second',updated_at=now() WHERE id=%s RETURNING *""",
        (lease_seconds, op["id"]),
    ).fetchone()
    event(c, op)
    return op


def heartbeat(c, oid, generation, lease_seconds):
    return (
        c.execute(
            """UPDATE operations SET lease_until=now()+%s*interval '1 second',updated_at=now()
        WHERE id=%s AND generation=%s AND state='RUNNING' AND lease_until>now()""",
            (lease_seconds, oid, generation),
        ).rowcount
        == 1
    )


def finish(c, op, result=None, error=None, result_hours=24):
    publication = None
    if op["kind"] == "prepare":
        publication = c.execute(
            "SELECT * FROM publications WHERE id=%s FOR UPDATE", (op["target"],)
        ).fetchone()
    current = c.execute(
        "SELECT * FROM operations WHERE id=%s FOR UPDATE", (op["id"],)
    ).fetchone()
    if current["state"] != "RUNNING" or current["generation"] != op["generation"]:
        return False
    alive = c.execute(
        "SELECT lease_until>now() AS alive FROM operations WHERE id=%s", (op["id"],)
    ).fetchone()["alive"]
    if not alive:
        return False
    state = "FAILED" if error else "SUCCEEDED"
    if op["kind"] == "prepare":
        c.execute(
            "UPDATE publications SET state=%s,prepared=%s WHERE id=%s AND state='PREPARING'",
            (
                "FAILED" if error else "READY",
                Jsonb(result) if result else None,
                op["target"],
            ),
        )
        public_result = {"publication_id": op["target"]} if not error else None
        if error and publication is not None:
            openlineage.record_fail(
                c,
                op["project"],
                publication,
                str(error.get("code") or "PUBLICATION_PREPARE_FAILED"),
            )
    elif op["kind"] in {"query", "preview"}:
        public_result = result["receipt"] if result else None
        table = "queries" if op["kind"] == "query" else "previews"
        c.execute(
            f"""UPDATE {table} SET receipt=%s,result_object=%s,
            expires_at=CASE WHEN %s THEN now()+%s*interval '1 hour' ELSE NULL END WHERE id=%s""",
            (
                Jsonb(public_result) if public_result else None,
                Jsonb(result["object"]) if result else None,
                result is not None,
                result_hours,
                op["id"],
            ),
        )
    else:
        raise RuntimeError("UNSUPPORTED_OPERATION_KIND")
    updated = c.execute(
        """UPDATE operations SET state=%s,result=%s,error=%s,updated_at=now()
        WHERE id=%s RETURNING *""",
        (
            state,
            Jsonb(public_result) if public_result else None,
            Jsonb(error) if error else None,
            op["id"],
        ),
    ).fetchone()
    event(c, updated)
    return True
