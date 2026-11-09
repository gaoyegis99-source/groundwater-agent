from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from pydantic import ValidationError

from ..config import Settings
from ..access import INTERNAL_ACTOR
from ..errors import KnowledgeProblemError
from ..http_models import ToolRequest, ToolResponse
from ..models import EvidenceRead, Lookup, Retrieval
from ..retrieval import EvidenceService
from ..wire import model_to_wire


def register_tool_routes(app: FastAPI, settings: Settings, service: EvidenceService) -> None:
    handlers = {
        "retrieve_evidence": (Retrieval, service.retrieve),
        "read_evidence": (EvidenceRead, service.read),
        "lookup_knowledge": (Lookup, service.lookup),
    }

    @app.post("/internal/tools/v5/{name}", response_model=ToolResponse, operation_id="knowledge_invoke_tool")
    def tool(name: str, body: ToolRequest):
        if body.project_id != settings.project:
            raise KnowledgeProblemError("PROJECT_MISMATCH", 403)
        if name not in handlers:
            raise KnowledgeProblemError("TOOL_NOT_FOUND", 404)
        model, handler = handlers[name]
        try:
            args = model.model_validate(body.arguments, by_alias=False, by_name=True)
        except ValidationError as error:
            raise RequestValidationError(error.errors()) from error
        actor = (
            INTERNAL_ACTOR
            if body.actor is None
            else {
                "subject": body.actor.subject,
                "display_name": body.actor.subject,
                "roles": body.actor.roles,
                "trace_id": body.call_id,
            }
        )
        result = handler(args, actor)
        data = model_to_wire(result)
        items = result.items
        if name == "read_evidence":
            readable = sum(
                1 for item in items if item.status == "OK" and bool(item.content)
            )
            outcome = {
                "outcomeCode": "evidence.read",
                "facts": {
                    "readableCount": readable,
                    "rejectedCount": len(items) - readable,
                },
            }
        elif items:
            outcome = {
                "outcomeCode": "evidence.candidates_found",
                "facts": {"candidateCount": len(items)},
            }
        else:
            outcome = {"outcomeCode": "evidence.no_match", "facts": {}}
        return {"data": data, "outcome": outcome}
