from psycopg.types.json import Jsonb

from .. import access
from ..adapters.database import digest, enqueue, event
from ..catalog.service import version
from ..errors import DataProblemError


def submit(c, project, body, actor):
    # Serialize same-key submissions before resolving any mutable semantic pointer.
    c.execute(
        "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
        (project + ":query:" + actor["subject"] + ":" + body.idempotency_key,),
    )
    hashed = digest(body.model_dump())
    old = c.execute(
        """SELECT id,digest FROM queries
        WHERE project=%s AND owner_subject=%s AND idem=%s""",
        (project, actor["subject"], body.idempotency_key),
    ).fetchone()
    if old:
        if old["digest"] != hashed:
            raise DataProblemError("IDEMPOTENCY_CONFLICT", 409)
        return {"query_id": old["id"], "operation_id": old["id"]}
    plan = body.model_dump()
    plan["inputs"] = []
    if set(body.semantic_ids) - set(body.version_ids):
        raise DataProblemError("SEMANTIC_VERSION_MISMATCH")
    for vid in body.version_ids:
        access.require_version(c, project, vid, actor)
        row = version(c, project, vid)
        sid = body.semantic_ids.get(vid, row["semantic_id"])
        sem = c.execute(
            "SELECT body FROM semantic_revisions WHERE id=%s AND version_id=%s",
            (sid, vid),
        ).fetchone()
        if not sem:
            raise DataProblemError("SEMANTIC_VERSION_MISMATCH", 409)
        objects = [
            x["descriptor"]
            for x in c.execute(
                "SELECT descriptor FROM objects WHERE version_id=%s ORDER BY id", (vid,)
            )
        ]
        plan["inputs"].append(
            {
                "version_id": vid,
                "semantic_id": sid,
                "objects": objects,
                "descriptor": row["descriptor"],
                "semantics": sem["body"],
            }
        )
    if body.kind == "table":
        inp = plan["inputs"][0]
        if not any(x["role"] == "query" for x in inp["objects"]):
            raise DataProblemError("VERSION_NOT_QUERYABLE")
        available = {x["id"]: x for x in inp["descriptor"]["fields"]}
        requested = set(
            body.fields
            + body.group_by
            + [x.field for x in body.filters]
            + [x.field for x in body.aggregates if x.field]
        )
        if requested - available.keys():
            raise DataProblemError("UNKNOWN_FIELD", fields=sorted(requested - available.keys()))
        aliases = [x.alias for x in body.aggregates]
        if len(set(aliases)) != len(aliases) or set(aliases) & set(body.group_by):
            raise DataProblemError("DUPLICATE_RESULT_FIELD")
        result_fields = (
            body.group_by + aliases
            if body.aggregates
            else body.fields or list(available)
        )
        if set(x.field for x in body.sort) - set(result_fields):
            raise DataProblemError("INVALID_SORT_FIELD")
        for aggregate in body.aggregates:
            if aggregate.function in {"sum", "avg"}:
                dtype = available[aggregate.field]["type"]
                if not dtype.startswith(("int", "uint", "float", "double", "decimal")):
                    raise DataProblemError("AGGREGATE_TYPE_INVALID", field=aggregate.field)
    else:
        metrics = c.execute(
            "SELECT * FROM metrics WHERE version_id=ANY(%s) AND name=%s ORDER BY version_id",
            (body.version_ids, body.metric_name),
        ).fetchall()
        if len(metrics) != len(body.version_ids):
            raise DataProblemError("METRIC_NOT_FOUND", 404)
        signatures = {
            (x["definition"], x["unit"], x["evaluation_scope"]) for x in metrics
        }
        if len(signatures) != 1:
            raise DataProblemError(
                "METRIC_INCOMPATIBLE",
                409,
                comparisons=[
                    {
                        k: x[k]
                        for k in (
                            "version_id",
                            "definition",
                            "unit",
                            "evaluation_scope",
                        )
                    }
                    for x in metrics
                ],
            )
        plan["metric_rows"] = metrics
        plan["postgres_version"] = c.execute("SHOW server_version").fetchone()[
            "server_version"
        ]
    oid = enqueue(c, project, "query", "query", plan, actor)
    c.execute(
        """INSERT INTO queries(id,project,owner_subject,idem,digest,plan)
        VALUES (%s,%s,%s,%s,%s,%s)""",
        (oid, project, actor["subject"], body.idempotency_key, hashed, Jsonb(plan)),
    )
    return {"query_id": oid, "operation_id": oid}


def get(c, project, qid):
    row = c.execute(
        """SELECT q.*,o.state,o.error,expires_at>now() AS result_available
        FROM queries q JOIN operations o ON o.id=q.id WHERE q.id=%s AND q.project=%s""",
        (qid, project),
    ).fetchone()
    if not row:
        raise DataProblemError("QUERY_NOT_FOUND", 404)
    return row


def get_owned(c, project, qid, actor):
    row = get(c, project, qid)
    if row["owner_subject"] != actor["subject"]:
        raise DataProblemError("QUERY_NOT_FOUND", 404)
    return row


def cancel(c, project, qid, actor):
    get_owned(c, project, qid, actor)
    op = c.execute(
        """UPDATE operations SET state='CANCELLED',updated_at=now()
        WHERE id=%s AND state IN ('QUEUED','RUNNING') RETURNING *""",
        (qid,),
    ).fetchone()
    if op:
        event(c, op)
    return {"query_id": qid, "state": get_owned(c, project, qid, actor)["state"]}
