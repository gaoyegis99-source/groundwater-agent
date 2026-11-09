from __future__ import annotations

import logging
from pathlib import Path
from time import monotonic

from .data_client import DataIntegrityError, DataUnavailableError
from .database import connect_database
from .errors import ExecutionServiceError
from .result_finalization import PublicationFinalizer

logger = logging.getLogger(__name__)


class ResultRecovery:
    def __init__(
        self,
        database: Path,
        orchestrator,
        finalizer: PublicationFinalizer,
    ) -> None:
        self.database = database
        self.orchestrator = orchestrator
        self.finalizer = finalizer

    def publish_ready(self) -> None:
        with connect_database(self.database) as connection:
            jobs = connection.execute(
                """SELECT j.* FROM executor_jobs j
                   WHERE j.status='RUNNING' AND (
                       j.stage IN ('FINALIZING','COMMIT_PENDING') OR
                       (j.stage='DAG' AND NOT EXISTS (
                           SELECT 1 FROM job_nodes n WHERE n.job_id=j.job_id
                           AND n.status!='SUCCEEDED')))
                   ORDER BY j.created_at"""
            ).fetchall()
        for job in jobs:
            if job["stage"] == "DAG" and not self.orchestrator.finalization_ready(job):
                continue
            started = monotonic()
            try:
                self.orchestrator.publish(job)
            except DataUnavailableError as error:
                logger.warning(
                    "result publication pending",
                    extra={"job_id": job["job_id"], "stage": job["stage"],
                           "duration_ms": round((monotonic() - started) * 1000),
                           "error_code": str(error)},
                )
            except ExecutionServiceError as error:
                with connect_database(self.database) as connection:
                    current = connection.execute(
                        "SELECT stage FROM executor_jobs WHERE job_id=?", (job["job_id"],)
                    ).fetchone()
                if current["stage"] == "COMMIT_PENDING":
                    if not isinstance(error, DataIntegrityError):
                        raise
                    try:
                        self.finalizer.abort_commit_pending(job["job_id"], str(error))
                    except DataUnavailableError as pending:
                        logger.warning("result abort pending", extra={
                            "job_id": job["job_id"], "error_code": str(pending)
                        })
                    continue
                try:
                    self.finalizer.abort_finalizing(job["job_id"], str(error))
                except DataUnavailableError as pending:
                    logger.warning("result abort pending", extra={
                        "job_id": job["job_id"], "error_code": str(pending)
                    })
