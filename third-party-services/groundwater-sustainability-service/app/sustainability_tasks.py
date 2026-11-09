from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from uuid import uuid4

from .config import Settings
from .errors import AppError
from .repository import SustainabilityTaskRepository
from .sustainability import (
    SustainabilityEvaluationResult,
    SustainabilityTaskInput,
    evaluate_sustainability,
)


LOGGER = logging.getLogger(__name__)


class SustainabilityTaskService:
    """Create, execute, and persist asynchronous full sustainability evaluations."""

    def __init__(self, settings: Settings, repository: SustainabilityTaskRepository):
        self.settings = settings
        self.repository = repository

    def create_task(self, payload: SustainabilityTaskInput) -> tuple[dict, bool]:
        request_data = payload.model_dump(mode="json", exclude_none=True)
        client_request_id = payload.client_request_id
        if client_request_id:
            existing = self.repository.get_by_client_request_id(client_request_id)
            if existing:
                if existing["input"] != request_data:
                    raise AppError(
                        "IDEMPOTENCY_CONFLICT",
                        "相同 client_request_id 已用于不同的请求内容。",
                        status_code=409,
                        details={"client_request_id": client_request_id},
                    )
                return existing, True

        task_id = str(uuid4())
        self.repository.create(task_id, client_request_id, request_data)
        task = self.repository.get(task_id)
        if task is None:
            raise RuntimeError("Sustainability task disappeared after creation")
        LOGGER.info("sustainability task created", extra={"task_id": task_id})
        return task, False

    def run_task(self, task_id: str) -> None:
        LOGGER.info("sustainability task started", extra={"task_id": task_id})
        if not self.repository.claim_pending(task_id):
            LOGGER.info(
                "sustainability task was not pending",
                extra={"task_id": task_id},
            )
            return
        task = self.repository.get(task_id)
        if task is None:
            LOGGER.error("sustainability task not found before execution", extra={"task_id": task_id})
            return
        try:
            payload = SustainabilityTaskInput.model_validate(task["input"])
            result = SustainabilityEvaluationResult.model_validate(
                evaluate_sustainability(payload)
            ).model_dump(mode="json", exclude_unset=True)
            output_path = self._write_result_atomically(task_id, result)
            self.repository.mark_succeeded(task_id, result, str(output_path))
            LOGGER.info("sustainability task succeeded", extra={"task_id": task_id})
        except ValueError as exc:
            self.repository.mark_failed(
                task_id,
                "SUSTAINABILITY_INPUT_INVALID",
                str(exc),
            )
            LOGGER.warning("sustainability task validation failed", extra={"task_id": task_id})
        except Exception:
            self.repository.mark_failed(
                task_id,
                "TASK_EXECUTION_FAILED",
                "综合评价任务执行失败。请使用任务ID查询服务日志。",
            )
            LOGGER.exception("sustainability task execution failed", extra={"task_id": task_id})

    def _write_result_atomically(self, task_id: str, result: dict) -> Path:
        target = self.settings.results_dir / f"sustainability-{task_id}.json"
        temporary = target.with_suffix(".json.tmp")
        with temporary.open("w", encoding="utf-8", newline="\n") as file:
            json.dump(result, file, ensure_ascii=False, indent=2)
            file.write("\n")
            file.flush()
            os.fsync(file.fileno())
        temporary.replace(target)
        return target.resolve()
