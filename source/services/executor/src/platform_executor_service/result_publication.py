from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory

from .artifacts import ArtifactStore
from .contracts import JobRequestV6
from .data_client import DataClient, DataIntegrityError, DataUnavailableError, _sha256_file
from .database import connect_database
from .errors import ExecutionServiceError


def publication_contract(
    model: dict, catalog_digest: str, request: JobRequestV6
) -> tuple[list[dict], dict[str, str]]:
    """Recreate the exact idempotent Data request during forward or abort recovery."""

    outputs = [
        {
            "name": name,
            "title": declaration["title"],
            "format": declaration["format"],
            "media_type": declaration["mediaType"],
            "policy": declaration["policy"],
            "semantics": declaration["semantics"],
        }
        for name in model["finalOutputs"]
        for declaration in (model["outputPublications"][name],)
    ]
    provenance = {
        "model_id": request.model_id,
        "model_version": request.model_version,
        "model_checksum": request.model_checksum,
        "workflow_id": request.workflow_id,
        "workflow_version": request.workflow_version,
        "workflow_checksum": request.workflow_checksum,
        "operator_catalog_digest": catalog_digest,
    }
    return outputs, provenance


class PublicationSession:
    def __init__(self, database: Path, data_client: DataClient) -> None:
        self.database = database
        self.data_client = data_client

    def ensure(
        self,
        job_id: str,
        request: JobRequestV6,
        publication_id: str | None,
        outputs: list[dict],
        provenance: dict[str, str],
    ) -> tuple[str, dict]:
        if publication_id is None:
            publication = self.data_client.begin_publication(
                job_id, request, outputs, provenance
            )
            publication_id = publication.get("publicationId")
            if not isinstance(publication_id, str) or not publication_id:
                raise DataUnavailableError("DATA_SERVICE_RESPONSE_INVALID")
            with connect_database(self.database) as connection:
                connection.execute(
                    "UPDATE executor_jobs SET publication_id=? "
                    "WHERE job_id=? AND publication_id IS NULL",
                    (publication_id, job_id),
                )
        publication = self.data_client.publication_status(publication_id, request)
        if publication is None:
            raise DataUnavailableError("DATA_PUBLICATION_MISSING")
        return publication_id, publication


class PublicationUpload:
    def __init__(self, artifacts: ArtifactStore, data_client: DataClient) -> None:
        self.artifacts = artifacts
        self.data_client = data_client

    def stage(
        self,
        publication_id: str,
        request: JobRequestV6,
        selected: list[dict],
        declarations: dict[str, dict],
    ) -> None:
        with TemporaryDirectory(
            dir=self.artifacts.object_root, prefix=".result-"
        ) as temporary:
            for index, item in enumerate(selected):
                declaration = declarations[item["name"]]
                if (item["format"], item["media_type"]) != (
                    declaration["format"], declaration["mediaType"]
                ):
                    raise DataIntegrityError("DATA_OUTPUT_CONTRACT_MISMATCH")
                if item["content"] is None:
                    path = self.artifacts.object_path(item["checksum"])
                    try:
                        identity = _sha256_file(path)
                    except OSError as error:
                        raise ExecutionServiceError("CHECKPOINT_MISSING") from error
                    if identity != (item["checksum"], item["size_bytes"]):
                        raise ExecutionServiceError("CHECKPOINT_MISSING")
                else:
                    path = Path(temporary) / f"value-{index}.json"
                    path.write_bytes(item["content"])
                self.data_client.stage_result(
                    publication_id, request, item["name"], item["checksum"], path
                )
