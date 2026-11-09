import os

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
import psycopg

from ...composition import Application
from ...formats import FORMAT_MEDIA_TYPES
from ..models import BeginPublicationRequest, DataQueryRequest, PreviewRequest, SemanticUpdate
from ..responses import CapabilitiesResponse, HealthResponse, ReadinessResponse
from ..wire import to_wire


def register_system_routes(app: FastAPI, settings, service: Application) -> None:
    @app.get("/internal/health", response_model=HealthResponse, operation_id="data_internal_health")
    def health():
        service.db.check()
        return {"status": "ready"}

    @app.get("/internal/health/live", response_model=HealthResponse, operation_id="data_internal_liveness")
    def live():
        return {"status": "live"}

    @app.get("/internal/health/ready", response_model=ReadinessResponse, operation_id="data_internal_readiness")
    def ready():
        components = {"database": "DOWN", "object_store": "DOWN"}
        try:
            service.db.check()
            components["database"] = "UP"
            directory = os.open(service.store.objects, os.O_RDONLY | os.O_DIRECTORY)
            os.close(directory)
            if os.access(service.store.objects, os.R_OK | os.W_OK | os.X_OK):
                components["object_store"] = "UP"
        except (OSError, RuntimeError, psycopg.Error):
            pass
        ready = all(value == "UP" for value in components.values())
        return JSONResponse(
            to_wire({"status": "ready" if ready else "down", "components": components}),
            status_code=200 if ready else 503,
        )

    @app.get("/data/v3/capabilities", response_model=CapabilitiesResponse, operation_id="data_capabilities")
    def capabilities(request: Request):
        actor = request.state.actor
        roles = set(actor["roles"])
        operator = "admin" in roles
        auditor_only = roles == {"viewer"}
        writable = not settings.read_only
        return to_wire({
            "version": "1", "product_id": settings.product,
            "project": settings.project,
            "current_user": {
                "subject": actor["subject"],
                "display_name": actor["display_name"],
                "roles": actor["roles"],
            },
            "permissions": {
                "catalog_read": True,
                "query_create": not auditor_only,
                "preview_create": not auditor_only,
                "object_download": True,
                "user_upload_create": writable and not auditor_only,
                "managed_publication_write": writable and operator,
                "semantic_write": writable and operator,
                "access_write": writable and not auditor_only,
                "governance_read": operator,
                "quality_write": writable and operator,
                "mlops_write": writable and operator,
            },
            "identity_mode": settings.identity_mode,
            "write_enabled": writable,
            "formats": list(FORMAT_MEDIA_TYPES), "format_media_types": FORMAT_MEDIA_TYPES,
            "query": {"kinds": ["table", "metrics"], "joins": False, "arbitrary_sql": False},
            "preview": {"kinds": ["xlsx_table", "geopackage_layer"], "arbitrary_sql": False, "formula_execution": False},
            "limits": {key: getattr(settings, key) for key in (
                "query_seconds", "preview_seconds", "prepare_seconds",
                "result_rows", "result_bytes",
                "preview_input_bytes", "preview_expanded_bytes", "preview_rows",
                "preview_columns", "preview_coordinates", "upload_bytes",
                "json_validation_bytes", "result_hours",
            )},
            "contracts": {
                "publication": BeginPublicationRequest.model_json_schema(),
                "query": DataQueryRequest.model_json_schema(),
                "preview": PreviewRequest.model_json_schema(),
                "semantics": SemanticUpdate.model_json_schema(),
            },
            "openapi": "/openapi.json",
        })
