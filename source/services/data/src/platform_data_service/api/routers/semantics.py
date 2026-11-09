from typing import Annotated

from fastapi import FastAPI, Path, Query, Request

from ...composition import Application
from ... import access
from ...errors import DataProblemError
from ...repositories import http_queries
from ...semantics import service as semantic_service
from ..models import SemanticUpdate
from ..responses import (
    SemanticRevisionPage,
    SemanticRevisionResponse,
    SemanticUpdateResponse,
)
from ..wire import page_with_semantic_ids, to_wire, with_semantic_id


def register_semantic_routes(app: FastAPI, settings, service: Application) -> None:
    @app.get(
        "/data/v3/versions/{versionId}/semantics",
        response_model=SemanticRevisionPage,
        operation_id="data_list_semantic_revisions",
    )
    def semantic_revisions(
        request: Request,
        version_id: Annotated[str, Path(alias="versionId")],
        cursor: str = "",
        limit: int = Query(default=20, ge=1, le=100),
    ):
        with service.db.transaction() as connection:
            access.require_version(
                connection, settings.project, version_id, request.state.actor
            )
        value = http_queries.list_semantic_revisions(
            service.db, settings.project, version_id, cursor, limit
        )
        return to_wire(page_with_semantic_ids(value, "semantic_id"))

    @app.get("/data/v3/versions/{versionId}/semantics/{semanticId}", response_model=SemanticRevisionResponse, operation_id="data_get_semantic_revision")
    def semantic_revision(
        request: Request,
        version_id: Annotated[str, Path(alias="versionId")],
        semantic_id: Annotated[str, Path(alias="semanticId")],
    ):
        with service.db.transaction() as connection:
            access.require_version(
                connection, settings.project, version_id, request.state.actor
            )
        row = http_queries.get_semantic_revision(service.db, settings.project, version_id, semantic_id)
        if not row:
            raise DataProblemError("SEMANTIC_REVISION_NOT_FOUND", 404)
        return to_wire(with_semantic_id(dict(row), "semantic_id"))

    @app.post("/data/v3/versions/{versionId}/semantics", response_model=SemanticUpdateResponse, operation_id="data_create_semantic_revision")
    def semantics(version_id: Annotated[str, Path(alias="versionId")], body: SemanticUpdate, request: Request):
        with service.db.transaction() as connection:
            return to_wire(semantic_service.add_semantics(
                connection, settings.project, version_id, body.semantics,
                body.expected_semantic_id, request.state.actor,
            ))
