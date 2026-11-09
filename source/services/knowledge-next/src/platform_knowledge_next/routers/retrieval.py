from typing import Annotated

from fastapi import FastAPI, Path, Request

from ..database import Database
from ..http_models import EvidenceReadRequest, LookupRequest, ReceiptResponse, RetrievalRequest
from ..models import EvidenceBundleV1
from ..repositories import http_queries
from ..retrieval import EvidenceService
from ..wire import model_to_wire, to_wire

PREFIX = "/knowledge/v3"


def register_retrieval_routes(app: FastAPI, db: Database, service: EvidenceService) -> None:
    @app.post(PREFIX + "/retrievals", response_model=EvidenceBundleV1, operation_id="knowledge_retrieve")
    def retrieve(body: RetrievalRequest, request: Request):
        return model_to_wire(service.retrieve(body, request.state.actor))

    @app.get(PREFIX + "/retrievals/{receiptId}", response_model=ReceiptResponse, operation_id="knowledge_get_retrieval_receipt")
    def receipt(
        receipt_id: Annotated[str, Path(alias="receiptId")], request: Request
    ):
        return to_wire(
            http_queries.get_receipt(db, receipt_id, request.state.actor)
        )

    @app.post(PREFIX + "/evidence/read", response_model=EvidenceBundleV1, operation_id="knowledge_read_evidence")
    def read(body: EvidenceReadRequest, request: Request):
        return model_to_wire(service.read(body, request.state.actor))

    @app.post(PREFIX + "/evidence/validate", response_model=EvidenceBundleV1, operation_id="knowledge_validate_evidence")
    def validate(body: EvidenceReadRequest, request: Request):
        return model_to_wire(
            service.read(body, request.state.actor, validate_only=True)
        )

    @app.post(PREFIX + "/lookup", response_model=EvidenceBundleV1, operation_id="knowledge_lookup")
    def lookup(body: LookupRequest, request: Request):
        return model_to_wire(service.lookup(body, request.state.actor))
