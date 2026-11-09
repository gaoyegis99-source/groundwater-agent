from __future__ import annotations

import hashlib
import json
import os
from contextlib import contextmanager
from pathlib import Path
import secrets
import shutil
import stat
from threading import RLock

from .database import utc_now
from .errors import ExecutionServiceError


SYNC_INTERVAL_BYTES = 64 * 1024**2


class ArtifactStore:
    """Control-owned durable objects and disposable node workspaces."""

    def __init__(self, object_root: Path, work_root: Path) -> None:
        self.object_root = object_root
        self.work_root = work_root
        self._publication_lock = RLock()
        object_root.mkdir(parents=True, exist_ok=True)
        work_root.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def object_publication(self):
        with self._publication_lock:
            yield

    def input_snapshot(self, connection, node) -> dict[str, dict]:
        inputs = {}
        for port, binding in json.loads(node["input_bindings_json"]).items():
            if "workflowInput" in binding:
                row = connection.execute(
                    "SELECT o.* FROM job_object_pins p JOIN content_objects o ON o.checksum=p.checksum WHERE p.job_id=? AND p.purpose=?",
                    (node["job_id"], f"workflow-input:{binding['workflowInput']}"),
                ).fetchone()
                if row is None:
                    raise ExecutionServiceError("CHECKPOINT_MISSING")
                media_type, format_id, execution_metadata = self.workflow_input_metadata(
                    connection, node["job_id"], binding["workflowInput"], row
                )
                inputs[port] = {
                    "checksum": row["checksum"],
                    "size_bytes": row["size_bytes"],
                    "media_type": media_type,
                    "format": format_id,
                    "execution_metadata": execution_metadata,
                }
            else:
                ref = binding["nodeOutput"]
                upstream = connection.execute(
                    "SELECT outputs_json FROM job_nodes WHERE job_id=? AND node_id=?",
                    (node["job_id"], ref["nodeId"]),
                ).fetchone()
                outputs = {
                    item["port"]: item for item in json.loads(upstream["outputs_json"])
                }
                if ref["port"] not in outputs:
                    raise ExecutionServiceError("CHECKPOINT_MISSING")
                inputs[port] = outputs[ref["port"]]
        return inputs

    @staticmethod
    def workflow_input_metadata_purpose(name: str, item: dict) -> str:
        facts = json.dumps(
            {
                "media_type": item["media_type"],
                "format": item["format"],
                "execution_metadata": item.get("execution_metadata", {}),
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return f"workflow-input-metadata:{name.encode('utf-8').hex()}:{facts}"

    @staticmethod
    def workflow_input_metadata(
        connection, job_id: str, name: str, row
    ) -> tuple[str, str, dict]:
        prefix = f"workflow-input-metadata:{name.encode('utf-8').hex()}:"
        metadata = connection.execute(
            """SELECT purpose FROM job_object_pins WHERE job_id=? AND checksum=?
               AND substr(purpose, 1, ?) = ?""",
            (job_id, row["checksum"], len(prefix), prefix),
        ).fetchall()
        if len(metadata) != 1:
            raise ExecutionServiceError("CHECKPOINT_MISSING")
        facts = json.loads(metadata[0]["purpose"][len(prefix) :])
        if isinstance(facts, list) and len(facts) == 2:
            # Recovery compatibility for jobs admitted before execution metadata
            # became part of the durable input snapshot.
            return facts[0], facts[1], {}
        if not isinstance(facts, dict) or set(facts) != {
            "media_type", "format", "execution_metadata"
        } or not isinstance(facts["execution_metadata"], dict):
            raise ExecutionServiceError("CHECKPOINT_MISSING")
        return facts["media_type"], facts["format"], facts["execution_metadata"]

    def prepare_workspace(self, inputs: dict[str, dict], workspace: Path) -> None:
        (workspace / "inputs").mkdir(parents=True)
        (workspace / "outputs").mkdir()
        for port, item in inputs.items():
            digest, size = hashlib.sha256(), 0
            try:
                with (
                    self.object_path(item["checksum"]).open("rb") as source,
                    (workspace / "inputs" / port).open("xb") as target,
                ):
                    while chunk := source.read(1024 * 1024):
                        digest.update(chunk)
                        size += len(chunk)
                        target.write(chunk)
            except FileNotFoundError as error:
                raise ExecutionServiceError("CHECKPOINT_MISSING") from error
            if (
                size != item["size_bytes"]
                or "sha256:" + digest.hexdigest() != item["checksum"]
            ):
                raise ExecutionServiceError("CHECKPOINT_MISSING")

    @staticmethod
    def workspace_inputs(inputs: dict[str, dict], workspace: Path) -> dict:
        return {
            port: [
                {
                    "path": str(workspace / "inputs" / port),
                    "mediaType": item["media_type"],
                    "format": item["format"],
                    "metadata": item.get("execution_metadata", {}),
                }
            ]
            for port, item in inputs.items()
        }

    def stage_outputs(
        self, node, attempt, outputs: object, directory: Path
    ) -> list[dict]:
        contracts = {
            item["name"]: item
            for item in json.loads(node["output_contracts_json"])
            if item["kind"] == "artifact"
        }
        if (
            not isinstance(outputs, list)
            or len(outputs) != len(contracts)
            or any(not isinstance(item, dict) for item in outputs)
            or {item.get("port") for item in outputs} != set(contracts)
        ):
            raise ExecutionServiceError("NODE_OUTPUT_INVALID")
        workspace = self.attempt_workspace(attempt)
        staged = []
        for item in outputs:
            if set(item) != {
                "port",
                "path",
                "media_type",
                "format",
                "size_bytes",
                "checksum",
            }:
                raise ExecutionServiceError("NODE_OUTPUT_INVALID")
            contract = contracts[item["port"]]
            if (
                not isinstance(item["path"], str)
                or type(item["size_bytes"]) is not int
                or item["size_bytes"] < 0
                or not isinstance(item["checksum"], str)
                or {"mediaType": item["media_type"], "format": item["format"]}
                not in contract["formatPairs"]
            ):
                raise ExecutionServiceError("NODE_OUTPUT_INVALID")
            source = (workspace / item["path"]).resolve()
            if not source.is_relative_to(workspace.resolve()):
                raise ExecutionServiceError("NODE_OUTPUT_INVALID")
            descriptor = os.open(source, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
            try:
                status = os.fstat(descriptor)
                if (
                    not stat.S_ISREG(status.st_mode)
                    or status.st_size != item["size_bytes"]
                ):
                    raise ExecutionServiceError("NODE_OUTPUT_INVALID")
                with os.fdopen(descriptor, "rb", closefd=False) as stream:
                    stored = self.stage_stream(
                        stream,
                        item["checksum"],
                        item["size_bytes"],
                        item["media_type"],
                        item["format"],
                        directory,
                    )
            finally:
                os.close(descriptor)
            staged.append({**stored, "port": item["port"]})
        return staged

    def stage_stream(
        self,
        source,
        expected_checksum: str,
        expected_size: int,
        media_type: str,
        format_id: str,
        directory: Path,
    ) -> dict:
        digest, size, unsynced = hashlib.sha256(), 0, 0
        temporary = directory / (secrets.token_hex(16) + ".part")
        with temporary.open("xb") as target:
            while chunk := source.read(1024 * 1024):
                size += len(chunk)
                unsynced += len(chunk)
                digest.update(chunk)
                target.write(chunk)
                if unsynced >= SYNC_INTERVAL_BYTES:
                    target.flush()
                    os.fsync(target.fileno())
                    unsynced = 0
            target.flush()
            os.fsync(target.fileno())
        checksum = "sha256:" + digest.hexdigest()
        if checksum != expected_checksum or size != expected_size:
            raise ExecutionServiceError("OBJECT_INTEGRITY_FAILED")
        return {
            "checksum": checksum,
            "size_bytes": size,
            "staged_path": str(temporary),
            "media_type": media_type,
            "format": format_id,
        }

    def publish_object(self, item: dict) -> None:
        destination = self.object_path(item["checksum"])
        destination.parent.mkdir(parents=True, exist_ok=True)
        os.replace(item["staged_path"], destination)
        for path in (destination.parent, self.object_root):
            descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)

    def record_object(self, connection, item: dict) -> None:
        destination = self.object_path(item["checksum"])
        connection.execute(
            """INSERT INTO content_objects(checksum, size_bytes, relative_path, created_at)
               VALUES (?, ?, ?, ?) ON CONFLICT(checksum) DO NOTHING""",
            (
                item["checksum"],
                item["size_bytes"],
                str(destination.relative_to(self.object_root)),
                utc_now(),
            ),
        )

    def object_path(self, checksum: str) -> Path:
        digest = checksum.removeprefix("sha256:")
        return self.object_root / digest[:2] / digest[2:]

    def prune_unpinned(self, connection) -> None:
        """Retire unpinned rows; reconcile collects files only after this commits."""
        connection.execute(
            """DELETE FROM content_objects WHERE NOT EXISTS (
                   SELECT 1 FROM job_object_pins p
                   WHERE p.checksum=content_objects.checksum
               )"""
        )

    def prune_orphan_files(self, connection) -> int:
        expected = {
            row["relative_path"]
            for row in connection.execute("SELECT relative_path FROM content_objects")
        }
        removed = 0
        for directory in self.object_root.iterdir():
            if directory.is_dir() and len(directory.name) == 2:
                for path in directory.iterdir():
                    if str(path.relative_to(self.object_root)) not in expected:
                        path.unlink()
                        removed += 1
        return removed

    def recover_workspaces(self) -> int:
        # Control startup precedes HTTP admission. Workspace bytes are never a
        # checkpoint, including partially renamed workspaces from rolled-back claims.
        self.work_root.mkdir(parents=True, exist_ok=True)
        removed = sum(
            1 for path in self.work_root.glob("*/*/*") if path.is_dir()
        )
        for child in self.work_root.iterdir():
            if child.is_dir():
                shutil.rmtree(child)
            else:
                child.unlink()
        staging = self.object_root / ".staging"
        if staging.exists():
            shutil.rmtree(staging)
        for pattern in (".inputs-*", ".outputs-*", ".result-*"):
            for temporary in self.object_root.glob(pattern):
                shutil.rmtree(temporary)
        for temporary in self.object_root.glob("*.part"):
            temporary.unlink()
        return removed

    def attempt_workspace(self, attempt) -> Path:
        return (
            self.work_root
            / attempt["job_id"]
            / attempt["node_id"]
            / attempt["lease_id"]
        )

    def remove_attempt_workspace(self, attempt) -> bool:
        path = self.attempt_workspace(attempt)
        existed = path.exists() or path.is_symlink()
        if existed:
            shutil.rmtree(path, ignore_errors=True)
        return existed and not path.exists() and not path.is_symlink()
