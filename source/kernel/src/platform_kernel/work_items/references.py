"""Verify WorkItem references against the owners of the underlying resources."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
import re
from typing import TYPE_CHECKING
from urllib.parse import quote

import httpx
from platform_protocol_contracts import encode_display_name_header, inject_trace_headers

from ..contracts import DataVersionRefV2
from ..errors import ResourceNotFoundError, UpstreamServiceError, WorkItemConflictError
from ..interaction.access import require_owned_workflow

if TYPE_CHECKING:
    from ..access import RequestIdentity
    from ..settings import KernelSettings
    from ..workflow_execution.models import WorkflowRun
    from ..workflow_execution.owner import WorkflowExecutionOwner
    from .lifecycle import PublicationState


class ReferenceAuthority:
    def __init__(
        self,
        client: httpx.AsyncClient,
        settings: KernelSettings,
        workflows: WorkflowExecutionOwner,
        database: Path,
    ) -> None:
        self._client = client
        self._project_id = settings.project_id
        self._services = settings.services_by_name
        self._workflows = workflows
        self._database = database

    async def resolve_object(self, reference: dict, actor: RequestIdentity) -> dict:
        kind = reference.get("objectKind")
        if kind not in ("WELL", "REGION", "AQUIFER"):
            raise WorkItemConflictError("WORK_ITEM_REFERENCE_INVALID")
        source = _data_reference(reference.get("source"))
        field_id = _text(reference.get("fieldId"))
        external_id = _text(reference.get("externalId"))
        await self._verify_data(source, actor, field_id=field_id)
        return {
            "objectKind": kind, "source": source.as_dict(),
            "fieldId": field_id, "externalId": external_id,
        }

    async def resolve_asset(
        self, reference: dict, actor: RequestIdentity, linked_runs: frozenset[str],
    ) -> dict:
        kind = reference.get("kind")
        if kind == "DATA":
            data_ref = _data_reference(reference.get("dataRef"))
            await self._verify_data(data_ref, actor)
            return {"kind": kind, "dataRef": data_ref.as_dict()}
        if kind == "KNOWLEDGE":
            return await self._resolve_knowledge(reference, actor)
        if kind not in ("WORKFLOW", "RESULT"):
            raise WorkItemConflictError("WORK_ITEM_REFERENCE_INVALID")

        run_id = _text(reference.get("runId"))
        if run_id not in linked_runs:
            raise ResourceNotFoundError("WORKFLOW_RUN_NOT_FOUND")
        run = self._owned_run(run_id, actor)
        if kind == "WORKFLOW":
            return _workflow_reference(run)

        output_name = _text(reference.get("outputName"))
        output = _named_output(run, output_name)
        publication = await self._workflows.run_publication(self._project_id, run_id)
        publication_id = publication.get("publicationId")
        if (
            publication.get("workflowRunId") != run_id or publication.get("state") != "PUBLISHED"
            or not isinstance(publication_id, str) or not publication_id
        ):
            raise WorkItemConflictError("WORK_ITEM_RESULT_NOT_PUBLISHED")
        await self._verify_data(output, actor, publication_id=publication_id)
        return {
            "kind": kind, "runId": run_id, "outputName": output_name,
            "publicationId": publication_id, "dataRef": output.as_dict(),
        }

    async def is_visible(self, kind: str, reference: dict, actor: RequestIdentity) -> bool:
        try:
            if kind == "OBJECT":
                return await self.resolve_object(reference, actor) == reference
            if reference.get("kind") != kind:
                return False
            if kind in ("DATA", "KNOWLEDGE"):
                return await self.resolve_asset(reference, actor, frozenset()) == reference
            if kind == "WORKFLOW":
                run = self._owned_run(_text(reference.get("runId")), actor)
                return _workflow_reference(run) == reference
            if kind == "RESULT":
                return await self._result_visible(reference, actor)
            return False
        except (ResourceNotFoundError, UpstreamServiceError, WorkItemConflictError):
            return False

    async def publication_state(self, run_id: str, actor: RequestIdentity) -> PublicationState:
        """Read the publication authority for owned runs without an attached result."""
        try:
            require_owned_workflow(self._database, self._project_id, run_id, actor.subject)
            value = await self._workflows.run_publication(self._project_id, run_id)
        except (ResourceNotFoundError, UpstreamServiceError):
            return "UNAVAILABLE"
        if value.get("workflowRunId") != run_id or not value.get("publicationId"):
            return "UNAVAILABLE"
        if value.get("state") == "PUBLISHED":
            return "PUBLISHED"
        if value.get("state") in {"ABORTED", "FAILED"}:
            return "FAILED"
        return "PENDING" if value.get("state") else "UNAVAILABLE"

    async def require_result(self, reference: dict, actor: RequestIdentity) -> None:
        if not await self._result_visible(reference, actor):
            raise WorkItemConflictError("WORK_ITEM_REVIEW_RESULT_MISMATCH")

    def _owned_run(self, run_id: str, actor: RequestIdentity) -> WorkflowRun:
        require_owned_workflow(self._database, self._project_id, run_id, actor.subject)
        return self._workflows.get(self._project_id, run_id)

    async def _result_visible(self, reference: dict, actor: RequestIdentity) -> bool:
        run = self._owned_run(_text(reference.get("runId")), actor)
        output = _named_output(run, _text(reference.get("outputName")))
        if (
            set(reference) != {"kind", "runId", "outputName", "publicationId", "dataRef"}
            or _data_reference(reference.get("dataRef"), named=True) != output
        ):
            return False
        # The Data version keeps the publication binding after Executor redeployment.
        await self._verify_data(output, actor, publication_id=_text(reference.get("publicationId")))
        return True

    async def _verify_data(
        self, reference: DataVersionRefV2, actor: RequestIdentity, *,
        field_id: str | None = None, publication_id: str | None = None,
    ) -> None:
        path = f"/data/v3/versions/{_segment(reference.version_id)}"
        version = await self._read("data", path, actor)
        expected = {
            "project": self._project_id,
            "datasetId": reference.dataset_id,
            "versionId": reference.version_id,
        }
        if publication_id is not None:
            expected["publicationId"] = publication_id
        _require_identity(version, expected)

        objects = version.get("objects")
        if not isinstance(objects, list) or any(not isinstance(item, dict) for item in objects):
            raise UpstreamServiceError("WORK_ITEM_REFERENCE_RESPONSE_INVALID")
        matches = [item for item in objects if item.get("objectId") == reference.object_id]
        if len(matches) != 1:
            raise WorkItemConflictError("WORK_ITEM_REFERENCE_MISMATCH")
        _require_identity(matches[0], {
            "mediaType": reference.media_type, "format": reference.format_id,
            "size": reference.size_bytes, "checksum": reference.checksum,
            **({"role": "raw"} if publication_id is not None else {}),
        })

        current_semantic = version.get("semanticId")
        if not isinstance(current_semantic, str) or not current_semantic:
            raise UpstreamServiceError("WORK_ITEM_REFERENCE_RESPONSE_INVALID")
        semantics = version.get("semantics")
        if current_semantic != reference.semantic_id:
            historical = await self._read(
                "data", f"{path}/semantics/{_segment(reference.semantic_id)}", actor,
            )
            _require_identity(historical, {
                "versionId": reference.version_id, "semanticId": reference.semantic_id,
            })
            semantics = historical.get("body")
        if field_id is not None:
            fields = semantics.get("fields") if isinstance(semantics, dict) else None
            if not isinstance(fields, dict):
                raise UpstreamServiceError("WORK_ITEM_REFERENCE_RESPONSE_INVALID")
            if field_id not in fields:
                raise WorkItemConflictError("WORK_ITEM_REFERENCE_MISMATCH")

    async def _resolve_knowledge(self, reference: dict, actor: RequestIdentity) -> dict:
        version_id = _text(reference.get("versionId"))
        checksum = _text(reference.get("checksum"))
        if re.fullmatch(r"sha256:[0-9a-f]{64}", checksum) is None:
            raise WorkItemConflictError("WORK_ITEM_REFERENCE_INVALID")
        expected = {
            "documentId": _text(reference.get("documentId")),
            "versionId": version_id, "checksum": checksum,
        }
        version = await self._read("knowledge-next", f"/knowledge/v3/versions/{_segment(version_id)}", actor)
        _require_identity(version, expected)
        return {"kind": "KNOWLEDGE", **expected}

    async def _read(self, binding: str, path: str, actor: RequestIdentity) -> dict:
        service = self._services.get(binding)
        if service is None:
            raise UpstreamServiceError("WORK_ITEM_REFERENCE_UNAVAILABLE")
        headers = inject_trace_headers({
            "X-Project-ID": self._project_id,
            "X-Client-Type": "service",
            "X-Platform-Subject": actor.subject,
            "X-Platform-Display-Name": encode_display_name_header(actor.display_name),
            "X-Platform-Roles": ",".join(sorted(actor.roles)),
        })
        try:
            response = await self._client.get(
                service.internal_base_url.rstrip("/") + path, headers=headers,
                timeout=10, follow_redirects=False,
            )
        except (httpx.InvalidURL, httpx.RequestError) as error:
            raise UpstreamServiceError("WORK_ITEM_REFERENCE_UNAVAILABLE") from error
        if response.status_code in (403, 404):
            raise ResourceNotFoundError("WORK_ITEM_REFERENCE_NOT_FOUND")
        if response.status_code != 200:
            raise UpstreamServiceError("WORK_ITEM_REFERENCE_UNAVAILABLE")
        try:
            body = response.json()
        except ValueError as error:
            raise UpstreamServiceError("WORK_ITEM_REFERENCE_RESPONSE_INVALID") from error
        if not isinstance(body, dict):
            raise UpstreamServiceError("WORK_ITEM_REFERENCE_RESPONSE_INVALID")
        return body


def _named_output(run: WorkflowRun, output_name: str) -> DataVersionRefV2:
    if run.status != "SUCCEEDED":
        raise WorkItemConflictError("WORK_ITEM_RESULT_NOT_PUBLISHED")
    matches = [output for output in run.outputs if output.output_name == output_name]
    if len(matches) != 1:
        raise WorkItemConflictError("WORK_ITEM_REFERENCE_MISMATCH")
    return matches[0]


def _workflow_reference(run: WorkflowRun) -> dict:
    return {
        "kind": "WORKFLOW", "runId": run.run_id,
        "workflowRef": {"id": run.workflow_id, "version": run.workflow_version, "checksum": run.workflow_checksum},
    }


def _data_reference(value: object, *, named: bool = False) -> DataVersionRefV2:
    if not isinstance(value, dict) or ("outputName" in value) != named:
        raise WorkItemConflictError("WORK_ITEM_REFERENCE_INVALID")
    try:
        reference = DataVersionRefV2.from_dict(value)
    except ValueError as error:
        raise WorkItemConflictError("WORK_ITEM_REFERENCE_INVALID") from error
    if named and reference.output_name is None:
        raise WorkItemConflictError("WORK_ITEM_REFERENCE_INVALID")
    return reference


def _require_identity(actual: dict, expected: Mapping[str, str | int]) -> None:
    for name, value in expected.items():
        observed = actual.get(name)
        if type(observed) is not type(value):
            raise UpstreamServiceError("WORK_ITEM_REFERENCE_RESPONSE_INVALID")
        if observed != value:
            raise WorkItemConflictError("WORK_ITEM_REFERENCE_MISMATCH")


def _text(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise WorkItemConflictError("WORK_ITEM_REFERENCE_INVALID")
    return value


def _segment(identifier: str) -> str:
    if identifier in (".", ".."):
        raise WorkItemConflictError("WORK_ITEM_REFERENCE_INVALID")
    try:
        return quote(identifier, safe="")
    except UnicodeEncodeError as error:
        raise WorkItemConflictError("WORK_ITEM_REFERENCE_INVALID") from error
