from __future__ import annotations

import hashlib
from pathlib import Path

import httpx
from platform_data_client import DataClient as PlatformDataClient
from platform_data_client.generated import models as data_models
from platform_data_client.generated import operations as data_operations
from platform_protocol_contracts import ApiProblem, ProtocolError, TransportError

from .contracts import DataVersionRefV2, JobRequestV6, canonical_json, sha256_bytes
from .errors import ExecutionServiceError, ExecutorUnavailableError


class DataUnavailableError(ExecutorUnavailableError):
    """The Data publication outcome is unknown and must be reconciled."""


class DataIntegrityError(ExecutionServiceError):
    """Data rejected bytes or an immutable publication contract."""


class DataClient:
    def __init__(self, origin: str, client: httpx.Client, project_id: str) -> None:
        self.api = PlatformDataClient(
            origin,
            project_id,
            writes=True,
            client_type="service",
            client=client,
            identity_headers={
                "X-Platform-Subject": "platform-executor",
                "X-Platform-Display-Name": "Platform Executor",
                "X-Platform-Roles": "admin",
            },
        )

    def read_exact(
        self, project_id: str, reference: DataVersionRefV2
    ) -> tuple[dict, StreamingBody]:
        self._require_project(project_id)
        try:
            value = self.api.read_version(reference.version_id).model_dump(
                mode="json", by_alias=True
            )
        except Exception as error:
            self._translate(error, "DATA_INPUT_REJECTED")
        objects = value["objects"]
        raw = [item for item in objects if item.get("role") == "raw"]
        if len(raw) != 1:
            raise ExecutionServiceError("DATA_VERSION_REFERENCE_MISMATCH")
        obj = raw[0]
        expected = reference.as_dict()
        expected.pop("outputName", None)
        actual = {
            "kind": "data_version",
            "service": "data-primary",
            "datasetId": value["datasetId"],
            "versionId": value["versionId"],
            "semanticId": value["semanticId"],
            "objectId": obj["objectId"],
            "mediaType": obj["mediaType"],
            "format": obj["format"],
            "sizeBytes": obj["size"],
            "checksum": obj["checksum"],
        }
        if actual != expected:
            raise ExecutionServiceError("DATA_VERSION_REFERENCE_MISMATCH")
        try:
            execution_metadata = _execution_metadata(value, obj)
        except (KeyError, TypeError, ValueError) as error:
            raise DataUnavailableError("DATA_SERVICE_RESPONSE_INVALID") from error
        try:
            stream = self.api.download_object(reference.version_id, reference.object_id)
        except Exception as error:
            self._translate(error, "DATA_INPUT_REJECTED")
        return {
            "media_type": reference.media_type,
            "format": reference.format_id,
            "size_bytes": reference.size_bytes,
            "checksum": reference.checksum,
            "execution_metadata": execution_metadata,
            "semantic_checksum": sha256_bytes(canonical_json(value["semantics"])),
            "raster_descriptor": value["descriptor"].get("raster"),
        }, StreamingBody(stream)

    def begin_publication(
        self,
        job_id: str,
        request: JobRequestV6,
        outputs: list[dict],
        provenance: dict[str, str],
    ) -> dict:
        self._require_project(request.project_id)
        body = {
            "idempotencyKey": "workflow-" + job_id,
            "outputs": [
                {
                    **item,
                    **(
                        {"mediaType": item["media_type"]}
                        if "media_type" in item
                        else {}
                    ),
                    "semantics": _semantics_to_wire(item.get("semantics", {})),
                }
                for item in outputs
            ],
            "inputVersions": list(
                dict.fromkeys(
                    reference.version_id
                    for reference in request.input_references.values()
                )
            ),
            "provenance": provenance,
        }
        for item in body["outputs"]:
            item.pop("media_type", None)
        try:
            model = data_models.BeginPublicationRequest.model_validate(body)
            return self.api.begin_publication(model).model_dump(
                mode="json", by_alias=True
            )
        except Exception as error:
            self._translate(error)

    def publication_status(
        self, publication_id: str, request: JobRequestV6
    ) -> dict | None:
        self._require_project(request.project_id)
        try:
            value = self.api.call(
                data_operations.DATA_GET_PUBLICATION,
                path={"publicationId": publication_id},
            )
            return value.model_dump(mode="json", by_alias=True)
        except ApiProblem as error:
            if error.problem.status == 404:
                return None
            self._translate(error)
        except Exception as error:
            self._translate(error)

    def stage_result(
        self,
        publication_id: str,
        request: JobRequestV6,
        output_name: str,
        checksum: str,
        path: Path,
    ) -> dict:
        self._require_project(request.project_id)
        expected, _size = _sha256_file(path)
        if checksum != expected:
            raise DataIntegrityError("DATA_OUTPUT_CHECKSUM_MISMATCH")
        try:
            with path.open("rb") as source:
                return self.api.upload(publication_id, output_name, source).model_dump(
                    mode="json", by_alias=True
                )
        except OSError as error:
            raise DataUnavailableError("DATA_SERVICE_UNAVAILABLE") from error
        except Exception as error:
            self._translate(error)

    def prepare_publication(self, publication_id: str, request: JobRequestV6) -> dict:
        self._require_project(request.project_id)
        try:
            return self.api.prepare_publication(publication_id).model_dump(
                mode="json", by_alias=True
            )
        except Exception as error:
            self._translate(error)

    def commit_publication(self, publication_id: str, request: JobRequestV6) -> dict:
        self._require_project(request.project_id)
        try:
            return self.api.commit_publication(publication_id).model_dump(
                mode="json", by_alias=True
            )
        except Exception as error:
            self._translate(error)

    def abort_publication(self, publication_id: str, request: JobRequestV6) -> dict:
        self._require_project(request.project_id)
        try:
            value = self.api.call(
                data_operations.DATA_ABORT_PUBLICATION,
                path={"publicationId": publication_id},
            )
            return value.model_dump(mode="json", by_alias=True)
        except Exception as error:
            self._translate(error)

    def output_references(
        self, receipt: dict, request: JobRequestV6, declarations: dict[str, dict]
    ) -> tuple[DataVersionRefV2, ...]:
        if receipt.get("state") != "PUBLISHED" or not isinstance(
            receipt.get("outputs"), list
        ):
            raise DataUnavailableError("DATA_SERVICE_RESPONSE_INVALID")
        receipt_outputs = receipt["outputs"]
        output_names = [
            output.get("name") for output in receipt_outputs
            if isinstance(output, dict)
        ]
        if (
            len(output_names) != len(receipt_outputs)
            or len(receipt_outputs) != len(declarations)
            or len(set(output_names)) != len(output_names)
            or set(output_names) != set(declarations)
        ):
            raise DataIntegrityError("DATA_OUTPUT_SET_MISMATCH")
        references = []
        for output in receipt_outputs:
            try:
                version = self.api.read_version(output["versionId"]).model_dump(
                    mode="json", by_alias=True
                )
            except Exception as error:
                self._translate(error)
            if any(
                version.get(key) != output.get(key)
                for key in ("datasetId", "versionId", "semanticId")
            ):
                raise DataIntegrityError("DATA_OUTPUT_IDENTITY_MISMATCH")
            raw = [item for item in version["objects"] if item.get("role") == "raw"]
            declaration = declarations[output["name"]]
            if len(raw) != 1:
                raise DataUnavailableError("DATA_SERVICE_RESPONSE_INVALID")
            obj = raw[0]
            try:
                reference = DataVersionRefV2.from_dict(
                    {
                        "kind": "data_version",
                        "service": "data-primary",
                        "datasetId": output["datasetId"],
                        "versionId": output["versionId"],
                        "semanticId": output["semanticId"],
                        "objectId": obj["objectId"],
                        "mediaType": obj["mediaType"],
                        "format": obj["format"],
                        "sizeBytes": obj["size"],
                        "checksum": obj["checksum"],
                        "outputName": output["name"],
                    }
                )
            except (KeyError, TypeError, ValueError) as error:
                raise DataUnavailableError("DATA_SERVICE_RESPONSE_INVALID") from error
            if (reference.format_id, reference.media_type) != (
                declaration["format"],
                declaration["mediaType"],
            ):
                raise DataIntegrityError("DATA_OUTPUT_CONTRACT_MISMATCH")
            references.append(reference)
        return tuple(references)

    def _require_project(self, project_id: str) -> None:
        if project_id != self.api.project:
            raise DataIntegrityError("DATA_PROJECT_MISMATCH")

    @staticmethod
    def _translate(error: Exception, fallback: str = "DATA_REQUEST_REJECTED"):
        if isinstance(error, ApiProblem):
            if error.problem.status >= 500 or error.problem.status in {408, 429}:
                raise DataUnavailableError("DATA_SERVICE_UNAVAILABLE") from error
            if error.problem.status == 409:
                raise DataIntegrityError(error.problem.code) from error
            raise ExecutionServiceError(fallback) from error
        if isinstance(error, TransportError):
            raise DataUnavailableError("DATA_SERVICE_UNAVAILABLE") from error
        if isinstance(error, (ProtocolError, ValueError, TypeError)):
            raise DataUnavailableError("DATA_SERVICE_RESPONSE_INVALID") from error
        raise error


