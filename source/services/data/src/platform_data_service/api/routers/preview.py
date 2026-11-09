import json

from typing import Annotated

from fastapi import FastAPI, Path, Query, Request

from ...composition import Application
from ...preview import service as previews
from ..models import PreviewRequest
from ..responses import PreviewAccepted, PreviewCancelResponse, PreviewResponse
from ..wire import fields_with_semantic_ids, to_wire


def register_preview_routes(app: FastAPI, settings, service: Application) -> None:
    @app.post("/data/v3/previews", status_code=202, response_model=PreviewAccepted, operation_id="data_create_preview")
    def preview(body: PreviewRequest, request: Request):
        with service.db.transaction() as connection:
            return to_wire(previews.submit(
                connection, settings.project, body, request.state.actor,
                settings.preview_input_bytes,
            ))

    @app.get("/data/v3/previews/{previewId}", response_model=PreviewResponse, operation_id="data_get_preview")
    def preview_result(preview_id: Annotated[str, Path(alias="previewId")], request: Request, offset: int = Query(default=0, ge=0), limit: int = Query(default=100, ge=1, le=1000)):
        with service.db.transaction() as connection:
            row = previews.get_owned(
                connection, settings.project, preview_id, request.state.actor
            )
        result = None
        if row["state"] == "SUCCEEDED" and row["result_available"]:
            with service.store.open(row["result_object"]) as source:
                result = json.load(source)
            all_rows, all_features = result["rows"], result.get("features", [])
            result["rows"] = all_rows[offset : offset + limit]
            result["features"] = [feature for feature in all_features[offset : offset + limit] if feature is not None]
            result["next_offset"] = offset + limit if offset + limit < len(all_rows) else None
            result = fields_with_semantic_ids(result)
        return to_wire({
            "preview_id": preview_id, "state": row["state"], "error": row["error"],
            "receipt": row["receipt"], "expires_at": row["expires_at"],
            "result_available": bool(row["result_available"]), "result": result,
        })

    @app.post("/data/v3/previews/{previewId}/cancel", response_model=PreviewCancelResponse, operation_id="data_cancel_preview")
    def cancel_preview(
        preview_id: Annotated[str, Path(alias="previewId")], request: Request
    ):
        with service.db.transaction() as connection:
            return to_wire(
                previews.cancel(
                    connection, settings.project, preview_id, request.state.actor
                )
            )
