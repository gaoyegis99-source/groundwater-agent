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
    quantity_kinds=(),
    time_window=None,
    spatial_extent=None,
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
    result = project_discovery(
        rows,
        query=query,
        tags=tags,
        formats=formats,
        field_terms=field_terms,
        units=units,
        quantity_kinds=quantity_kinds,
        time_window=time_window,
        spatial_extent=spatial_extent,
        after=after,
        limit=limit,
    )
    items = result["items"]
    if not items:
        return result
    quality = c.execute(
        """SELECT r.dataset_id,r.version_id,r.semantic_id,r.id AS quality_run_id,
                  r.suite_version_id,r.status
           FROM unnest(%s::text[],%s::text[],%s::text[]) AS selected(dataset_id,version_id,semantic_id)
           JOIN LATERAL (
               SELECT revision,enabled FROM quality_bindings b
               WHERE b.project=%s AND b.dataset_id=selected.dataset_id
               ORDER BY revision DESC LIMIT 1
           ) b ON b.enabled
           JOIN LATERAL (
               SELECT r.* FROM quality_runs r
               WHERE r.project=%s AND r.dataset_id=selected.dataset_id
                   AND r.version_id=selected.version_id AND r.semantic_id=selected.semantic_id
                   AND r.binding_revision=b.revision
               ORDER BY r.created_at DESC,r.id DESC LIMIT 1
           ) r ON true""",
        (
            [item["id"] for item in items],
            [item["current_version"] for item in items],
            [item["semantic_id"] for item in items],
            project,
            project,
        ),
    ).fetchall()
    by_dataset = {row["dataset_id"]: row for row in quality}
    for item in items:
        run = by_dataset.get(item["id"])
        if run:
            item["quality"] = {
                key: run[key] for key in ("quality_run_id", "suite_version_id", "status")
            }
    return result
