from psycopg.types.json import Jsonb
from .. import access, openlineage
from ..adapters.database import digest, enqueue, identity, event
from ..catalog.service import version
from ..errors import DataProblemError


def get(c, project, pid, lock=False):
    row = c.execute(
        "SELECT * FROM publications WHERE id=%s AND project=%s"
        + (" FOR UPDATE" if lock else ""),
        (pid, project),
    ).fetchone()
    if not row:
        raise DataProblemError("PUBLICATION_NOT_FOUND", 404)
    return row


def _subject(actor):
    return actor["subject"]


def get_owned(c, project, pid, actor, lock=False):
    row = get(c, project, pid, lock)
    if row["owner_subject"] != _subject(actor):
        raise DataProblemError("PUBLICATION_NOT_FOUND", 404)
    if (
        row["publication_kind"] == "MANAGED"
        and "admin" not in actor["roles"]
    ):
        raise DataProblemError("ROLE_FORBIDDEN", 403)
    return row


def public(c, project, pid, actor):
    row = get_owned(c, project, pid, actor)
    row.pop("prepared")
    row["events"] = c.execute(
        "SELECT seq,state,created_at FROM publication_events WHERE publication_id=%s ORDER BY seq",
        (pid,),
    ).fetchall()
    row["uploads"] = [
        x["name"]
        for x in c.execute(
            "SELECT name FROM uploads WHERE publication_id=%s ORDER BY name", (pid,)
        )
    ]
    row["operations"] = c.execute(
        "SELECT id,state,error FROM operations WHERE target=%s ORDER BY created_at",
        (pid,),
    ).fetchall()
    return row


def _publication_kind(body, actor):
    if "admin" in actor["roles"]:
        return "MANAGED"
    output = body.outputs[0] if len(body.outputs) == 1 else None
    semantics = output.semantics if output else None
    if (
        output is None
        or output.name != "file"
        or output.dataset_id is not None
        or output.expected_version is not None
        or output.policy not in {"FILE_ONLY", "FILE_QUERYABLE"}
        or output.tags
        or output.conversion.model_dump() != {"types": {}, "null_values": []}
        or semantics is None
        or semantics.source != "web_upload"
        or semantics.fields
        or semantics.grain is not None
        or semantics.time_range is not None
        or semantics.timezone is not None
        or semantics.crs is not None
        or semantics.vertical_datum is not None
        or semantics.limitations
        or body.input_versions
        or body.provenance != {"client": "platform-web-v12"}
    ):
        raise DataProblemError("ROLE_FORBIDDEN", 403)
    return "USER_UPLOAD"


def begin(c, project, body, actor):
    manifest = body.model_dump()
    hashed = digest(manifest)
    owner = _subject(actor)
    kind = _publication_kind(body, actor)
    row = c.execute(
        """INSERT INTO publications
        (id,project,owner_subject,publication_kind,idem,digest,manifest,state)
        VALUES (%s,%s,%s,%s,%s,%s,%s,'OPEN')
        ON CONFLICT(project,owner_subject,idem) DO NOTHING RETURNING *""",
        (identity(), project, owner, kind, body.idempotency_key, hashed, Jsonb(manifest)),
    ).fetchone()
    if row is None:
        row = c.execute(
            """SELECT * FROM publications
            WHERE project=%s AND owner_subject=%s AND idem=%s""",
            (project, owner, body.idempotency_key),
        ).fetchone()
        if row["digest"] != hashed or row["publication_kind"] != kind:
            raise DataProblemError("IDEMPOTENCY_CONFLICT", 409)
    for vid in body.input_versions:
        access.require_version(c, project, vid, actor)
        version(c, project, vid)
    for out in body.outputs:
        if out.dataset_id:
            access.require_dataset(
                c, project, out.dataset_id, actor, "modify", hide=False
            )
            existing = version(c, project, out.expected_version)
            if existing["dataset_id"] != out.dataset_id:
                raise DataProblemError("DATASET_VERSION_MISMATCH", 409)
    openlineage.record_start(c, project, row)
    return public(c, project, row["id"], actor)


def attach(c, project, pid, name, obj, actor):
    row = get_owned(c, project, pid, actor, True)
    if row["state"] != "OPEN":
        raise DataProblemError("PUBLICATION_FROZEN", 409)
    if name not in {x["name"] for x in row["manifest"]["outputs"]}:
        raise DataProblemError("UNKNOWN_OUTPUT", 404)
    old = c.execute(
        "SELECT object FROM uploads WHERE publication_id=%s AND name=%s", (pid, name)
    ).fetchone()
    if old:
        if (old["object"]["checksum"], old["object"]["size"]) != (
            obj["checksum"],
            obj["size"],
        ):
            raise DataProblemError("UPLOAD_CONFLICT", 409)
        return old["object"]
    c.execute("INSERT INTO uploads VALUES (%s,%s,%s)", (pid, name, Jsonb(obj)))
    return obj


