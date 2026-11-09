from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel


class TaskStatus(str, Enum):
    pending = "pending"
    running = "running"
    succeeded = "succeeded"
    failed = "failed"
    cancelled = "cancelled"


class ErrorDetail(BaseModel):
    code: str
    message: str
    details: Any | None = None
    request_id: str | None = None


class ErrorResponse(BaseModel):
    error: ErrorDetail


class TaskAccepted(BaseModel):
    task_id: str
    status: TaskStatus
    created_at: datetime
    status_url: str
    result_url: str
    download_url: str
    idempotent_replay: bool = False


class TaskView(BaseModel):
    task_id: str
    status: TaskStatus
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error: ErrorDetail | None = None
    result_url: str
    download_url: str


class CancellationResult(BaseModel):
    task_id: str
    status: TaskStatus
    cancellation_accepted: bool
    remote_termination_confirmed: bool
