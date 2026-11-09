from __future__ import annotations

import json
from collections.abc import Callable

import httpx
from jsonschema import ValidationError, validate
from platform_protocol_contracts import LocalIdentity, trace_context_for_id

from ..errors import AgentRunError, UpstreamServiceError
from ..settings import ObservationToolDefinition, ServiceBinding
from ..tool_progress import render_tool_outcome


class ObservationClient:
    def __init__(
        self,
        client: httpx.AsyncClient,
        *,
        actor_subject_resolver: Callable[[str], str | None] | None = None,
        default_actor_subject: str | None = None,
    ) -> None:
        self.client = client
        self.actor_subject_resolver = actor_subject_resolver
        self.default_actor_subject = (
            default_actor_subject or LocalIdentity().subject
        )

    def _actor_subject(
        self, actor_subject: str | None, agent_run_id: str | None
    ) -> str:
        if actor_subject:
            return actor_subject
        if self.actor_subject_resolver is not None and agent_run_id:
            resolved = self.actor_subject_resolver(agent_run_id)
            if not resolved:
                raise AgentRunError("OBSERVATION_IDENTITY_UNAVAILABLE")
            return resolved
        return self.default_actor_subject

    async def invoke(
        self,
        definition: ObservationToolDefinition,
        service: ServiceBinding,
        *,
        call_id: str,
        project_id: str,
        trace_id: str,
        arguments: dict[str, object],
        actor_subject: str | None = None,
        agent_run_id: str | None = None,
    ) -> dict[str, object]:
        subject = self._actor_subject(actor_subject, agent_run_id)
        trace_context = trace_context_for_id(trace_id)
        try:
            response = await self.client.post(
                f"{service.internal_base_url}{definition.path}",
                json={
                    "callId": call_id,
                    "projectId": project_id,
                    "arguments": arguments,
                    "actor": {
                        "subject": subject,
                        "roles": ["researcher"],
                    },
                },
                headers={
                    "X-Trace-ID": trace_id,
                    "traceparent": trace_context.traceparent,
                },
                timeout=definition.timeout_seconds,
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as error:
            status = error.response.status_code
            if status == 404:
                raise AgentRunError("OBSERVATION_RESOURCE_NOT_FOUND") from None
            if status == 422:
                raise AgentRunError(
                    "OBSERVATION_ARGUMENTS_INVALID",
                    diagnostics={"reason": "OBSERVATION_ARGUMENTS_INVALID"},
                ) from None
            if status == 503:
                raise UpstreamServiceError("OBSERVATION_SERVICE_UNAVAILABLE") from None
            raise UpstreamServiceError("OBSERVATION_RESPONSE_INVALID") from None
        except httpx.RequestError:
            raise UpstreamServiceError("OBSERVATION_SERVICE_UNAVAILABLE") from None
        try:
            body = response.json()
            expected_fields = (
                {"data", "outcome"} if definition.progress is not None else {"data"}
            )
            if not isinstance(body, dict) or set(body) != expected_fields:
                raise ValueError("OBSERVATION_RESPONSE_INVALID")
            validate(body["data"], definition.output_schema)
            if definition.progress is not None:
                render_tool_outcome(definition.progress, body["outcome"])
            encoded = json.dumps(body, ensure_ascii=False, separators=(",", ":"))
            encoded.encode("utf-8")
            return body
        except (ValueError, TypeError, UnicodeEncodeError, ValidationError) as error:
            raise UpstreamServiceError("OBSERVATION_RESPONSE_INVALID") from error
