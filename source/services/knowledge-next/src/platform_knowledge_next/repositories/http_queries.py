from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from urllib.parse import quote

from ..database import Database, required
from .. import access, catalog
from ..errors import KnowledgeProblemError
from ..models import document_content, stored_document_content


def _page(rows, limit: int, cursor_field: str):
    page = rows[:limit]
    return {
        "items": page,
        "next_cursor": page[-1][cursor_field] if len(rows) > limit else None,
    }


def list_imports(database: Database, cursor: str, limit: int):
    with database.transaction() as connection:
        rows = connection.execute(
            """SELECT id AS import_id,state,attempt,data_attempts,retry_generation,next_retry_at,retry_deadline,
                      last_error,error,result,actor,created_at,updated_at
               FROM imports WHERE id>%s ORDER BY id LIMIT %s""",
            (cursor, limit + 1),
        ).fetchall()
    return _page(rows, limit, "import_id")


def list_operator_activity(database: Database, limit: int):
    """Return imports and lifecycle changes in one Operator-facing timeline."""

    with database.transaction() as connection:
        imports = connection.execute(
            """SELECT id,state,payload,actor,error,created_at,updated_at
               FROM imports ORDER BY updated_at DESC,id DESC LIMIT %s""",
            (limit,),
        ).fetchall()
        lifecycle = connection.execute(
            """SELECT l.version_id,l.revision,l.status,l.reason,l.actor,l.created_at,
                      v.document_id,v.body->>'title' AS title
               FROM lifecycle l
               JOIN versions v ON v.id=l.version_id
               ORDER BY l.created_at DESC,l.version_id DESC,l.revision DESC
               LIMIT %s""",
            (limit,),
        ).fetchall()
    items = []
    for row in imports:
        documents = row["payload"].get("documents", [])
        document_title = documents[0].get("title") if documents else None
        items.append(
            {
                "service": "knowledge",
                "operation_id": row["id"],
                "kind": "import",
                "state": row["state"],
                "title": document_title
                or f"知识包导入 · {len(documents)} 个知识条目",
                "subject": row["actor"].get("subject", "unknown"),
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
                "href": (
                    "/knowledge/?view=import&import="
                    f"{quote(row['id'], safe='')}"
                ),
                "details": {
                    "documentCount": len(documents),
                    "errorCode": (row["error"] or {}).get("code"),
                },
            }
        )
    for row in lifecycle:
        items.append(
            {
                "service": "knowledge",
                "operation_id": f"{row['version_id']}:{row['revision']}",
                "kind": "lifecycle",
                "state": row["status"],
                "title": row["title"] or row["document_id"],
                "subject": row["actor"].get("subject", "unknown"),
                "created_at": row["created_at"],
                "updated_at": row["created_at"],
                "href": (
                    "/knowledge/?view=catalog&document="
                    f"{quote(row['document_id'], safe='')}&version="
                    f"{quote(row['version_id'], safe='')}"
                ),
                "details": {
                    "documentId": row["document_id"],
                    "versionId": row["version_id"],
                    "revision": row["revision"],
                    "reason": row["reason"],
                },
            }
        )
    items.sort(
        key=lambda item: (item["updated_at"], item["operation_id"]), reverse=True
    )
    return {
        "items": items[:limit],
        "generated_at": datetime.now(timezone.utc),
    }


def get_import(database: Database, import_id: str):
    with database.transaction() as connection:
        return required(
            connection.execute(
                """SELECT id AS import_id,state,attempt,data_attempts,retry_generation,next_retry_at,retry_deadline,
                      last_error,error,result,actor,created_at,updated_at
               FROM imports WHERE id=%s""",
                (import_id,),
            ).fetchone()
        )


