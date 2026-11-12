from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .artifacts import ArtifactStore
from .contracts import JobRequestV6, canonical_json
from .data_client import DataClient, DataUnavailableError
from .database import connect_database, utc_now
from .orchestration import NodeSnapshot, WorkflowSnapshot, finalization_ready
from .result_finalization import PublicationFinalizer
from .result_publication import PublicationSession, PublicationUpload, publication_contract
from .result_recovery import ResultRecovery


class PublicationOrchestrator:
    """Coordinate publication preparation without owning terminal transitions."""

    def __init__(
        self,
        database: Path,
        artifacts: ArtifactStore,
        config: dict,
        data_client: DataClient,
        finalizer: PublicationFinalizer,
    ) -> None:
        self.database = database
        self.artifacts = artifacts
        self.models = {
            (item["modelId"], item["version"]): item for item in config["models"]
        }
        self.catalog_digest = config["operatorCatalogDigest"]
        self.data_client = data_client
        self.session = PublicationSession(database, data_client)
        self.upload = PublicationUpload(artifacts, data_client)
        self.finalizer = finalizer

    def finalization_ready(self, job) -> bool:
        with connect_database(self.database) as connection:
            rows = connection.execute(
                "SELECT node_id, status, dependencies_json FROM job_nodes WHERE job_id=?",
                (job["job_id"],),
            ).fetchall()
        snapshot = WorkflowSnapshot(
            status=job["status"],
            stage=job["stage"],
            nodes=tuple(
                NodeSnapshot(
                    row["node_id"],
                    row["status"],
                    tuple(json.loads(row["dependencies_json"])),
                )
                for row in rows
            ),
        )
        return finalization_ready(snapshot)

    def publish(self, job) -> None:
        request = JobRequestV6.from_storage_dict(json.loads(job["request_json"]))
        model = self.models[(request.model_id, request.model_version)]
        job_id = job["job_id"]
        request_digest = self._request_digest(request)
        if job["stage"] == "DAG":
            with connect_database(self.database) as connection:
                changed = connection.execute(
                    """UPDATE executor_jobs SET stage='FINALIZING'
                       WHERE job_id=? AND status='RUNNING' AND stage='DAG'
                         AND cancel_requested=0 AND julianday(deadline_at)>julianday(?)""",
                    (job_id, utc_now()),
                ).rowcount
            if changed != 1:
                return
        with connect_database(self.database) as connection:
            current = connection.execute(
                """SELECT *, julianday(deadline_at)<=julianday(?) AS overdue
                   FROM executor_jobs WHERE job_id=?""",
                (utc_now(), job_id),
            ).fetchone()
        if current["status"] != "RUNNING":
            return
        if current["cancel_requested"] or current["overdue"]:
            error = "JOB_CANCELLED" if current["cancel_requested"] else "JOB_DEADLINE_EXCEEDED"
            if current["stage"] == "COMMIT_PENDING":
                self.finalizer.abort_commit_pending(job_id, error)
            else:
                self.finalizer.abort_finalizing(job_id, error)
            return

        declarations = {
            name: model["outputPublications"][name]
            for name in model["finalOutputs"]
        }
        outputs, provenance = publication_contract(model, self.catalog_digest, request)
        publication_id, publication = self.session.ensure(
            job_id, request, current["publication_id"], outputs, provenance,
        )
        state = publication.get("state")
        if state == "OPEN":
            self.upload.stage(
                publication_id, request, self._selected_outputs(job_id, model),
                declarations,
            )
            self.data_client.prepare_publication(publication_id, request)
            state = "PREPARING"
        commit_digest = "sha256:" + hashlib.sha256(canonical_json({
            "publication_id": publication_id,
            "request_digest": request_digest,
            "outputs": outputs,
        })).hexdigest()
        with connect_database(self.database) as connection:
            connection.execute(
                """UPDATE executor_jobs SET stage='COMMIT_PENDING', commit_digest=?
                   WHERE job_id=? AND status='RUNNING' AND stage IN ('FINALIZING','COMMIT_PENDING')""",
                (commit_digest, job_id),
            )
        if state == "PREPARING":
            raise DataUnavailableError("DATA_PUBLICATION_PREPARING")
        self.finalizer.commit(job_id, request, request_digest)

    def _selected_outputs(self, job_id: str, model: dict) -> list[dict]:
        outputs = {
            item["name"]: item["nodeOutput"] for item in model["workflow"]["outputs"]
        }
        selected = []
        with connect_database(self.database) as connection:
            for name in model["finalOutputs"]:
                ref = outputs[name]
                node = connection.execute(
                    "SELECT outputs_json,values_json FROM job_nodes WHERE job_id=? AND node_id=?",
                    (job_id, ref["nodeId"]),
                ).fetchone()
                artifacts = {
                    item["port"]: item for item in json.loads(node["outputs_json"])
                }
                if ref["port"] in artifacts:
                    item = artifacts[ref["port"]]
                    selected.append({
                        "name": name, "node_id": ref["nodeId"], "port": ref["port"],
                        "checksum": item["checksum"], "size_bytes": item["size_bytes"],
                        "media_type": item["media_type"], "format": item["format"],
                        "content": None,
                    })
                else:
                    content = canonical_json(
                        json.loads(node["values_json"])[ref["port"]]
                    )
                    selected.append({
                        "name": name, "node_id": ref["nodeId"], "port": ref["port"],
                        "checksum": "sha256:" + hashlib.sha256(content).hexdigest(),
                        "size_bytes": len(content), "media_type": "application/json",
                        "format": "json", "content": content,
                    })
        return selected

    @staticmethod
    def _request_digest(request: JobRequestV6) -> str:
        return "sha256:" + hashlib.sha256(
            canonical_json(request.as_storage_dict())
        ).hexdigest()


class ResultPublisher:
    """Stable facade for recovering and publishing ready Job results."""

    def __init__(
        self,
        database: Path,
        artifacts: ArtifactStore,
        config: dict,
        data_client: DataClient,
    ) -> None:
        models = {
            (item["modelId"], item["version"]): item for item in config["models"]
        }
        self.finalizer = PublicationFinalizer(
            database, artifacts, models, data_client, config["operatorCatalogDigest"]
        )
        self.orchestrator = PublicationOrchestrator(
            database, artifacts, config, data_client, self.finalizer
        )
        self.recovery = ResultRecovery(
            database, self.orchestrator, self.finalizer
        )

    def publish_ready(self) -> None:
        self.recovery.publish_ready()
