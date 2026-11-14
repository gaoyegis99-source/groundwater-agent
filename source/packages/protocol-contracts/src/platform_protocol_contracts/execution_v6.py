from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
import re
from typing import Mapping


Scalar = int | float | str


def sha256_bytes(content: bytes) -> str:
    return "sha256:" + hashlib.sha256(content).hexdigest()


def canonical_json(value: object) -> bytes:
    _validate_json(value, "JSON_INVALID")
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


@dataclass(frozen=True)
class DataVersionRefV2:
    kind: str
    service: str
    dataset_id: str
    version_id: str
    semantic_id: str
    object_id: str
    media_type: str
    format_id: str
    size_bytes: int
    checksum: str
    output_name: str | None = None

    def as_dict(self) -> dict[str, object]:
        value: dict[str, object] = {
            "kind": self.kind,
            "service": self.service,
            "datasetId": self.dataset_id,
            "versionId": self.version_id,
            "semanticId": self.semantic_id,
            "objectId": self.object_id,
            "mediaType": self.media_type,
            "format": self.format_id,
            "sizeBytes": self.size_bytes,
            "checksum": self.checksum,
        }
        if self.output_name is not None:
            value["outputName"] = self.output_name
        return value

    def as_storage_dict(self) -> dict[str, object]:
        value: dict[str, object] = {
            "kind": self.kind, "service": self.service,
            "dataset_id": self.dataset_id, "version_id": self.version_id,
            "semantic_id": self.semantic_id, "object_id": self.object_id,
            "media_type": self.media_type, "format": self.format_id,
            "size_bytes": self.size_bytes, "checksum": self.checksum,
        }
        if self.output_name is not None:
            value["output_name"] = self.output_name
        return value

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> DataVersionRefV2:
        required = {
            "kind", "service", "datasetId", "versionId", "semanticId",
            "objectId", "mediaType", "format", "sizeBytes", "checksum",
        }
        if set(value) not in (required, required | {"outputName"}):
            raise ValueError("DATA_VERSION_REFERENCE_INVALID")
        if value["kind"] != "data_version" or value["service"] != "data-primary":
            raise ValueError("DATA_VERSION_REFERENCE_INVALID")
        output_name = value.get("outputName")
        if output_name is not None:
            _require_string(output_name, "DATA_VERSION_REFERENCE_INVALID")
        for field_name in (
            "kind", "service", "datasetId", "versionId", "semanticId", "objectId",
            "mediaType", "format", "checksum",
        ):
            _require_string(value[field_name], "DATA_VERSION_REFERENCE_INVALID")
        if re.fullmatch(r"sha256:[0-9a-f]{64}", str(value["checksum"])) is None:
            raise ValueError("DATA_VERSION_REFERENCE_INVALID")
        size = value["sizeBytes"]
        if isinstance(size, bool) or not isinstance(size, int) or size < 0:
            raise ValueError("DATA_VERSION_REFERENCE_INVALID")
        return cls(
            kind=str(value["kind"]), service=str(value["service"]),
            dataset_id=str(value["datasetId"]), version_id=str(value["versionId"]),
            semantic_id=str(value["semanticId"]), object_id=str(value["objectId"]),
            media_type=str(value["mediaType"]), format_id=str(value["format"]),
            size_bytes=size, checksum=str(value["checksum"]),
            output_name=None if output_name is None else str(output_name),
        )

    @classmethod
    def from_storage_dict(cls, value: Mapping[str, object]) -> DataVersionRefV2:
        keys = {
            "kind": "kind", "service": "service", "dataset_id": "datasetId",
            "version_id": "versionId", "semantic_id": "semanticId",
            "object_id": "objectId", "media_type": "mediaType", "format": "format",
            "size_bytes": "sizeBytes", "checksum": "checksum",
            "output_name": "outputName",
        }
        if any(key not in keys for key in value):
            raise ValueError("DATA_VERSION_REFERENCE_INVALID")
        return cls.from_dict({keys[key]: item for key, item in value.items()})


