from collections.abc import Callable
from typing import Annotated, Literal

from fastapi import Depends, FastAPI, Path, Query, Request

from .. import access, catalog
from ..access import require_operator
from ..database import Database
from ..http_models import (
    DocumentPage,
    LifecycleBatchPreflightRequest,
    LifecycleBatchPreflightResponse,
    LifecycleBatchRequest,
    LifecycleBatchResponse,
    LifecyclePage,
    LifecycleUpdateRequest,
    ResourceAccessResponse,
    ResourceAccessUpdateRequest,
    VersionPage,
    VersionResponse,
)
from ..repositories import http_queries
from ..wire import to_wire, with_semantic_id

PREFIX = "/knowledge/v3"


def register_document_routes(
    app: FastAPI, db: Database, writable: Callable[[], None]
) -> None:
    @app.get(
        PREFIX + "/documents",
        response_model=DocumentPage,
        operation_id="knowledge_list_documents",
    )
    def documents(
        request: Request,
        query: str = Query("", max_length=512),
        cursor: str = "",
        limit: int = Query(20, ge=1, le=100),
        status: Literal["ACTIVE", "WITHDRAWN"] | None = None,
        review: Literal["due", "upcoming", "current"] | None = None,
        source_kind: str | None = Query(None, alias="sourceKind", max_length=80),
        kind: Literal["knowledge", "definition", "method", "case", "rule"]
        | None = None,
        bound: bool | None = None,
        sort: Literal[
            "id-asc", "title-asc", "title-desc", "modified-desc", "review-asc"
        ] = "id-asc",
    ):
        return to_wire(
            http_queries.list_documents(
                db,
                query,
                cursor,
                limit,
                status=status,
                review=review,
                source_kind=source_kind,
                kind=kind,
                bound=bound,
                sort=sort,
                actor=request.state.actor,
            )
        )

    @app.get(
        PREFIX + "/documents/{documentId}/versions",
        response_model=VersionPage,
        operation_id="knowledge_list_document_versions",
    )
    def versions(
        request: Request,
        document_id: Annotated[str, Path(alias="documentId")],
        cursor: str = "",
        limit: int = Query(20, ge=1, le=100),
    ):
        return to_wire(
            http_queries.list_versions(
                db, document_id, cursor, limit, request.state.actor
            )
        )

    @app.get(
        PREFIX + "/documents/{documentId}/access",
        response_model=ResourceAccessResponse,
        operation_id="knowledge_get_resource_access",
    )
    def get_resource_access(
        request: Request,
        document_id: Annotated[str, Path(alias="documentId")],
    ):
        with db.transaction() as connection:
            return to_wire(
                access.get_access(connection, document_id, request.state.actor)
            )

    @app.put(
        PREFIX + "/documents/{documentId}/access",
        response_model=ResourceAccessResponse,
        operation_id="knowledge_replace_resource_access",
    )
    def replace_resource_access(
        request: Request,
        body: ResourceAccessUpdateRequest,
        document_id: Annotated[str, Path(alias="documentId")],
    ):
        writable()
        with db.transaction() as connection:
            return to_wire(
                access.replace_access(
                    connection,
                    document_id,
                    visibility=body.visibility,
                    actor=request.state.actor,
                )
            )

    @app.get(
        PREFIX + "/versions/{versionId}",
        response_model=VersionResponse,
        operation_id="knowledge_get_version",
    )
    def get_version(
        request: Request,
        version_id: Annotated[str, Path(alias="versionId")],
    ):
        return to_wire(
            with_semantic_id(
                http_queries.get_version(db, version_id, request.state.actor),
                "version_id",
            )
        )

    @app.get(
        PREFIX + "/versions/{versionId}/lifecycle",
        response_model=LifecyclePage,
        operation_id="knowledge_get_lifecycle_history",
    )
    def history(
        request: Request,
        version_id: Annotated[str, Path(alias="versionId")],
    ):
        items = http_queries.lifecycle_history(db, version_id, request.state.actor)
        return to_wire({"items": items, "next_cursor": None})

    @app.post(
        PREFIX + "/lifecycle/batch/preflight",
        response_model=LifecycleBatchPreflightResponse,
        operation_id="knowledge_preflight_lifecycle_batch",
        dependencies=[Depends(require_operator)],
    )
    def lifecycle_batch_preflight(body: LifecycleBatchPreflightRequest):
        return to_wire(catalog.preflight_lifecycle_batch(db, body))

    @app.post(
        PREFIX + "/lifecycle/batch",
        response_model=LifecycleBatchResponse,
        operation_id="knowledge_update_lifecycle_batch",
        dependencies=[Depends(require_operator)],
    )
    def lifecycle_batch(
        body: LifecycleBatchRequest,
        request: Request,
    ):
        writable()
        result = catalog.update_lifecycle_batch(db, body, request.state.actor)
        return to_wire(
            {
                **result,
                "items": [
                    with_semantic_id(item, "version_id")
                    for item in result["items"]
                ],
            }
        )

    @app.post(
        PREFIX + "/versions/{versionId}/lifecycle",
        response_model=VersionResponse,
        operation_id="knowledge_update_lifecycle",
        dependencies=[Depends(require_operator)],
    )
    def lifecycle(
        version_id: Annotated[str, Path(alias="versionId")],
        body: LifecycleUpdateRequest,
        request: Request,
    ):
        writable()
        return to_wire(
            with_semantic_id(
                catalog.update_lifecycle(db, version_id, body, request.state.actor),
                "version_id",
            )
        )
