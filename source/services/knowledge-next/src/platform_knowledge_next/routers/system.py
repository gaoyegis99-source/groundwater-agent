from __future__ import annotations

from importlib.resources import files
import json

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from platform_embedding_client import UpstreamServiceError
import psycopg

from ..config import INDEX_GENERATION, MODEL_PROFILE, Settings
from ..database import Database
from ..errors import KnowledgeProblemError
from ..http_models import CapabilitiesResponse, HealthResponse, ObservationToolContractResponse, ReadinessResponse

PREFIX = "/knowledge/v3"


def register_system_routes(app: FastAPI, settings: Settings, db: Database, embeddings, *, max_body: int) -> None:
    @app.get("/internal/health", response_model=HealthResponse, operation_id="knowledge_internal_health")
    def health():
        db.check()
        return {"status": "OK"}

    @app.get("/internal/health/live", response_model=HealthResponse, operation_id="knowledge_internal_liveness")
    def live():
        return {"status": "live"}

    @app.get("/internal/health/ready", response_model=ReadinessResponse, operation_id="knowledge_internal_readiness")
    def ready():
        components = {"database": "DOWN", "embedding": "DOWN"}
        try:
            db.check()
            components["database"] = "UP"
        except (KnowledgeProblemError, RuntimeError, psycopg.Error):
            return JSONResponse({"status": "down", "components": components}, status_code=503)
        try:
            embeddings.ready()
            components["embedding"] = "UP"
        except UpstreamServiceError:
            return JSONResponse({"status": "down", "components": components}, status_code=503)
        return {"status": "ready", "components": components}

    @app.get(PREFIX + "/capabilities", response_model=CapabilitiesResponse, operation_id="knowledge_capabilities")
    def capabilities(request: Request):
        actor = request.state.actor
        roles = set(actor["roles"])
        operator = "admin" in roles
        writable = not settings.read_only
        return {
            "version": "2.0.0", "productId": settings.product,
            "project": settings.project,
            "currentUser": {
                "subject": actor["subject"],
                "displayName": actor["display_name"],
                "roles": actor["roles"],
            },
            "permissions": {
                "catalogRead": True,
                "retrievalCreate": True,
                "evidenceRead": True,
                "evidenceValidate": True,
                "importWrite": writable and operator,
                "lifecycleWrite": writable and operator,
                "accessWrite": writable and roles != {"viewer"},
            },
            "identity": settings.identity_mode,
            "writeEnabled": writable,
            "schema": "knowledge-package.v1", "databaseSchema": 6,
            "retrievalScopes": ["general", "bound", "combined"],
            "defaultScope": "general_without_binding_combined_with_binding",
            "dataRetry": {"maxAttempts": 5, "delaysSeconds": [5, 15, 45, 120], "deadlineSeconds": 600},
            "modelProfile": MODEL_PROFILE, "indexGeneration": INDEX_GENERATION,
            "vectorSearch": settings.vector_search, "maxBodyBytes": max_body,
            "dataConfigured": bool(settings.data_url), "openapi": "/openapi.json",
        }

    @app.get(PREFIX + "/tools", response_model=ObservationToolContractResponse, operation_id="knowledge_tool_contract")
    def tool_contracts():
        return json.loads(files("platform_knowledge_next").joinpath("contracts/observation-tools.v5.json").read_text())
