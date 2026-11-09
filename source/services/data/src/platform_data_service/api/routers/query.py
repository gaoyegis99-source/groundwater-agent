import json

from typing import Annotated

from fastapi import FastAPI, Path, Query, Request

from ...composition import Application
from ...query import service as queries
from ..models import DataQueryRequest
from ..responses import QueryAccepted, QueryCancelResponse, QueryResponse
from ..wire import fields_with_semantic_ids, to_wire


def register_query_routes(app: FastAPI, settings, service: Application) -> None:
    @app.post("/data/v3/queries", status_code=202, response_model=QueryAccepted, operation_id="data_create_query")
    def query(body: DataQueryRequest, request: Request):
        with service.db.transaction() as connection:
            return to_wire(queries.submit(connection, settings.project, body, request.state.actor))

    @app.get("/data/v3/queries/{queryId}", response_model=QueryResponse, operation_id="data_get_query")
    def query_result(query_id: Annotated[str, Path(alias="queryId")], request: Request, offset: int = Query(default=0, ge=0), limit: int = Query(default=100, ge=1, le=1000)):
        with service.db.transaction() as connection:
            row = queries.get_owned(
                connection, settings.project, query_id, request.state.actor
            )
        result = None
        if row["state"] == "SUCCEEDED" and row["result_available"]:
            with service.store.open(row["result_object"]) as source:
                result = json.load(source)
            all_rows = result["rows"]
            result["rows"] = all_rows[offset : offset + limit]
            result["next_offset"] = offset + limit if offset + limit < len(all_rows) else None
            result = fields_with_semantic_ids(result)
        return to_wire({
            "query_id": query_id, "state": row["state"], "error": row["error"],
            "receipt": row["receipt"], "expires_at": row["expires_at"],
            "result_available": bool(row["result_available"]), "result": result,
        })

    @app.post("/data/v3/queries/{queryId}/cancel", response_model=QueryCancelResponse, operation_id="data_cancel_query")
    def cancel(query_id: Annotated[str, Path(alias="queryId")], request: Request):
        with service.db.transaction() as connection:
            return to_wire(
                queries.cancel(
                    connection, settings.project, query_id, request.state.actor
                )
            )
