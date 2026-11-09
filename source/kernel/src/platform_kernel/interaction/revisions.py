from __future__ import annotations

import hashlib
import json
from pathlib import Path
from uuid import uuid4

import httpx
from jsonschema import ValidationError, validate

from ..application.task_api import bind_confirmation
from ..database import append_event, connect_database, utc_now
from ..errors import (
    ConfirmationConflictError,
    ResourceNotFoundError,
    UpstreamServiceError,
)
from ..contracts import DataVersionRefV2
from .actors import DEFAULT_LOCAL_ACTOR_SUBJECT
from .state_projection import confirmation_from_row


def _canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _data_reference(value: object) -> DataVersionRefV2:
    if not isinstance(value, dict) or not isinstance(value.get("objects"), list):
        raise ConfirmationConflictError("CONFIRMATION_DATA_VERSION_INVALID")
    raw = [
        item
        for item in value["objects"]
        if isinstance(item, dict) and item.get("role") == "raw"
    ]
    if len(raw) != 1:
        raise ConfirmationConflictError("CONFIRMATION_DATA_VERSION_INVALID")
    item = raw[0]
    try:
        return DataVersionRefV2.from_dict(
            {
                "kind": "data_version",
                "service": "data-primary",
                "datasetId": value["datasetId"],
                "versionId": value["versionId"],
                "semanticId": value["semanticId"],
                "objectId": item["objectId"],
                "mediaType": item["mediaType"],
                "format": item["format"],
                "sizeBytes": item["size"],
                "checksum": item["checksum"],
            }
        )
    except (KeyError, TypeError, ValueError) as error:
        raise ConfirmationConflictError("CONFIRMATION_DATA_VERSION_INVALID") from error