@dataclass(frozen=True)
class JobRequestV6:
    request_id: str
    project_id: str
    model_id: str
    model_version: str
    model_checksum: str
    workflow_id: str
    workflow_version: str
    workflow_checksum: str
    executor_id: str
    arguments: Mapping[str, object]
    input_references: Mapping[str, DataVersionRefV2]
    deadline_at: str
    traceparent: str | None = None
    composition: Mapping[str, object] | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "requestId": self.request_id,
            "projectId": self.project_id,
            "modelId": self.model_id,
            "modelVersion": self.model_version,
            "modelChecksum": self.model_checksum,
            "workflowId": self.workflow_id,
            "workflowVersion": self.workflow_version,
            "workflowChecksum": self.workflow_checksum,
            "executorId": self.executor_id,
            "arguments": dict(self.arguments),
            "inputReferences": {
                name: reference.as_dict()
                for name, reference in self.input_references.items()
            },
            "deadlineAt": self.deadline_at,
            **({"composition": dict(self.composition)} if self.composition is not None else {}),
        }

    def as_storage_dict(self) -> dict[str, object]:
        value: dict[str, object] = {
            "request_id": self.request_id, "project_id": self.project_id,
            "model_id": self.model_id, "model_version": self.model_version,
            "model_checksum": self.model_checksum, "workflow_id": self.workflow_id,
            "workflow_version": self.workflow_version,
            "workflow_checksum": self.workflow_checksum, "executor_id": self.executor_id,
            "arguments": dict(self.arguments),
            "input_references": {
                name: reference.as_storage_dict()
                for name, reference in self.input_references.items()
            },
            "deadline_at": self.deadline_at,
        }
        if self.traceparent is not None:
            value["traceparent"] = self.traceparent
        if self.composition is not None:
            value["composition"] = dict(self.composition)
        return value

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> JobRequestV6:
        required = {
            "requestId", "projectId", "modelId", "modelVersion", "modelChecksum",
            "workflowId", "workflowVersion", "workflowChecksum", "executorId",
            "arguments", "inputReferences", "deadlineAt",
        }
        if set(value) - {"composition"} != required:
            raise ValueError("JOB_REQUEST_INVALID")
        composition = value.get("composition")
        if "composition" in value and not isinstance(composition, dict):
            raise ValueError("JOB_REQUEST_INVALID")
        if composition is not None:
            _validate_json(composition, "JOB_REQUEST_INVALID")
        for field_name in required - {"arguments", "inputReferences"}:
            _require_string(value[field_name], "JOB_REQUEST_INVALID")
        for field_name in ("modelChecksum", "workflowChecksum"):
            if re.fullmatch(r"sha256:[0-9a-f]{64}", str(value[field_name])) is None:
                raise ValueError("JOB_REQUEST_INVALID")
        arguments = value["arguments"]
        references = value["inputReferences"]
        if not isinstance(arguments, dict) or not isinstance(references, dict):
            raise ValueError("JOB_REQUEST_INVALID")
        if any(not isinstance(name, str) or not name or not isinstance(ref, dict)
               for name, ref in references.items()):
            raise ValueError("JOB_REQUEST_INVALID")
        _validate_json(arguments, "JOB_REQUEST_INVALID")
        deadline = str(value["deadlineAt"])
        if re.fullmatch(
            r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?"
            r"(?:Z|[+-](?:[01]\d|2[0-3]):[0-5]\d)", deadline,
        ) is None:
            raise ValueError("JOB_REQUEST_INVALID")
        try:
            datetime.fromisoformat(deadline.replace("Z", "+00:00")).astimezone(timezone.utc)
        except (ValueError, OverflowError) as cause:
            raise ValueError("JOB_REQUEST_INVALID") from cause
        return cls(
            request_id=str(value["requestId"]), project_id=str(value["projectId"]),
            model_id=str(value["modelId"]), model_version=str(value["modelVersion"]),
            model_checksum=str(value["modelChecksum"]), workflow_id=str(value["workflowId"]),
            workflow_version=str(value["workflowVersion"]),
            workflow_checksum=str(value["workflowChecksum"]),
            executor_id=str(value["executorId"]), arguments=dict(arguments),
            input_references={
                name: DataVersionRefV2.from_dict(ref) for name, ref in references.items()
            },
            deadline_at=deadline,
            composition=composition,
        )

    @classmethod
    def from_storage_dict(cls, value: Mapping[str, object]) -> JobRequestV6:
        keys = {
            "request_id": "requestId", "project_id": "projectId",
            "model_id": "modelId", "model_version": "modelVersion",
            "model_checksum": "modelChecksum", "workflow_id": "workflowId",
            "workflow_version": "workflowVersion",
            "workflow_checksum": "workflowChecksum", "executor_id": "executorId",
            "arguments": "arguments", "input_references": "inputReferences",
            "deadline_at": "deadlineAt",
            "composition": "composition",
        }
        if set(value) - {"traceparent", "composition"} != keys.keys() - {"composition"}:
            raise ValueError("JOB_REQUEST_INVALID")
        traceparent = value.get("traceparent")
        if traceparent is not None and not isinstance(traceparent, str):
            raise ValueError("JOB_REQUEST_INVALID")
        wire = {keys[key]: item for key, item in value.items() if key in keys}
        references = wire["inputReferences"]
        if isinstance(references, dict):
            wire["inputReferences"] = {
                name: DataVersionRefV2.from_storage_dict(ref).as_dict()
                if isinstance(ref, dict) else ref
                for name, ref in references.items()
            }
        request = cls.from_dict(wire)
        return cls(**{**request.__dict__, "traceparent": traceparent})


