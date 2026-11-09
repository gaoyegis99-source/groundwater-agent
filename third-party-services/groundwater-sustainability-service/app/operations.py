from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RemoteArtifactReference(StrictModel):
    artifact_id: str = Field(alias="artifactId", pattern=r"^artifact-[0-9a-f]{32}$")
    media_type: str = Field(alias="mediaType", min_length=1)
    format: str = Field(min_length=1)
    size_bytes: int = Field(alias="sizeBytes", ge=1)
    sha256: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")


class OperationRequest(StrictModel):
    operation_type: Literal["sustainability.evaluate"] = Field(alias="operationType")
    client_request_id: str = Field(
        alias="clientRequestId", min_length=1, max_length=128,
        pattern=r"^[A-Za-z0-9._:-]+$",
    )
    inputs: dict[str, RemoteArtifactReference]
    parameters: dict[str, Any]


class UploadCreateRequest(StrictModel):
    name: str = Field(min_length=1, max_length=200)
    size_bytes: int = Field(alias="sizeBytes", ge=1)
    sha256: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    media_type: str = Field(alias="mediaType", min_length=1)
    format: str = Field(min_length=1)
