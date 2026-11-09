"""Owner, project-role and visibility authorization for Data assets."""

from __future__ import annotations

from platform_protocol_contracts import ResourcePolicy

from .errors import DataProblemError


INTERNAL_ACTOR = {
    "subject": "platform-data",
    "display_name": "Platform Data",
    "roles": ["admin"],
}


def _policy(connection, project: str, resource: dict) -> ResourcePolicy:
    return ResourcePolicy(resource["owner_subject"], resource["visibility"])


def dataset(connection, project: str, dataset_id: str, *, lock: bool = False) -> dict:
    row = connection.execute(
        """SELECT id,project,owner_subject,asset_kind,visibility
           FROM datasets WHERE id=%s AND project=%s"""
        + (" FOR UPDATE" if lock else ""),
        (dataset_id, project),
    ).fetchone()
    if row is None:
        raise DataProblemError("DATASET_NOT_FOUND", 404)
    return row


def version_resource(connection, project: str, version_id: str) -> dict:
    row = connection.execute(
        """SELECT dataset.id,dataset.project,dataset.owner_subject,
                  dataset.asset_kind,dataset.visibility
           FROM versions AS version
           JOIN datasets AS dataset ON dataset.id=version.dataset_id
           WHERE version.id=%s AND dataset.project=%s""",
        (version_id, project),
    ).fetchone()
    if row is None:
        raise DataProblemError("VERSION_NOT_FOUND", 404)
    return row


def require(
    connection,
    project: str,
    resource: dict,
    actor: dict,
    operation: str,
    *,
    hide: bool = True,
) -> dict:
    if not _policy(connection, project, resource).allows(
        subject=actor["subject"],
        roles=actor["roles"],
        operation=operation,
    ):
        if hide:
            code = "DATASET_NOT_FOUND" if resource["asset_kind"] == "DATASET" else "MODEL_NOT_FOUND"
            raise DataProblemError(code, 404)
        raise DataProblemError("RESOURCE_ACCESS_FORBIDDEN", 403)
    return resource


def require_dataset(
    connection,
    project: str,
    dataset_id: str,
    actor: dict | None,
    operation: str = "view",
    *,
    lock: bool = False,
    hide: bool = True,
) -> dict:
    resource = dataset(connection, project, dataset_id, lock=lock)
    return require(
        connection,
        project,
        resource,
        actor or INTERNAL_ACTOR,
        operation,
        hide=hide,
    )


def require_version(
    connection,
    project: str,
    version_id: str,
    actor: dict | None,
    operation: str = "view",
    *,
    hide: bool = True,
) -> dict:
    resource = version_resource(connection, project, version_id)
    return require(
        connection,
        project,
        resource,
        actor or INTERNAL_ACTOR,
        operation,
        hide=hide,
    )


def readable_predicate(alias: str, actor: dict | None) -> tuple[str, tuple]:
    """Return a parameterized SQL predicate for list and discovery projections."""

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


def get_access(connection, project: str, dataset_id: str, actor: dict) -> dict:
    resource = require_dataset(
        connection, project, dataset_id, actor, "manage", hide=False
    )
    return {
        "resource_kind": resource["asset_kind"],
        "resource_id": resource["id"],
        "owner_subject": resource["owner_subject"],
        "visibility": resource["visibility"],
    }


def replace_access(
    connection,
    project: str,
    dataset_id: str,
    *,
    visibility: str,
    actor: dict,
) -> dict:
    resource = require_dataset(
        connection,
        project,
        dataset_id,
        actor,
        "manage",
        lock=True,
        hide=False,
    )
    ResourcePolicy(resource["owner_subject"], visibility)
    connection.execute(
        "UPDATE datasets SET visibility=%s WHERE id=%s",
        (visibility, dataset_id),
    )
    return get_access(connection, project, dataset_id, actor)
