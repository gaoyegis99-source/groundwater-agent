"""Bounded exact-version table inspection through the existing query worker."""

from __future__ import annotations

import hashlib
import json
import time

from .catalog import service as catalog
from .errors import DataProblemError
from .models import Query
from .query import service as queries


class TableInspector:
    """Read a small table sample while retaining normal query sandboxing and audit."""

    sample_rows = 5

    def __init__(self, settings, application) -> None:
        self.settings = settings
        self.application = application

    def inspect(
        self, *, call_id: str, version_id: str, actor: dict | None = None
    ) -> dict:
        actor = actor or {
            "subject": "platform-kernel",
            "client": "agent",
            "roles": ["admin"],
            "trace_id": call_id,
        }
        with self.application.db.transaction() as connection:
            data = catalog.describe(
                connection, self.settings.project, version_id, actor,
            )
        if data["descriptor"].get("policy") != "FILE_QUERYABLE":
            return {
                "status": "NOT_QUERYABLE",
                "version_id": data["id"],
                "dataset_id": data["dataset_id"],
            }

        identity = hashlib.sha256(
            f"{call_id}\n{version_id}".encode("utf-8")
        ).hexdigest()[:48]
        request = Query(
            idempotency_key=f"inspect-sample-{identity}",
            version_ids=[version_id],
            semantic_ids={version_id: data["semantic_id"]},
        )
        with self.application.db.transaction() as connection:
            accepted = queries.submit(
                connection, self.settings.project, request, actor,
                row_limit=self.sample_rows,
            )

        deadline = time.monotonic() + min(25, self.settings.query_seconds)
        row = None
        while time.monotonic() < deadline:
            with self.application.db.transaction() as connection:
                row = queries.get(
                    connection, self.settings.project, accepted["query_id"],
                )
            if row["state"] in {"SUCCEEDED", "FAILED", "CANCELLED"}:
                break
            time.sleep(0.05)
        if row is None or row["state"] not in {"SUCCEEDED", "FAILED", "CANCELLED"}:
            raise DataProblemError("TABLE_INSPECTION_TIMEOUT", 503)
        if row["state"] != "SUCCEEDED" or not row["result_available"]:
            raise DataProblemError(
                "TABLE_INSPECTION_FAILED", 503,
                query_state=row["state"],
                query_error=row["error"],
            )
        with self.application.store.open(row["result_object"]) as source:
            result = json.load(source)
        rows = result.get("rows")
        fields = data["descriptor"].get("fields")
        if not isinstance(rows, list) or not isinstance(fields, list):
            raise DataProblemError("TABLE_INSPECTION_RESULT_INVALID", 500)
        return {
            "status": "OK",
            **data,
            "fields": fields,
            "sample_rows": rows[: self.sample_rows],
            "sample_truncated": bool(result.get("truncated"))
            or len(rows) > self.sample_rows,
            "sample_limit": self.sample_rows,
            "coverage": result["coverage"],
        }