def _sha256_file(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            size += len(chunk)
            digest.update(chunk)
    return "sha256:" + digest.hexdigest(), size


def _execution_metadata(version: dict, raw: dict) -> dict:
    descriptor = version["descriptor"]
    semantics = version["semantics"]
    if not isinstance(descriptor, dict) or not isinstance(semantics, dict):
        raise ValueError("DATA_EXECUTION_METADATA_INVALID")
    timezone_name = semantics.get("timezone")
    if timezone_name is not None and (
        not isinstance(timezone_name, str) or not timezone_name
    ):
        raise ValueError("DATA_EXECUTION_METADATA_INVALID")

    fields = descriptor.get("fields", [])
    if not isinstance(fields, list):
        raise ValueError("DATA_EXECUTION_METADATA_INVALID")
    projected_fields = []
    for item in fields:
        if not isinstance(item, dict):
            raise ValueError("DATA_EXECUTION_METADATA_INVALID")
        projected = {
            "fieldId": item.get("fieldId"),
            "name": item.get("name"),
            "type": item.get("type"),
            "nullable": item.get("nullable"),
        }
        if (
            any(not isinstance(projected[name], str) or not projected[name]
                for name in ("fieldId", "name", "type"))
            or not isinstance(projected["nullable"], bool)
        ):
            raise ValueError("DATA_EXECUTION_METADATA_INVALID")
        projected_fields.append(projected)

    null_values: list[str] = []
    types: dict[str, str] = {}
    conversion_object_id = None
    if descriptor.get("policy") == "FILE_QUERYABLE":
        query_objects = [
            item
            for item in version["objects"]
            if item.get("role") == "query"
            and item.get("sourceChecksum") == raw["checksum"]
        ]
        if len(query_objects) != 1:
            raise ValueError("DATA_EXECUTION_METADATA_INVALID")
        query_object = query_objects[0]
        conversion = query_object.get("conversion")
        if not isinstance(conversion, dict):
            raise ValueError("DATA_EXECUTION_METADATA_INVALID")
        candidate_nulls = conversion.get("nullValues", [])
        candidate_types = conversion.get("types", {})
        if (
            not isinstance(candidate_nulls, list)
            or any(not isinstance(item, str) for item in candidate_nulls)
            or not isinstance(candidate_types, dict)
            or any(
                not isinstance(key, str) or not isinstance(value, str)
                for key, value in candidate_types.items()
            )
        ):
            raise ValueError("DATA_EXECUTION_METADATA_INVALID")
        null_values = candidate_nulls
        types = candidate_types
        conversion_object_id = query_object.get("objectId")
        if not isinstance(conversion_object_id, str) or not conversion_object_id:
            raise ValueError("DATA_EXECUTION_METADATA_INVALID")

    return {
        "dataContract": {
            "datasetId": version["datasetId"],
            "versionId": version["versionId"],
            "semanticId": version["semanticId"],
            "objectId": raw["objectId"],
            "conversionObjectId": conversion_object_id,
            "timezone": timezone_name,
            "nullValues": null_values,
            "types": types,
            "fields": projected_fields,
        }
    }


def _semantics_to_wire(value: dict) -> dict:
    aliases = {
        "description": "description",
        "fields": "fields",
        "grain": "grain",
        "time_range": "timeRange",
        "timezone": "timezone",
        "crs": "crs",
        "vertical_datum": "verticalDatum",
        "limitations": "limitations",
        "source": "source",
    }
    return {aliases[name]: item for name, item in value.items() if name in aliases}


class StreamingBody:
    def __init__(self, chunks) -> None:
        self._source = chunks
        self._chunks = iter(chunks)
        self._pending = b""
        self._finished = False

    def read(self, size: int) -> bytes:
        if size <= 0 or self._finished and not self._pending:
            return b""
        while len(self._pending) < size and not self._finished:
            try:
                self._pending += next(self._chunks)
            except StopIteration:
                self._finished = True
            except (httpx.TransportError, TransportError) as error:
                raise ExecutorUnavailableError("DATA_SERVICE_UNAVAILABLE") from error
        result, self._pending = self._pending[:size], self._pending[size:]
        return result

    def close(self) -> None:
        close = getattr(self._source, "close", None)
        if close is not None:
            close()
