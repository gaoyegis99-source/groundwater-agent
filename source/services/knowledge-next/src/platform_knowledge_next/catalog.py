from datetime import date, datetime, timezone

from psycopg.types.json import Jsonb

from . import access
from .access import INTERNAL_ACTOR
from .database import required
from .encoding import input_hash, normalize, tokens
from .errors import KnowledgeProblemError
from .models import Document


def version(c, version_id):
    return required(
        c.execute(
            """SELECT v.*,l.revision AS lifecycle_revision,l.status,l.valid_through,
        l.review_after,l.actor AS lifecycle_actor
        FROM versions v JOIN LATERAL (SELECT * FROM lifecycle WHERE version_id=v.id
        ORDER BY revision DESC LIMIT 1) l ON true WHERE v.id=%s""",
            (version_id,),
        ).fetchone()
    )


def lifecycle_from_sources(document):
    expires = [s.valid_through for s in document.sources if s.valid_through]
    return {
        "status": "WITHDRAWN"
        if any(s.lifecycle_status == "WITHDRAWN" for s in document.sources)
        else "ACTIVE",
        "valid_through": min(expires) if expires else None,
        "review_after": min(s.review_after for s in document.sources),
    }


def project_search(c, version_id, document, lifecycle):
    c.execute(
        "DELETE FROM search_entries WHERE document_id=%s", (document.knowledge_id,)
    )
    if lifecycle["status"] != "ACTIVE":
        return
    keys = sorted(
        {
            normalize(k)
            for k in [
                document.knowledge_id,
                document.title,
                *document.aliases,
                document.model_id,
            ]
            if k
        }
    )
    for i, unit in enumerate(document.units):
        c.execute(
            """INSERT INTO search_entries
            (version_id,unit_id,document_id,ordinal,lexical,embedding,kind,keys,bindings,valid_through)
            SELECT %s,%s,%s,%s,
            setweight(to_tsvector('simple',%s),'A') || setweight(to_tsvector('simple',%s),'B') ||
            setweight(to_tsvector('simple',%s),'D'),embedding,%s,%s,%s,%s FROM embeddings WHERE input_hash=%s""",
            (
                version_id,
                unit.unit_id,
                document.knowledge_id,
                i,
                " ".join(tokens(document.title + " " + " ".join(document.aliases))),
                " ".join(tokens(document.summary)),
                " ".join(tokens(unit.content)),
                document.kind,
                keys,
                Jsonb([b.model_dump(mode="json") for b in document.bindings]),
                lifecycle["valid_through"],
                input_hash(document, unit),
            ),
        )


def _append_lifecycle(c, v, change, actor):
    c.execute(
        """INSERT INTO lifecycle
        (version_id,revision,status,valid_through,review_after,reason,actor)
        VALUES(%s,%s,%s,%s,%s,%s,%s)""",
        (
            v["id"],
            change.expected_revision + 1,
            change.status,
            change.valid_through,
            change.review_after,
            change.reason,
            Jsonb(actor),
        ),
    )
    current = c.execute(
        "SELECT current_version FROM documents WHERE id=%s", (v["document_id"],)
    ).fetchone()
    if current["current_version"] == v["id"]:
        project_search(
            c, v["id"], Document.model_validate(v["body"]), change.model_dump()
        )


def update_lifecycle(db, version_id, change, actor=None):
    actor = actor or INTERNAL_ACTOR
    with db.transaction() as c:
        c.execute("SELECT * FROM meta FOR UPDATE")
        access.require_version(c, version_id, actor, "manage", hide=False)
        v = version(c, version_id)
        if v["lifecycle_revision"] != change.expected_revision:
            raise KnowledgeProblemError("LIFECYCLE_CONFLICT", 409)
        _append_lifecycle(c, v, change, actor)
        c.execute("UPDATE meta SET revision=revision+1")
        return version(c, version_id)


def preflight_lifecycle_batch(db, request):
    document_ids = request.document_ids
    with db.transaction(snapshot=True) as c:
        rows = c.execute(
            """SELECT d.id AS document_id,d.current_version,v.body,
                      l.revision AS lifecycle_revision,l.status,l.valid_through,
                      l.review_after
               FROM documents d
               JOIN versions v ON v.id=d.current_version
               JOIN LATERAL (
                   SELECT revision,status,valid_through,review_after
                   FROM lifecycle WHERE version_id=v.id
                   ORDER BY revision DESC LIMIT 1
               ) l ON true
               WHERE d.id=ANY(%s)""",
            (document_ids,),
        ).fetchall()
    by_id = {row["document_id"]: row for row in rows}
    missing = [document_id for document_id in document_ids if document_id not in by_id]
    if missing:
        raise KnowledgeProblemError(
            "DOCUMENT_NOT_FOUND", 404, {"documentIds": missing}
        )
    items = []
    for document_id in document_ids:
        row = by_id[document_id]
        target = (request.status, request.valid_through, request.review_after)
        current = (row["status"], row["valid_through"], row["review_after"])
        items.append(
            {
                "document_id": document_id,
                "title": row["body"].get("title") or document_id,
                "version_id": row["current_version"],
                "current_revision": row["lifecycle_revision"],
                "current_status": row["status"],
                "target_status": request.status,
                "current_valid_through": row["valid_through"],
                "target_valid_through": request.valid_through,
                "current_review_after": row["review_after"],
                "target_review_after": request.review_after,
                "changed": current != target,
            }
        )
    return {
        "can_submit": True,
        "items": items,
        "generated_at": datetime.now(timezone.utc),
    }


def update_lifecycle_batch(db, request, actor=None):
    actor = actor or INTERNAL_ACTOR
    document_ids = [item.document_id for item in request.items]
    with db.transaction() as c:
        c.execute("SELECT * FROM meta FOR UPDATE")
        documents = c.execute(
            """SELECT id,current_version FROM documents
               WHERE id=ANY(%s) ORDER BY id FOR UPDATE""",
            (document_ids,),
        ).fetchall()
        current_versions = {row["id"]: row["current_version"] for row in documents}
        missing = [item.document_id for item in request.items if item.document_id not in current_versions]
        if missing:
            raise KnowledgeProblemError(
                "DOCUMENT_NOT_FOUND", 404, {"documentIds": missing}
            )

        validated = []
        for item in request.items:
            access.require_document(
                c, item.document_id, actor, "manage", hide=False
            )
            if current_versions[item.document_id] != item.version_id:
                raise KnowledgeProblemError(
                    "LIFECYCLE_CONFLICT",
                    409,
                    {"documentId": item.document_id, "reason": "CURRENT_VERSION_CHANGED"},
                )
            v = version(c, item.version_id)
            if (
                v["document_id"] != item.document_id
                or v["lifecycle_revision"] != item.expected_revision
            ):
                raise KnowledgeProblemError(
                    "LIFECYCLE_CONFLICT",
                    409,
                    {"documentId": item.document_id, "reason": "REVISION_CHANGED"},
                )
            validated.append((v, item))

        for v, item in validated:
            _append_lifecycle(c, v, item, actor)
        c.execute("UPDATE meta SET revision=revision+1")
        return {
            "items": [version(c, item.version_id) for item in request.items],
            "updated_at": datetime.now(timezone.utc),
        }


def status(v):
    if v["status"] == "WITHDRAWN":
        return "WITHDRAWN"
    if v["valid_through"] and v["valid_through"] < date.today():
        return "EXPIRED"
    return "OK"