def preflight_import(database: Database, package):
    document_ids = [document.knowledge_id for document in package.documents]
    with database.transaction(snapshot=True) as connection:
        rows = connection.execute(
            """SELECT d.id,d.current_version,v.body
               FROM documents d JOIN versions v ON v.id=d.current_version
               WHERE d.id=ANY(%s)""",
            (document_ids,),
        ).fetchall()
    current = {row["id"]: row for row in rows}
    documents = []
    expected_versions_match = True
    has_bindings = False
    for document in package.documents:
        existing = current.get(document.knowledge_id)
        current_version = existing["current_version"] if existing else None
        expected_matches = current_version == document.expected_version
        expected_versions_match = expected_versions_match and expected_matches
        current_body = existing["body"] if existing else {}
        next_body = document_content(document)
        current_bindings = {
            tuple(sorted(binding.items()))
            for binding in current_body.get("bindings", [])
        }
        next_bindings = {
            tuple(sorted(binding.items()))
            for binding in [item.model_dump(mode="json") for item in document.bindings]
        }
        has_bindings = has_bindings or bool(next_bindings)
        documents.append(
            {
                "knowledge_id": document.knowledge_id,
                "title": document.title,
                "operation": "UPDATE" if existing else "CREATE",
                "current_version": current_version,
                "expected_version": document.expected_version,
                "expected_version_matches": expected_matches,
                "current_content_version": current_body.get("content_version"),
                "next_content_version": document.content_version,
                "content_version_changed": current_body.get("content_version")
                != document.content_version,
                "content_changed": (
                    stored_document_content(current_body) != next_body
                ),
                "source_count": len(document.sources),
                "next_review_after": min(
                    source.review_after for source in document.sources
                ),
                "binding_added": len(next_bindings - current_bindings),
                "binding_removed": len(current_bindings - next_bindings),
                "binding_unchanged": len(current_bindings & next_bindings),
            }
        )
    project_matches = package.project_id == database.settings.project
    return {
        "project_matches": project_matches,
        "can_submit": project_matches and expected_versions_match,
        "documents": documents,
        "checks": [
            {
                "name": "package_schema",
                "state": "PASSED",
                "detail": "知识包结构和引用关系有效",
            },
            {
                "name": "project_identity",
                "state": "PASSED" if project_matches else "FAILED",
                "detail": "项目身份一致"
                if project_matches
                else "知识包项目与当前环境不一致",
            },
            {
                "name": "expected_versions",
                "state": "PASSED" if expected_versions_match else "FAILED",
                "detail": "全部 expectedVersion 与当前目录一致"
                if expected_versions_match
                else "至少一个 expectedVersion 已过期",
            },
            {
                "name": "data_bindings",
                "state": "PENDING" if has_bindings else "PASSED",
                "detail": "提交后由 Data 服务逐项核验不可变身份"
                if has_bindings
                else "没有需要跨服务核验的 Data 绑定",
            },
            {
                "name": "embedding_index",
                "state": "PENDING",
                "detail": "提交后生成向量并原子切换检索索引",
            },
        ],
        "generated_at": datetime.now(timezone.utc),
    }


def _review_state(row) -> str:
    from datetime import date, timedelta

    today = date.today()
    if row["valid_through"] and row["valid_through"] < today:
        return "due"
    if row["review_after"] < today:
        return "due"
    if row["review_after"] <= today + timedelta(days=30):
        return "upcoming"
    return "current"


def _counts(counter: Counter) -> list[dict]:
    return [
        {"name": str(name), "count": count}
        for name, count in sorted(counter.items(), key=lambda item: str(item[0]))
    ]