async def revise_confirmation(
    database: Path,
    client: httpx.AsyncClient,
    resource_service: object,
    models: tuple[object, ...],
    *,
    session_id: str,
    confirmation_id: str,
    project_id: str,
    request_id: str,
    expected_goal_revision: int,
    actions: tuple[dict[str, object], ...],
    decision_subject: str = DEFAULT_LOCAL_ACTOR_SUBJECT,
):
    fingerprint = hashlib.sha256(
        _canonical(
            {
                "expected_goal_revision": expected_goal_revision,
                "actions": actions,
            }
        ).encode()
    ).hexdigest()
    with connect_database(database) as connection:
        existing = connection.execute(
            "SELECT * FROM confirmations WHERE session_id=? AND request_id=?",
            (session_id, request_id),
        ).fetchone()
        if existing is not None:
            if existing["request_fingerprint"] != fingerprint:
                raise ConfirmationConflictError("CONFIRMATION_REVISION_CONFLICT")
            return confirmation_from_row(connection, existing)
        session = connection.execute(
            "SELECT * FROM sessions WHERE session_id=?",
            (session_id,),
        ).fetchone()
        source = connection.execute(
            "SELECT * FROM confirmations WHERE confirmation_id=? AND session_id=?",
            (confirmation_id, session_id),
        ).fetchone()
        if session is None or session["project_id"] != project_id:
            raise ResourceNotFoundError("SESSION_NOT_FOUND")
        if expected_goal_revision != session["goal_revision"]:
            raise ConfirmationConflictError("CONFIRMATION_REVISION_CONFLICT")
        if (
            source is None
            or source["status"] != "PENDING"
            or source["goal_revision"] != session["goal_revision"]
        ):
            raise ConfirmationConflictError("CONFIRMATION_NOT_CURRENT")
        stored = connection.execute(
            "SELECT * FROM confirmation_actions WHERE confirmation_id=? ORDER BY position",
            (confirmation_id,),
        ).fetchall()
    if tuple(item.get("tool_call_id") for item in actions) != tuple(
        item["tool_call_id"] for item in stored
    ):
        raise ConfirmationConflictError("CONFIRMATION_ACTION_SET_INVALID")
    model_by_id = {item.model_id: item for item in models}
    validated: list[
        tuple[object, dict[str, object], str, dict[str, DataVersionRefV2]]
    ] = []
    for old, submitted in zip(stored, actions, strict=True):
        arguments = submitted.get("arguments")
        model = model_by_id.get(old["model_id"])
        if model is None or not isinstance(arguments, dict):
            raise ConfirmationConflictError("CONFIRMATION_ARGUMENTS_INVALID")
        try:
            validate(arguments, model.input_schema)
        except ValidationError:
            raise ConfirmationConflictError("CONFIRMATION_ARGUMENTS_INVALID") from None
        input_references: dict[str, DataVersionRefV2] = {}
        for parameter in model.data_input_parameters:
            value = arguments.get(parameter)
            version_id = value.get("version_id") if isinstance(value, dict) else value
            if not isinstance(version_id, str):
                raise ConfirmationConflictError("CONFIRMATION_DATA_VERSION_INVALID")
            try:
                response = await client.get(
                    f"{resource_service.internal_base_url}/data/v3/versions/{version_id}",
                    headers={
                        "X-Project-ID": project_id,
                        "X-Client-Type": "agent",
                        "X-Platform-Subject": "platform-kernel",
                        "X-Platform-Display-Name": "Platform Kernel",
                        "X-Platform-Roles": "researcher",
                    },
                    timeout=10,
                )
            except httpx.RequestError:
                raise UpstreamServiceError("DATA_SERVICE_UNAVAILABLE") from None
            if response.status_code != 200:
                raise ConfirmationConflictError("CONFIRMATION_DATA_VERSION_INVALID")
            reference = _data_reference(response.json())
            if reference.version_id != version_id:
                raise ConfirmationConflictError("CONFIRMATION_DATA_VERSION_INVALID")
            input_references[parameter] = reference
        action_fingerprint = hashlib.sha256(
            f"{source['agent_run_id']}:{old['tool']}:{_canonical(arguments)}".encode()
        ).hexdigest()
        validated.append((old, arguments, action_fingerprint, input_references))
    new_id = str(uuid4())
    with connect_database(database) as connection:
        connection.execute("BEGIN IMMEDIATE")
        existing = connection.execute(
            "SELECT * FROM confirmations WHERE session_id=? AND request_id=?",
            (session_id, request_id),
        ).fetchone()
        if existing is not None:
            if existing["request_fingerprint"] != fingerprint:
                raise ConfirmationConflictError("CONFIRMATION_REVISION_CONFLICT")
            return confirmation_from_row(connection, existing)
        current = connection.execute(
            "SELECT * FROM confirmations WHERE confirmation_id=?",
            (confirmation_id,),
        ).fetchone()
        if current is None or current["status"] != "PENDING":
            raise ConfirmationConflictError("CONFIRMATION_NOT_CURRENT")
        new_kind = "RECOVERY" if current["kind"] == "RECOVERY" else "EDITED"
        connection.execute(
            "UPDATE confirmations SET status='SUPERSEDED' WHERE confirmation_id=?",
            (confirmation_id,),
        )
        revision = current["revision"] + 1
        connection.execute(
            """INSERT INTO confirmations(
                   confirmation_id,session_id,goal_revision,agent_run_id,status,reason,
                   decision,decision_state,revision,kind,replaces_confirmation_id,
                   source_workflow_run_id,request_id,request_fingerprint,created_at
               ) VALUES (?,?,?,?, 'PENDING',NULL,NULL,NULL,?,?,?,?,?,?,?)""",
            (
                new_id,
                session_id,
                current["goal_revision"],
                current["agent_run_id"],
                revision,
                new_kind,
                confirmation_id,
                current["source_workflow_run_id"],
                request_id,
                fingerprint,
                utc_now(),
            ),
        )
        for old, arguments, action_fingerprint, input_references in validated:
            connection.execute(
                """INSERT INTO confirmation_actions VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    new_id,
                    old["position"],
                    old["tool_call_id"],
                    old["tool"],
                    old["model_id"],
                    old["model_version"],
                    old["model_checksum"],
                    old["workflow_id"],
                    old["workflow_version"],
                    old["workflow_checksum"],
                    old["input_schema_checksum"],
                    action_fingerprint,
                    _canonical(arguments),
                    _canonical(
                        {
                            name: reference.as_storage_dict()
                            for name, reference in input_references.items()
                        }
                    ),
                ),
            )
        bind_confirmation(connection, new_id, session_id)
        append_event(
            connection,
            session_id,
            "confirmation.revised",
            {
                "confirmation_id": new_id,
                "replaces_confirmation_id": confirmation_id,
                "revision": revision,
                "status": "PENDING",
                "decision_subject": decision_subject,
            },
        )
        row = connection.execute(
            "SELECT * FROM confirmations WHERE confirmation_id=?",
            (new_id,),
        ).fetchone()
        return confirmation_from_row(connection, row)