def prepare(c, project, pid, actor):
    row = get_owned(c, project, pid, actor, True)
    old = c.execute(
        "SELECT id FROM operations WHERE target=%s AND kind='prepare'", (pid,)
    ).fetchone()
    if old:
        return {"operation_id": old["id"]}
    if row["state"] != "OPEN":
        raise DataProblemError("PUBLICATION_STATE_CONFLICT", 409)
    uploads = {
        x["name"]: x["object"]
        for x in c.execute("SELECT * FROM uploads WHERE publication_id=%s", (pid,))
    }
    if set(uploads) != {x["name"] for x in row["manifest"]["outputs"]}:
        raise DataProblemError("OUTPUTS_INCOMPLETE", 409)
    op = enqueue(
        c,
        project,
        "prepare",
        pid,
        {"manifest": row["manifest"], "uploads": uploads},
        actor,
    )
    c.execute("UPDATE publications SET state='PREPARING' WHERE id=%s", (pid,))
    return {"operation_id": op}


def abort(c, project, pid, actor):
    # Lock publication before its operation, consistently with Worker finalization.
    row = get_owned(c, project, pid, actor, True)
    if row["state"] == "PUBLISHED":
        raise DataProblemError("ALREADY_PUBLISHED", 409)
    c.execute("UPDATE publications SET state='ABORTED' WHERE id=%s", (pid,))
    openlineage.record_fail(c, project, row, "PUBLICATION_ABORTED")
    ops = c.execute(
        """UPDATE operations SET state='CANCELLED',updated_at=now()
        WHERE target=%s AND state IN ('QUEUED','RUNNING') RETURNING *""",
        (pid,),
    ).fetchall()
    for op in ops:
        event(c, op)
    return {"id": pid, "state": "ABORTED"}


def commit(c, project, pid, actor):
    row = get_owned(c, project, pid, actor, True)
    if row["state"] == "PUBLISHED":
        return row["receipt"]
    if row["state"] != "READY":
        raise DataProblemError("PUBLICATION_NOT_READY", 409)
    result = []
    # Consistent lock order for publications targeting multiple existing datasets.
    targets = sorted(
        x["dataset_id"] for x in row["manifest"]["outputs"] if x["dataset_id"]
    )
    for did in targets:
        c.execute("SELECT id FROM datasets WHERE id=%s FOR UPDATE", (did,))
        access.require_dataset(c, project, did, actor, "modify", hide=False)
    for input_version in row["manifest"]["input_versions"]:
        access.require_version(c, project, input_version, actor)
    for item in row["prepared"]:
        output = item["output"]
        did, vid, sid = output["dataset_id"] or identity(), identity(), identity()
        if output["dataset_id"]:
            current = c.execute(
                "SELECT current_version FROM datasets WHERE id=%s AND project=%s",
                (did, project),
            ).fetchone()
            if not current or current["current_version"] != output["expected_version"]:
                raise DataProblemError("DATASET_VERSION_CONFLICT", 409, dataset_id=did)
        else:
            c.execute(
                """INSERT INTO datasets(
                     id,project,title,tags,owner_subject,asset_kind
                   ) VALUES (%s,%s,%s,%s,%s,%s)""",
                (
                    did,
                    project,
                    output["title"],
                    Jsonb(output["tags"]),
                    row["owner_subject"],
                    "MODEL"
                    if output["format"] == "pytorch-state-dict"
                    else "DATASET",
                ),
            )
        c.execute(
            "INSERT INTO versions(id,dataset_id,publication_id,descriptor,semantic_id) VALUES (%s,%s,%s,%s,%s)",
            (vid, did, pid, Jsonb(item["descriptor"]), sid),
        )
        c.execute(
            "INSERT INTO semantic_revisions(id,version_id,body,actor) VALUES (%s,%s,%s,%s)",
            (sid, vid, Jsonb(output["semantics"]), Jsonb(actor)),
        )
        for obj in item["objects"]:
            c.execute(
                "INSERT INTO objects VALUES (%s,%s,%s)", (obj["id"], vid, Jsonb(obj))
            )
        for metric in item["metrics"]:
            c.execute(
                "INSERT INTO metrics VALUES (%s,%s,%s,%s,%s,%s)",
                (
                    vid,
                    metric["name"],
                    metric["definition"],
                    metric["unit"],
                    metric["evaluation_scope"],
                    metric["value"],
                ),
            )
        for input_id in row["manifest"]["input_versions"]:
            c.execute("INSERT INTO lineage VALUES (%s,%s)", (vid, input_id))
        c.execute(
            "UPDATE datasets SET current_version=%s,title=%s,tags=%s WHERE id=%s",
            (vid, output["title"], Jsonb(output["tags"]), did),
        )
        result.append(
            {
                "name": output["name"],
                "dataset_id": did,
                "version_id": vid,
                "semantic_id": sid,
            }
        )
    receipt = {"publication_id": pid, "state": "PUBLISHED", "outputs": result}
    c.execute(
        "UPDATE publications SET state='PUBLISHED',receipt=%s WHERE id=%s",
        (Jsonb(receipt), pid),
    )
    openlineage.record_complete(c, project, row)
    return receipt
