from .discovery import project_discovery
from .. import access
from ..errors import DataProblemError


def version(c, project, version_id):
    row = c.execute(
        """SELECT v.*,d.title,d.tags,d.project,d.current_version FROM versions v
        JOIN datasets d ON d.id=v.dataset_id WHERE v.id=%s AND d.project=%s""",
        (version_id, project),
    ).fetchone()
    if not row:
        raise DataProblemError("VERSION_NOT_FOUND", 404)
    return row


def describe(c, project, version_id, actor=None):
    access.require_version(c, project, version_id, actor)
    row = version(c, project, version_id)
    sem = c.execute(
        "SELECT body FROM semantic_revisions WHERE id=%s", (row["semantic_id"],)
    ).fetchone()
    objects = c.execute(
        "SELECT id,descriptor FROM objects WHERE version_id=%s ORDER BY id",
        (version_id,),
    ).fetchall()
    inputs = c.execute(
        "SELECT input_version_id FROM lineage WHERE version_id=%s ORDER BY input_version_id",
        (version_id,),
    ).fetchall()
    return {
        **row,
        "semantics": sem["body"],
        "objects": [x["descriptor"] for x in objects],
        "input_versions": [x["input_version_id"] for x in inputs],
    }


def list_datasets(
    c, project, q="", tag=None, format=None, after="", limit=20, actor=None
):
    readable, access_parameters = access.readable_predicate("d", actor)
    rows = c.execute(
        """SELECT d.id,d.project,d.title,d.tags,d.current_version,d.created_at,
        s.body AS semantics,
        (SELECT jsonb_agg(DISTINCT o.descriptor->>'format')
         FROM objects o WHERE o.version_id=v.id) AS formats
        FROM datasets d
        JOIN versions v ON v.id=d.current_version
        JOIN semantic_revisions s ON s.id=v.semantic_id
        WHERE d.project=%s AND ("""
        + readable
        + """) AND d.id>%s AND
        (%s='' OR strpos(lower(d.title || ' ' || d.tags::text || ' ' || s.body::text),lower(%s))>0)
        AND (%s::text IS NULL OR d.tags ? %s)
        AND (%s::text IS NULL OR EXISTS (
          SELECT 1 FROM objects o WHERE o.version_id=v.id
          AND o.descriptor->>'format'=%s))
        ORDER BY d.id LIMIT %s""",
        (
            project,
            *access_parameters,
            after,
            q,
            q,
            tag,
            tag,
            format,
            format,
            limit + 1,
        ),
    ).fetchall()
    return {
        "items": rows[:limit],
        "next_cursor": rows[limit - 1]["id"] if len(rows) > limit else None,
        "match_basis": "case_insensitive_literal_metadata_substring",
    }


def inventory(c, project, actor=None):
    readable, access_parameters = access.readable_predicate("d", actor)
    summary = c.execute(
        """SELECT count(*) AS dataset_count,
        count(*) FILTER (WHERE jsonb_array_length(d.tags)>0) AS tagged_dataset_count,
        count(*) FILTER (WHERE s.body->>'description'<>'' OR s.body->'fields'<>'{}'::jsonb)
          AS described_dataset_count
        FROM datasets d JOIN versions v ON v.id=d.current_version
        JOIN semantic_revisions s ON s.id=v.semantic_id
        WHERE d.project=%s AND ("""
        + readable
        + ")",
        (project, *access_parameters),
    ).fetchone()
    formats = c.execute(
        """SELECT o.descriptor->>'format' AS name,count(DISTINCT d.id) AS count
        FROM datasets d JOIN versions v ON v.id=d.current_version
        JOIN objects o ON o.version_id=v.id
        WHERE d.project=%s AND ("""
        + readable
        + ") GROUP BY o.descriptor->>'format' ORDER BY name",
        (project, *access_parameters),
    ).fetchall()
    tags = c.execute(
        """SELECT tag AS name,count(*) AS count FROM datasets d,
        LATERAL jsonb_array_elements_text(d.tags) AS tag
        WHERE d.project=%s AND ("""
        + readable
        + ") GROUP BY tag ORDER BY count DESC,tag LIMIT 50",
        (project, *access_parameters),
    ).fetchall()
    return {
        **summary,
        "formats": formats,
        "tags": tags,
        "projection_basis": "current_data_catalog",
    }


def discover_datasets(
    c,
    project,
    *,
    query="",
    tags=(),
    formats=(),
    field_terms=(),
    units=(),
    after=None,
    limit=20,
    actor=None,
):
    """Read current catalog rows and apply the deterministic discovery projection."""
    readable, access_parameters = access.readable_predicate("d", actor)
    rows = c.execute(
        """SELECT d.id,d.title,d.tags,d.current_version,s.id AS semantic_id,
        s.body AS semantics,v.descriptor,
        (SELECT jsonb_agg(DISTINCT o.descriptor->>'format')
         FROM objects o WHERE o.version_id=v.id) AS formats
        FROM datasets d JOIN versions v ON v.id=d.current_version
        JOIN semantic_revisions s ON s.id=v.semantic_id
        WHERE d.project=%s AND ("""
        + readable
        + ") ORDER BY d.id",
        (project, *access_parameters),
    ).fetchall()
    return project_discovery(
        rows,
        query=query,
        tags=tags,
        formats=formats,
        field_terms=field_terms,
        units=units,
        after=after,
        limit=limit,
    )
