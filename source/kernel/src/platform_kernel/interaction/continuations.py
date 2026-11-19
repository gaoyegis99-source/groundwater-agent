"""Read provenance and review evidence for a published workflow continuation."""

import hashlib
import json
from pathlib import Path
from urllib.parse import quote

import httpx
from platform_protocol_contracts import encode_display_name_header

from ..contracts import DataVersionRefV2
from ..database import connect_database
from ..errors import ConfirmationConflictError
from ..workflow_selection import REFERENCE_FIELDS

def continuation_report(
    database: Path, session_id: str, project_id: str, request_id: str,
    selection: dict, references: list[dict], policies: tuple[dict, ...],
) -> dict | None:
    source = selection["continuation"]
    with connect_database(database) as connection:
        prior = connection.execute(
            "SELECT 1 FROM message_requests WHERE session_id=? AND request_id=?",
            (session_id, request_id),
        ).fetchone()
        if prior is not None:
            # The command boundary validates the full replay payload.
            return None
        row = connection.execute(
            "SELECT model_id,outputs_json FROM workflow_runs WHERE run_id=? AND session_id=? AND project_id=? AND status='SUCCEEDED'",
            (source["run_id"], session_id, project_id),
        ).fetchone()
    policy = next((item for item in policies if row is not None and item["sourceModel"] == row[0]
                   and item["output"] == source["output_name"] and item["targetModel"] == selection["model_id"]), None)
    if policy is None:
        raise ConfirmationConflictError("WORKFLOW_CONTINUATION_INVALID")
    outputs = {item["output_name"]: item for item in json.loads(row[1])}
    expected = outputs.get(policy["output"])
    index = selection["input_bindings"].get(policy["targetInput"])
    requested = DataVersionRefV2.from_dict(references[index]).as_storage_dict() if index is not None and 0 <= index < len(references) else {}
    if expected is None or any(expected[field] != requested.get(field) for field in REFERENCE_FIELDS):
        raise ConfirmationConflictError("WORKFLOW_CONTINUATION_INVALID")
    report_name = policy.get("reviewReport")
    if report_name is None:
        return None
    report = outputs.get(report_name)
    if report is None or report["format"] != "json" or not 0 < report["size_bytes"] <= 262_144:
        raise ConfirmationConflictError("WORKFLOW_CONTINUATION_REVIEW_UNAVAILABLE")
    return report


async def verify_continuation_review(
    client: httpx.AsyncClient, base_url: str, project: str, subject: str, display_name: str, report: dict,
) -> None:
    path = (
        f"/data/v3/versions/{quote(report['version_id'], safe='')}/objects/"
        f"{quote(report['object_id'], safe='')}/content"
    )
    try:
        async with client.stream("GET", base_url + path, headers={
            "X-Project-ID": project, "X-Client-Type": "service",
            "X-Platform-Subject": subject, "X-Platform-Roles": "researcher",
            "X-Platform-Display-Name": encode_display_name_header(display_name),
        }, timeout=10) as response:
            response.raise_for_status()
            content = bytearray()
            async for block in response.aiter_bytes():
                content.extend(block)
                if len(content) > report["size_bytes"]:
                    raise ValueError("Report size mismatch")
        if len(content) != report["size_bytes"] or "sha256:" + hashlib.sha256(content).hexdigest() != report["checksum"]:
            raise ValueError("Report identity mismatch")
        payload = json.loads(content)
        if not isinstance(payload, dict) or payload.get("status") not in {"BLOCKED", "REVIEW_REQUIRED"}:
            raise ValueError("Report status missing")
    except (httpx.HTTPError, ValueError, TypeError) as error:
        raise ConfirmationConflictError("WORKFLOW_CONTINUATION_REVIEW_UNAVAILABLE") from error
    if payload["status"] == "BLOCKED":
        raise ConfirmationConflictError("WORKFLOW_CONTINUATION_BLOCKED")