@dataclass(frozen=True)
class JobStatusV6:
    job_id: str
    request_id: str
    status: str
    stage: str | None = None
    error: str | None = None
    outputs: tuple[DataVersionRefV2, ...] = ()
    service_version: str | None = None
    created_at: str | None = None
    started_at: str | None = None
    finished_at: str | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "jobId": self.job_id, "requestId": self.request_id,
            "status": self.status, "stage": self.stage, "error": self.error,
            "outputs": [item.as_dict() for item in self.outputs],
            "serviceVersion": self.service_version, "createdAt": self.created_at,
            "startedAt": self.started_at, "finishedAt": self.finished_at,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> JobStatusV6:
        required = {
            "jobId", "requestId", "status", "stage", "error", "outputs",
            "serviceVersion", "createdAt", "startedAt", "finishedAt",
        }
        if set(value) != required:
            raise ValueError("JOB_STATUS_INVALID")
        for field_name in ("jobId", "requestId", "status"):
            _require_string(value[field_name], "JOB_STATUS_INVALID")
        if value["status"] not in {
            "QUEUED", "RUNNING", "SUCCEEDED", "FAILED", "CANCELLED", "TIMED_OUT",
        }:
            raise ValueError("JOB_STATUS_INVALID")
        for field_name in ("stage", "error", "serviceVersion", "createdAt", "startedAt", "finishedAt"):
            item = value[field_name]
            if item is not None:
                _require_string(item, "JOB_STATUS_INVALID")
        outputs = value["outputs"]
        if not isinstance(outputs, list) or any(not isinstance(item, dict) for item in outputs):
            raise ValueError("JOB_STATUS_INVALID")
        return cls(
            job_id=str(value["jobId"]), request_id=str(value["requestId"]),
            status=str(value["status"]), stage=value["stage"], error=value["error"],
            outputs=tuple(DataVersionRefV2.from_dict(item) for item in outputs),
            service_version=value["serviceVersion"], created_at=value["createdAt"],
            started_at=value["startedAt"], finished_at=value["finishedAt"],
        )


def _require_string(value: object, error: str) -> None:
    if not isinstance(value, str) or not value:
        raise ValueError(error)
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as cause:
        raise ValueError(error) from cause


def _require_utf8(value: str, error: str) -> None:
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as cause:
        raise ValueError(error) from cause


def _validate_json(value: object, error: str) -> None:
    def visit(item: object) -> None:
        if isinstance(item, str):
            _require_utf8(item, error)
        elif isinstance(item, float):
            if not math.isfinite(item):
                raise ValueError(error)
        elif isinstance(item, dict):
            for key, nested in item.items():
                if not isinstance(key, str):
                    raise ValueError(error)
                _require_utf8(key, error)
                visit(nested)
        elif isinstance(item, list):
            for nested in item:
                visit(nested)
        elif item is not None and not isinstance(item, (bool, int)):
            raise ValueError(error)

    visit(value)
    try:
        json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    except (TypeError, ValueError, UnicodeEncodeError) as cause:
        raise ValueError(error) from cause
