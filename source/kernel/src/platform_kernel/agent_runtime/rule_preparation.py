"""Prepare an explicit rules document through Data's existing publication API."""

from __future__ import annotations

import asyncio
import hashlib
import json

import httpx
from jsonschema import Draft202012Validator
from platform_protocol_contracts import encode_display_name_header

from .governance import governance

RULE_REQUEST_SCHEMA = {
    "type": "object",
    "properties": {
        "operator_id": {"type": "string"},
        "operator_version": {"type": "string"},
        "rules_port": {"type": "string"},
        "rules": {"type": "object"},
        "data_inputs": {"type": "object", "additionalProperties": {"type": "string"}},
    },
    "required": ["operator_id", "operator_version", "rules_port", "rules", "data_inputs"],
    "additionalProperties": False,
}


def _blocked(code, **details):
    return {"status": "RULES_BLOCKED", "code": code, **details}


async def prepare_operator_rules(context, request, profile, observation_client, inspector, data_service):
    invalid = next(Draft202012Validator(RULE_REQUEST_SCHEMA).iter_errors(request), None)
    if invalid is not None:
        return _blocked("RULE_REQUEST_INVALID", path=list(invalid.path))
    identity = request["operator_id"], request["operator_version"]
    operator = profile.operators.get(identity)
    if operator is None:
        return _blocked("COMPOSITION_OPERATOR_UNSUPPORTED")
    port = next((port for port in operator["inputs"] if port["name"] == request["rules_port"]), {})
    version = port.get("rulesSchema")
    if version is None:
        return _blocked("OPERATOR_RULES_PORT_UNSUPPORTED")
    rules = request["rules"]
    issue = next(Draft202012Validator(profile.rule_schemas[version]).iter_errors(rules), None)
    if issue is not None:
        return _blocked("RULESET_INVALID", path=list(issue.path), validator=issue.validator)
    data_inputs = request["data_inputs"]
    observed = governance(context.context)["data_evidence"]
    required = {item["name"] for item in operator["inputs"] if item["required"] and "rulesSchema" not in item}
    if set(data_inputs) != required or not set(data_inputs.values()) <= observed.keys():
        return _blocked("RULE_INPUT_EVIDENCE_REQUIRED", ports=sorted(required))
    run_id = context.context["agent_run_id"]
    project = context.context["project_id"]
    trace = context.context["trace_id"]
    if version == "table-join-rules.v1":
        for side in ("left", "right"):
            result = await observation_client.invoke(
                inspector, data_service, call_id=f"{context.tool_call_id}-{side}",
                project_id=project, trace_id=trace, agent_run_id=run_id,
                arguments={"version_id": data_inputs[side]},
            )
            source = result["data"]
            if source.get("status", "OK") != "OK":
                return _blocked("RULE_INPUT_EVIDENCE_REQUIRED", ports=[side])
            fields = {field["name"] for field in source["descriptor"].get("fields", [])}
            missing = sorted({key[side] for key in rules["keys"]} - fields)
            if missing:
                return _blocked("RULE_FIELD_NOT_FOUND", port=side, fields=missing)
    blob = json.dumps(rules, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    if len(blob) > 64 * 1024:
        return _blocked("RULESET_TOO_LARGE")
    digest = hashlib.sha256(blob).hexdigest()
    subject = observation_client.actor_subject(None, run_id)
    headers = {
        "X-Project-ID": project,
        "X-Trace-ID": trace,
        "X-Client-Type": "agent",
        "X-Platform-Subject": subject,
        "X-Platform-Display-Name": encode_display_name_header(subject),
        "X-Platform-Roles": "researcher",
    }

    async def call(method, path, *, request_headers=None, **kwargs):
        response = await observation_client.client.request(
            method, f"{data_service.internal_base_url}/data/v3/publications{path}",
            headers={**headers, **(request_headers or {})}, timeout=10.0, **kwargs,
        )
        response.raise_for_status()
        return response.json()

    try:
        async with asyncio.timeout(40):
            publication = await call("POST", "", json={
                "idempotencyKey": f"operator-rules-{digest}",
                "outputs": [{
                    "name": "file", "title": f"{version} {digest[:12]}",
                    "format": "json", "policy": "FILE_ONLY",
                    "semantics": {"source": "agent_operator_rules"},
                }],
                "provenance": {"client": "platform-agent", "purpose": "operator_rules"},
            })
            path = f"/{publication['publicationId']}"
            if publication["state"] == "OPEN":
                if "file" not in publication["uploads"]:
                    await call("PUT", path + "/items/file", content=blob,
                               request_headers={"X-Content-SHA256": f"sha256:{digest}"})
                await call("POST", path + "/prepare")
                publication = await call("GET", path)
            while publication["state"] == "PREPARING":
                await asyncio.sleep(0.25)
                publication = await call("GET", path)
            if publication["state"] not in {"READY", "PUBLISHED"}:
                return _blocked("RULE_PUBLICATION_FAILED", state=publication["state"])
            receipt = publication["receipt"] if publication["state"] == "PUBLISHED" else await call("POST", path + "/commit")
    except (httpx.HTTPError, TimeoutError):
        return _blocked("RULE_PUBLICATION_UNAVAILABLE")
    return {
        "status": "RULES_READY", "rules_schema": version,
        "version_id": receipt["outputs"][0]["versionId"],
        "checksum": f"sha256:{digest}",
    }
