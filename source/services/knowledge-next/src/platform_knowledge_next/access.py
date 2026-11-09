from fastapi import Request
from platform_protocol_contracts import ResourcePolicy

from .errors import KnowledgeProblemError

INTERNAL_ACTOR = {
    "subject": "platform-knowledge-internal",
    "display_name": "Knowledge Internal Service",
    "roles": ["admin"],
}


def require_operator(request: Request) -> None:
    """Authorize management writes at the route that owns the operation."""
    actor = getattr(request.state, "actor", None)
    if not actor or "admin" not in actor["roles"]:
        raise KnowledgeProblemError("ROLE_FORBIDDEN", 403)


def document(connection, document_id: str, *, lock: bool = False) -> dict:
    row = connection.execute(
        """SELECT id,owner_subject,visibility FROM documents
           WHERE id=%s"""
        + (" FOR UPDATE" if lock else ""),
        (document_id,),
    ).fetchone()
    if row is None:
        raise KnowledgeProblemError("DOCUMENT_NOT_FOUND", 404)
    return row


def version_document(connection, version_id: str) -> dict:
    row = connection.execute(
        """SELECT document.id,document.owner_subject,document.visibility
           FROM versions AS version
           JOIN documents AS document ON document.id=version.document_id
           WHERE version.id=%s""",
        (version_id,),
    ).fetchone()
    if row is None:
        raise KnowledgeProblemError("NOT_FOUND", 404)
    return row


def require(
    connection,
    resource: dict,
    actor: dict | None,
    operation: str = "view",
    *,
    hide: bool = True,
) -> dict:
    actor = actor or INTERNAL_ACTOR
    policy = ResourcePolicy(resource["owner_subject"], resource["visibility"])
    if not policy.allows(
        subject=actor["subject"], roles=actor["roles"], operation=operation
    ):
        if hide:
            raise KnowledgeProblemError("NOT_FOUND", 404)
        raise KnowledgeProblemError("RESOURCE_ACCESS_FORBIDDEN", 403)
    return resource


def require_document(
    connection,
    document_id: str,
    actor: dict | None,
    operation: str = "view",
    *,
    lock: bool = False,
    hide: bool = True,
) -> dict:
    return require(
        connection,
        document(connection, document_id, lock=lock),
        actor,
        operation,
        hide=hide,
    )


def require_version(
    connection,
    version_id: str,
    actor: dict | None,
    operation: str = "view",
    *,
    hide: bool = True,
) -> dict:
    return require(
        connection,
        version_document(connection, version_id),
        actor,
        operation,
        hide=hide,
    )


def readable_predicate(alias: str, actor: dict | None) -> tuple[str, tuple]:
    actor = actor or INTERNAL_ACTOR
    if "admin" in actor["roles"]:
        return "TRUE", ()
    return (
        f"""(
          {alias}.owner_subject=%s
          OR {alias}.visibility='PROJECT'
        )""",
        (actor["subject"],),
    )


def search_predicate(actor: dict | None) -> tuple[str, tuple]:
    readable, parameters = readable_predicate("access_document", actor)
    return (
        """EXISTS (
          SELECT 1 FROM documents AS access_document
          WHERE access_document.id=search_entries.document_id AND ("""
        + readable
        + "))",
        parameters,
    )


def get_access(connection, document_id: str, actor: dict) -> dict:
    resource = require_document(
        connection, document_id, actor, "manage", hide=False
    )
    return {
        "resource_kind": "KNOWLEDGE",
        "resource_id": document_id,
        "owner_subject": resource["owner_subject"],
        "visibility": resource["visibility"],
    }


def replace_access(
    connection,
    document_id: str,
    *,
    visibility: str,
    actor: dict,
) -> dict:
    resource = require_document(
        connection,
        document_id,
        actor,
        "manage",
        lock=True,
        hide=False,
    )
    ResourcePolicy(resource["owner_subject"], visibility)
    connection.execute(
        "UPDATE documents SET visibility=%s WHERE id=%s",
        (visibility, document_id),
    )
    return get_access(connection, document_id, actor)
