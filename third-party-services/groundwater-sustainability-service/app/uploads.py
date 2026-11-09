from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
from threading import Lock
from typing import Any
from uuid import uuid4

from .errors import AppError


_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_ARTIFACT_ID = re.compile(r"^artifact-[0-9a-f]{32}$")
_UPLOAD_ID = re.compile(r"^upload-[0-9a-f]{32}$")


def canonical_json(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


class UploadStore:
    """Bounded local upload sessions for the common remote-operation protocol."""

    def __init__(self, root: Path, max_upload_bytes: int, part_bytes: int) -> None:
        self.root = root
        self.max_upload_bytes = max_upload_bytes
        self.part_bytes = part_bytes
        self._lock = Lock()
        (root / "sessions").mkdir(parents=True, exist_ok=True)
        (root / "artifacts").mkdir(parents=True, exist_ok=True)

    def create(self, metadata: dict[str, Any]) -> dict[str, Any]:
        required = {"name", "sizeBytes", "sha256", "mediaType", "format"}
        if set(metadata) != required:
            raise AppError("UPLOAD_METADATA_INVALID", "上传元数据不完整。", 422)
        size = metadata["sizeBytes"]
        if (
            not isinstance(metadata["name"], str)
            or not metadata["name"]
            or len(metadata["name"]) > 200
            or type(size) is not int
            or not 0 < size <= self.max_upload_bytes
            or not isinstance(metadata["sha256"], str)
            or _DIGEST.fullmatch(metadata["sha256"]) is None
            or not isinstance(metadata["mediaType"], str)
            or not metadata["mediaType"]
            or not isinstance(metadata["format"], str)
            or not metadata["format"]
        ):
            raise AppError("UPLOAD_METADATA_INVALID", "上传元数据无效。", 422)
        upload_id = "upload-" + uuid4().hex
        directory = self._session_directory(upload_id)
        directory.mkdir(mode=0o700)
        self._write_json_atomic(directory / "metadata.json", metadata)
        return {
            "uploadId": upload_id,
            "partSizeBytes": self.part_bytes,
            "sizeBytes": size,
            "sha256": metadata["sha256"],
        }

    def write_part(
        self,
        upload_id: str,
        part_number: int,
        body: bytes,
        checksum: str,
    ) -> dict[str, Any]:
        directory, metadata = self._load_session(upload_id)
        if (
            type(part_number) is not int
            or part_number < 0
            or not body
            or len(body) > self.part_bytes
            or _DIGEST.fullmatch(checksum) is None
            or "sha256:" + hashlib.sha256(body).hexdigest() != checksum
        ):
            raise AppError("UPLOAD_PART_INVALID", "上传分片无效。", 422)
        offset = part_number * self.part_bytes
        if offset >= metadata["sizeBytes"] or offset + len(body) > metadata["sizeBytes"]:
            raise AppError("UPLOAD_PART_RANGE_INVALID", "上传分片超出声明范围。", 416)
        expected_size = min(self.part_bytes, metadata["sizeBytes"] - offset)
        if len(body) != expected_size:
            raise AppError("UPLOAD_PART_SIZE_INVALID", "上传分片长度不正确。", 422)
        target = directory / f"part-{part_number:08d}"
        try:
            descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            existing = target.read_bytes()
            if existing != body:
                raise AppError("UPLOAD_PART_CONFLICT", "分片编号已用于不同内容。", 409)
            return {"uploadId": upload_id, "partNumber": part_number, "replayed": True}
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(body)
            stream.flush()
            os.fsync(stream.fileno())
        return {"uploadId": upload_id, "partNumber": part_number, "replayed": False}

    def complete(self, upload_id: str) -> dict[str, Any]:
        with self._lock:
            directory, metadata = self._load_session(upload_id)
            part_count = (metadata["sizeBytes"] + self.part_bytes - 1) // self.part_bytes
            parts = [directory / f"part-{index:08d}" for index in range(part_count)]
            if any(not part.is_file() or part.is_symlink() for part in parts):
                raise AppError("UPLOAD_INCOMPLETE", "上传分片尚未完整。", 409)
            artifact_id = "artifact-" + uuid4().hex
            target = self._artifact_path(artifact_id)
            temporary = target.with_suffix(".part")
            digest = hashlib.sha256()
            size = 0
            try:
                with temporary.open("xb") as output:
                    for part in parts:
                        with part.open("rb") as source:
                            while chunk := source.read(1024 * 1024):
                                size += len(chunk)
                                digest.update(chunk)
                                output.write(chunk)
                    output.flush()
                    os.fsync(output.fileno())
                actual = "sha256:" + digest.hexdigest()
                if size != metadata["sizeBytes"] or actual != metadata["sha256"]:
                    raise AppError("UPLOAD_CHECKSUM_MISMATCH", "上传文件校验失败。", 422)
                os.replace(temporary, target)
                stored = {"artifactId": artifact_id, **metadata}
                self._write_json_atomic(target.with_suffix(".json"), stored)
            finally:
                temporary.unlink(missing_ok=True)
            for part in parts:
                part.unlink()
            (directory / "metadata.json").unlink()
            directory.rmdir()
            return stored

    def resolve(self, artifact_id: str) -> tuple[dict[str, Any], Path]:
        if _ARTIFACT_ID.fullmatch(artifact_id) is None:
            raise AppError("ARTIFACT_NOT_FOUND", "上传文件不存在。", 404)
        target = self._artifact_path(artifact_id)
        metadata_path = target.with_suffix(".json")
        if not target.is_file() or target.is_symlink() or not metadata_path.is_file():
            raise AppError("ARTIFACT_NOT_FOUND", "上传文件不存在。", 404)
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise AppError("ARTIFACT_CORRUPT", "上传文件元数据损坏。", 500) from error
        return metadata, target

    def capabilities(self) -> dict[str, int]:
        return {
            "maxArtifactBytes": self.max_upload_bytes,
            "uploadPartBytes": self.part_bytes,
        }

    def _load_session(self, upload_id: str) -> tuple[Path, dict[str, Any]]:
        directory = self._session_directory(upload_id)
        metadata_path = directory / "metadata.json"
        if not directory.is_dir() or directory.is_symlink() or not metadata_path.is_file():
            raise AppError("UPLOAD_NOT_FOUND", "上传会话不存在。", 404)
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise AppError("UPLOAD_CORRUPT", "上传会话元数据损坏。", 500) from error
        return directory, metadata

    def _session_directory(self, upload_id: str) -> Path:
        if _UPLOAD_ID.fullmatch(upload_id) is None:
            raise AppError("UPLOAD_NOT_FOUND", "上传会话不存在。", 404)
        return self.root / "sessions" / upload_id

    def _artifact_path(self, artifact_id: str) -> Path:
        return self.root / "artifacts" / f"{artifact_id}.bin"

    @staticmethod
    def _write_json_atomic(path: Path, value: object) -> None:
        temporary = path.with_name("." + path.name + ".tmp")
        try:
            descriptor = os.open(
                temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600
            )
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(canonical_json(value))
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
