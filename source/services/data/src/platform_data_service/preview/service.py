from psycopg.types.json import Jsonb

from .. import access
from ..adapters.database import digest, enqueue, event
from ..catalog.service import version
from ..errors import DataProblemError


EXPECTED_FORMAT = {
    "xlsx_table": "xlsx",
    "geopackage_layer": "geopackage",
}


def submit(c, project, body, actor, input_limit):
    c.execute(
        "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
        (project + ":preview:" + actor["subject"] + ":" + body.idempotency_key,),
    )
    hashed = digest(body.model_dump())
    old = c.execute(
        """SELECT id,digest FROM previews
        WHERE project=%s AND owner_subject=%s AND idem=%s""",
        (project, actor["subject"], body.idempotency_key),
    ).fetchone()
    if old:
        if old["digest"] != hashed:
            raise DataProblemError("IDEMPOTENCY_CONFLICT", 409)
        return {"preview_id": old["id"], "operation_id": old["id"]}

    access.require_version(c, project, body.version_id, actor)
    row = version(c, project, body.version_id)
    semantic = c.execute(
        "SELECT body FROM semantic_revisions WHERE id=%s AND version_id=%s",
        (body.semantic_id, body.version_id),
    ).fetchone()
    if not semantic:
        raise DataProblemError("SEMANTIC_VERSION_MISMATCH", 409)
    object_row = c.execute(
        "SELECT descriptor FROM objects WHERE id=%s AND version_id=%s",
        (body.object_id, body.version_id),
    ).fetchone()
    if not object_row:
        raise DataProblemError("OBJECT_NOT_FOUND", 404)
    obj = object_row["descriptor"]
    if obj.get("format") != EXPECTED_FORMAT[body.kind]:
        raise DataProblemError(
            "PREVIEW_FORMAT_MISMATCH",
            409,
            expected=EXPECTED_FORMAT[body.kind],
            actual=obj.get("format"),
        )
    if obj["size"] > input_limit:
        raise DataProblemError("PREVIEW_INPUT_TOO_LARGE", 413, limit=input_limit)

    plan = body.model_dump()
    plan["input"] = {
        "dataset_id": row["dataset_id"],
        "version_id": body.version_id,
        "semantic_id": body.semantic_id,
        "object": obj,
        "semantics": semantic["body"],
    }
    oid = enqueue(c, project, "preview", body.object_id, plan, actor)
    c.execute(
        """INSERT INTO previews(id,project,owner_subject,idem,digest,plan)
        VALUES (%s,%s,%s,%s,%s,%s)""",
        (oid, project, actor["subject"], body.idempotency_key, hashed, Jsonb(plan)),
    )
    return {"preview_id": oid, "operation_id": oid}


def get(c, project, preview_id):
    row = c.execute(
        """SELECT p.*,o.state,o.error,expires_at>now() AS result_available
        FROM previews p JOIN operations o ON o.id=p.id
        WHERE p.id=%s AND p.project=%s""",
        (preview_id, project),
    ).fetchone()
    if not row:
        raise DataProblemError("PREVIEW_NOT_FOUND", 404)
    return row


def get_owned(c, project, preview_id, actor):
    row = get(c, project, preview_id)
    if row["owner_subject"] != actor["subject"]:
        raise DataProblemError("PREVIEW_NOT_FOUND", 404)
    return row


def cancel(c, project, preview_id, actor):
    get_owned(c, project, preview_id, actor)
    op = c.execute(
        """UPDATE operations SET state='CANCELLED',updated_at=now()
        WHERE id=%s AND state IN ('QUEUED','RUNNING') RETURNING *""",
        (preview_id,),
    ).fetchone()
    if op:
        event(c, op)
    return {
        "preview_id": preview_id,
        "state": get_owned(c, project, preview_id, actor)["state"],
    }