def list_documents(
    database: Database,
    query: str,
    cursor: str,
    limit: int,
    *,
    status: str | None = None,
    review: str | None = None,
    source_kind: str | None = None,
    kind: str | None = None,
    bound: bool | None = None,
    sort: str = "id-asc",
    actor: dict | None = None,
):
    projection = """SELECT d.id AS document_id,d.current_version,
               v.body->>'title' AS title,v.body->>'summary' AS summary,
               v.body->>'kind' AS kind,v.body->>'language' AS language,
               v.created_at,GREATEST(v.created_at,l.created_at) AS modified_at,
               l.status,l.review_after,l.valid_through,
               ARRAY(SELECT DISTINCT source->>'kind'
                     FROM jsonb_array_elements(v.body->'sources') source
                     ORDER BY source->>'kind') AS source_kinds,
               jsonb_array_length(v.body->'sources') AS source_count,
               jsonb_array_length(v.body->'bindings') AS binding_count
               FROM documents d JOIN versions v ON v.id=d.current_version
               JOIN LATERAL (SELECT status,review_after,valid_through,created_at
                             FROM lifecycle WHERE version_id=v.id
                             ORDER BY revision DESC LIMIT 1) l ON true"""
    query_where = """(strpos(lower(v.body->>'title'),lower(%s))>0
                   OR strpos(lower(v.body->>'summary'),lower(%s))>0
                   OR strpos(lower(d.id),lower(%s))>0)"""
    readable, access_parameters = access.readable_predicate("d", actor)
    filters = [query_where, "(" + readable + ")"]
    parameters: list = [query, query, query, *access_parameters]
    if status:
        filters.append("l.status=%s")
        parameters.append(status)
    if review == "due":
        filters.append(
            "(l.review_after<CURRENT_DATE OR (l.valid_through IS NOT NULL AND l.valid_through<CURRENT_DATE))"
        )
    elif review == "upcoming":
        filters.append(
            "l.review_after BETWEEN CURRENT_DATE AND CURRENT_DATE + INTERVAL '30 days' AND (l.valid_through IS NULL OR l.valid_through>=CURRENT_DATE)"
        )
    elif review == "current":
        filters.append(
            "l.review_after>CURRENT_DATE + INTERVAL '30 days' AND (l.valid_through IS NULL OR l.valid_through>=CURRENT_DATE)"
        )
    if source_kind:
        filters.append(
            "EXISTS (SELECT 1 FROM jsonb_array_elements(v.body->'sources') source WHERE source->>'kind'=%s)"
        )
        parameters.append(source_kind)
    if kind:
        filters.append("v.body->>'kind'=%s")
        parameters.append(kind)
    if bound is not None:
        filters.append(
            "jsonb_array_length(v.body->'bindings') " + (">0" if bound else "=0")
        )
    filtered_where = " AND ".join(filters)

    # Facets intentionally follow the text query but not the currently selected
    # facets, so an Operator can always see which alternative filters exist.
    facet_sql = projection + " WHERE " + query_where + " AND (" + readable + ")"
    facet_parameters = (query, query, query, *access_parameters)
    sort_sql = {
        "id-asc": "document_id ASC",
        "title-asc": "lower(title) ASC,document_id ASC",
        "title-desc": "lower(title) DESC,document_id ASC",
        "modified-desc": "modified_at DESC,document_id ASC",
        "review-asc": "review_after ASC,document_id ASC",
    }[sort]
    page_parameters = list(parameters)
    offset = 0
    page_where = filtered_where
    if sort == "id-asc" and cursor:
        page_where += " AND d.id>%s"
        page_parameters.append(cursor)
    elif cursor:
        try:
            offset = int(cursor.removeprefix("offset:"))
        except ValueError:
            offset = 0
    with database.transaction() as connection:
        rows = connection.execute(
            projection
            + " WHERE "
            + page_where
            + " ORDER BY "
            + sort_sql
            + " LIMIT %s OFFSET %s",
            (*page_parameters, limit + 1, offset),
        ).fetchall()
        total = connection.execute(
            "SELECT count(*) AS count FROM ("
            + projection
            + " WHERE "
            + filtered_where
            + ") matches",
            parameters,
        ).fetchone()["count"]
        facet_rows = connection.execute(facet_sql, facet_parameters).fetchall()

    page = rows[:limit]
    for row in page:
        row["review_state"] = _review_state(row)
        row["has_data_binding"] = row["binding_count"] > 0
    statuses = Counter(row["status"] for row in facet_rows)
    review_states = Counter(_review_state(row) for row in facet_rows)
    source_kinds = Counter(kind for row in facet_rows for kind in row["source_kinds"])
    knowledge_kinds = Counter(row["kind"] for row in facet_rows)
    binding_states = Counter(
        "bound" if row["binding_count"] else "general" for row in facet_rows
    )
    next_cursor = None
    if len(rows) > limit:
        next_cursor = (
            page[-1]["document_id"] if sort == "id-asc" else f"offset:{offset + limit}"
        )
    return {
        "items": page,
        "next_cursor": next_cursor,
        "total_matches": total,
        "facets": {
            "statuses": _counts(statuses),
            "review_states": _counts(review_states),
            "source_kinds": _counts(source_kinds),
            "knowledge_kinds": _counts(knowledge_kinds),
            "binding_states": _counts(binding_states),
        },
    }


def list_versions(
    database: Database,
    document_id: str,
    cursor: str,
    limit: int,
    actor: dict | None = None,
):
    with database.transaction() as connection:
        access.require_document(connection, document_id, actor)
        rows = connection.execute(
            """SELECT id AS version_id,checksum,created_at FROM versions
               WHERE document_id=%s AND id>%s ORDER BY id LIMIT %s""",
            (document_id, cursor, limit + 1),
        ).fetchall()
    return _page(rows, limit, "version_id")


def get_version(database: Database, version_id: str, actor: dict | None = None):
    with database.transaction() as connection:
        access.require_version(connection, version_id, actor)
        return catalog.version(connection, version_id)


def lifecycle_history(database: Database, version_id: str, actor: dict | None = None):
    with database.transaction() as connection:
        access.require_version(connection, version_id, actor)
        catalog.version(connection, version_id)
        return connection.execute(
            "SELECT * FROM lifecycle WHERE version_id=%s ORDER BY revision",
            (version_id,),
        ).fetchall()


def get_receipt(database: Database, receipt_id: str, actor: dict | None = None):
    actor = actor or access.INTERNAL_ACTOR
    with database.transaction() as connection:
        row = connection.execute(
            """SELECT id AS receipt_id,request_hash,kind,body,created_at,owner_subject
               FROM receipts WHERE id=%s""",
            (receipt_id,),
        ).fetchone()
        if row is None or (
            row["owner_subject"] != actor["subject"]
            and "admin" not in actor["roles"]
        ):
            raise KnowledgeProblemError("NOT_FOUND", 404)
        row.pop("owner_subject")
        return row
