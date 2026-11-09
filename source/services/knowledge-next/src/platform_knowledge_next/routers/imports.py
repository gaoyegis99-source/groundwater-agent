from collections.abc import Callable
from typing import Annotated

from fastapi import Depends, FastAPI, Path, Query, Request

from .. import ingestion
from ..access import require_operator
from ..database import Database
from ..http_models import (
    ImportPage,
    ImportPreflightResponse,
    ImportResponse,
    KnowledgePackageRequest,
    OperatorActivityPage,
    RetryImportRequest,
)
from ..repositories import http_queries
from ..wire import to_wire

PREFIX = "/knowledge/v3"


def register_import_routes(
    app: FastAPI, db: Database, writable: Callable[[], None]
) -> None:
    @app.post(
        PREFIX + "/imports",
        status_code=202,
        response_model=ImportResponse,
        operation_id="knowledge_submit_import",
        dependencies=[Depends(require_operator)],
    )
    def submit(package: KnowledgePackageRequest, request: Request):
        writable()
        return to_wire(ingestion.submit(db, package, request.state.actor))

    @app.post(
        PREFIX + "/imports/preflight",
        response_model=ImportPreflightResponse,
        operation_id="knowledge_preflight_import",
        dependencies=[Depends(require_operator)],
    )
    def preflight(package: KnowledgePackageRequest):
        return to_wire(http_queries.preflight_import(db, package))

    @app.post(
        PREFIX + "/imports/{importId}/retry",
        status_code=202,
        response_model=ImportResponse,
        operation_id="knowledge_retry_import",
        dependencies=[Depends(require_operator)],
    )
    def retry_import(
        import_id: Annotated[str, Path(alias="importId")],
        retry: RetryImportRequest,
        request: Request,
    ):
        writable()
        ingestion.request_manual_retry(
            db, import_id, retry.request_id, request.state.actor
        )
        return to_wire(http_queries.get_import(db, import_id))

    @app.get(
        PREFIX + "/imports",
        response_model=ImportPage,
        operation_id="knowledge_list_imports",
    )
    def imports(cursor: str = "", limit: int = Query(20, ge=1, le=100)):
        return to_wire(http_queries.list_imports(db, cursor, limit))

    @app.get(
        PREFIX + "/activity",
        response_model=OperatorActivityPage,
        operation_id="knowledge_list_operator_activity",
        dependencies=[Depends(require_operator)],
    )
    def activity(limit: int = Query(30, ge=1, le=100)):
        return to_wire(http_queries.list_operator_activity(db, limit))

    @app.get(
        PREFIX + "/imports/{importId}",
        response_model=ImportResponse,
        operation_id="knowledge_get_import",
    )
    def get_import(import_id: Annotated[str, Path(alias="importId")]):
        return to_wire(http_queries.get_import(db, import_id))
