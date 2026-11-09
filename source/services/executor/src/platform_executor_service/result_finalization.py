from __future__ import annotations

import json
from pathlib import Path
import shutil

from .artifacts import ArtifactStore
from .contracts import JobRequestV6, canonical_json
from .data_client import DataClient, DataIntegrityError, DataUnavailableError
from .database import connect_database, utc_now
from .result_publication import PublicationSession, publication_contract


class PublicationFinalizer:
    """Own terminal commit and abort transitions for a Data publication."""

    def __init__(
        self,
        database: Path,
        artifacts: ArtifactStore,
        models: dict[tuple[str, str], dict],
        data_client: DataClient,
        catalog_digest: str,
    ) -> None:
        self.database = database
        self.artifacts = artifacts
        self.models = models
        self.data_client = data_client
        self.catalog_digest = catalog_digest

    def commit(self, job_id: str, request: JobRequestV6, request_digest: str) -> None:
        with connect_database(self.database) as connection:
            job = connection.execute(
                "SELECT *, julianday(deadline_at)<=julianday(?) AS overdue FROM executor_jobs WHERE job_id=?",
                (utc_now(), job_id),
            ).fetchone()
        if job["status"] != "RUNNING" or job["stage"] != "COMMIT_PENDING":
            return
        if job["error"] is not None or job["overdue"]:
            self.abort_commit_pending(
                job_id, job["error"] or "JOB_DEADLINE_EXCEEDED"
            )
            return
        publication = self.data_client.publication_status(job["publication_id"], request)
        if publication is None:
            raise DataUnavailableError("DATA_PUBLICATION_MISSING")
        state = publication.get("state")
        if state in {"OPEN", "PREPARING"}:
            raise DataUnavailableError("DATA_PUBLICATION_PREPARING")
        if state == "READY":
            receipt = self.data_client.commit_publication(job["publication_id"], request)
        elif state == "PUBLISHED":
            receipt = publication.get("receipt")
            if not isinstance(receipt, dict):
                raise DataUnavailableError("DATA_SERVICE_RESPONSE_INVALID")
        elif state in {"FAILED", "ABORTED"}:
            raise DataIntegrityError("DATA_PUBLICATION_" + state)
        else:
            raise DataUnavailableError("DATA_SERVICE_RESPONSE_INVALID")
        self.complete_committed(job, receipt)

    def complete_committed(self, job, receipt: dict) -> None:
        request = JobRequestV6.from_storage_dict(json.loads(job["request_json"]))
        model = self.models[(request.model_id, request.model_version)]
        declarations = {
            name: model["outputPublications"][name]
            for name in model["finalOutputs"]
        }
        outputs = self.data_client.output_references(receipt, request, declarations)
        with connect_database(self.database) as connection:
            connection.execute("BEGIN IMMEDIATE")
            changed = connection.execute(
                """UPDATE executor_jobs SET status='SUCCEEDED', stage='COMPLETE',
                       outputs_json=?, error=NULL, finished_at=?
                   WHERE job_id=? AND status='RUNNING' AND stage='COMMIT_PENDING'
                     AND commit_digest=?""",
                (
                    canonical_json([item.as_storage_dict() for item in outputs]).decode("utf-8"),
                    utc_now(), job["job_id"], job["commit_digest"],
                ),
            ).rowcount
            if changed != 1:
                raise ValueError("RESULT_PUBLICATION_STATE_INVALID")
            connection.execute(
                "DELETE FROM job_object_pins WHERE job_id=? AND purpose NOT LIKE 'prepare:%'",
                (job["job_id"],),
            )
            self.artifacts.prune_unpinned(connection)
        shutil.rmtree(self.artifacts.work_root / job["job_id"], ignore_errors=True)

    def abort_commit_pending(self, job_id: str, error: str) -> None:
        with connect_database(self.database) as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """UPDATE executor_jobs SET error=COALESCE(error, ?)
                   WHERE job_id=? AND status='RUNNING' AND stage='COMMIT_PENDING'""",
                (error, job_id),
            )
            job = connection.execute(
                "SELECT * FROM executor_jobs WHERE job_id=?", (job_id,)
            ).fetchone()
        if job["status"] != "RUNNING" or job["stage"] != "COMMIT_PENDING":
            return
        request = JobRequestV6.from_storage_dict(json.loads(job["request_json"]))
        current = self.data_client.publication_status(job["publication_id"], request)
        if current is None:
            raise DataUnavailableError("DATA_PUBLICATION_MISSING")
        if current["state"] == "PUBLISHED":
            receipt = current.get("receipt")
            if not isinstance(receipt, dict):
                raise DataUnavailableError("DATA_SERVICE_RESPONSE_INVALID")
            self.complete_committed(job, receipt)
            return
        if current["state"] != "ABORTED":
            current = self.data_client.abort_publication(job["publication_id"], request)
        if current.get("state") != "ABORTED":
            raise DataUnavailableError("DATA_PUBLICATION_ABORT_PENDING")
        with connect_database(self.database) as connection:
            connection.execute("BEGIN IMMEDIATE")
            status = {
                "JOB_CANCELLED": "CANCELLED",
                "JOB_DEADLINE_EXCEEDED": "TIMED_OUT",
            }.get(job["error"], "FAILED")
            changed = connection.execute(
                """UPDATE executor_jobs SET status=?, stage=?, outputs_json='[]', finished_at=?
                   WHERE job_id=? AND status='RUNNING' AND stage='COMMIT_PENDING'
                     AND commit_digest=?""",
                (
                    status, "RESULT" if status == "FAILED" else status,
                    utc_now(), job_id, job["commit_digest"],
                ),
            ).rowcount
            if changed != 1:
                raise ValueError("RESULT_ABORT_STATE_INVALID")
            connection.execute(
                "DELETE FROM job_object_pins WHERE job_id=? AND purpose NOT LIKE 'prepare:%'",
                (job_id,),
            )
            self.artifacts.prune_unpinned(connection)
        shutil.rmtree(self.artifacts.work_root / job_id, ignore_errors=True)

    def abort_finalizing(self, job_id: str, error: str) -> None:
        with connect_database(self.database) as connection:
            job = connection.execute(
                "SELECT * FROM executor_jobs WHERE job_id=?", (job_id,),
            ).fetchone()
        if job["stage"] != "FINALIZING" or job["status"] != "RUNNING":
            raise ValueError("RESULT_ABORT_STATE_INVALID")
        request = JobRequestV6.from_storage_dict(json.loads(job["request_json"]))
        publication_id = job["publication_id"]
        if publication_id is None:
            # Data may have committed begin_publication before the Executor
            # stored its ID. Resolve by the same Data idempotency key first;
            # a missing local ID is never proof that no publication exists.
            model = self.models[(request.model_id, request.model_version)]
            outputs, provenance = publication_contract(
                model, self.catalog_digest, request
            )
            try:
                publication_id, current = PublicationSession(
                    self.database, self.data_client
                ).ensure(job_id, request, None, outputs, provenance)
            except DataIntegrityError as error:
                raise DataUnavailableError(
                    "DATA_PUBLICATION_IDENTITY_UNRESOLVED"
                ) from error
        else:
            current = self.data_client.publication_status(publication_id, request)
        if current is None:
            raise DataUnavailableError("DATA_PUBLICATION_MISSING")
        if current["state"] == "PUBLISHED":
            raise DataUnavailableError("DATA_PUBLICATION_PUBLISHED_DURING_ABORT")
        if current["state"] != "ABORTED":
            aborted = self.data_client.abort_publication(publication_id, request)
            if aborted.get("state") != "ABORTED":
                raise DataUnavailableError("DATA_PUBLICATION_ABORT_PENDING")
        with connect_database(self.database) as connection:
            connection.execute("BEGIN IMMEDIATE")
            status = {
                "JOB_CANCELLED": "CANCELLED",
                "JOB_DEADLINE_EXCEEDED": "TIMED_OUT",
            }.get(error, "FAILED")
            changed = connection.execute(
                """UPDATE executor_jobs SET status=?, stage=?,
                       error=?,finished_at=?
                   WHERE job_id=? AND status='RUNNING' AND stage='FINALIZING'""",
                (
                    status,
                    "RESULT" if status == "FAILED" else status,
                    error, utc_now(), job_id,
                ),
            ).rowcount
            if changed != 1:
                raise ValueError("RESULT_ABORT_STATE_INVALID")
            connection.execute(
                "DELETE FROM job_object_pins WHERE job_id=? AND purpose NOT LIKE 'prepare:%'",
                (job_id,),
            )
            self.artifacts.prune_unpinned(connection)
