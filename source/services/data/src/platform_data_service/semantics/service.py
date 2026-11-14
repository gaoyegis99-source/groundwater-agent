from psycopg.types.json import Jsonb
from .. import access
from ..adapters.database import identity
from ..catalog.service import version
from ..errors import DataProblemError


def check_semantics(body, fields):
    unknown = set(body.fields) - {x["id"] for x in fields}
    if unknown:
        raise DataProblemError("UNKNOWN_FIELD", fields=sorted(unknown))
    if body.spatial_extent is not None and body.crs is not None and (
        body.spatial_extent.crs.strip().casefold() != body.crs.strip().casefold()
    ):
        raise DataProblemError("SPATIAL_EXTENT_CRS_MISMATCH", 422)


def add_semantics(c, project, version_id, body, expected, actor):
    access.require_version(c, project, version_id, actor, "modify", hide=False)
    row = version(c, project, version_id)
    check_semantics(body, row["descriptor"].get("fields", []))
    sid = identity()
    c.execute(
        "INSERT INTO semantic_revisions(id,version_id,body,actor) VALUES (%s,%s,%s,%s)",
        (sid, version_id, Jsonb(body.model_dump()), Jsonb(actor)),
    )
    changed = c.execute(
        "UPDATE versions SET semantic_id=%s WHERE id=%s AND semantic_id=%s",
        (sid, version_id, expected),
    ).rowcount
    if not changed:
        raise DataProblemError("SEMANTIC_VERSION_CONFLICT", 409)
    return {"semantic_id": sid, "version_id": version_id}
