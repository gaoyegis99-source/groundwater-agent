from __future__ import annotations

import os
from pathlib import Path

import httpx

from .artifacts import ArtifactStore
from .control import ExecutorControl
from .database import initialize_database
from .data_client import DataClient
from .results import ResultPublisher
from .runtime import ExecutorRuntime
from .settings import definition_set_digest, load_executor_config


class ExecutorApplication:
    def __init__(
        self,
        config_path: Path,
        state_root: Path,
        object_root: Path,
        work_root: Path,
        data_base_url: str,
        project_id: str,
        client: httpx.Client | None = None,
    ) -> None:
        if not project_id:
            raise ValueError("PLATFORM_PROJECT_ID_REQUIRED")
        self.project_id = project_id
        state_root.mkdir(parents=True, exist_ok=True)
        self.database = state_root / "executor.sqlite3"
        self.object_root = object_root
        self.temp_root = work_root
        initialize_database(self.database)
        if client is None:
            self.client = httpx.Client(timeout=30.0, trust_env=False)
        else:
            self.client = client
        config = load_executor_config(config_path)
        self.product_id = config["productId"]
        self.definition_set_digest = definition_set_digest(config)
        artifacts = ArtifactStore(object_root, work_root)
        data = DataClient(data_base_url, self.client, project_id)
        self.control = ExecutorControl(
            self.database,
            artifacts,
            config,
            data,
            project_id,
        )
        publisher = ResultPublisher(
            self.database, artifacts, config, data
        )
        self.runtime = ExecutorRuntime(
            self.control,
            publisher,
            self.client if client is None else None,
        )


def create_application_from_environment() -> ExecutorApplication:
    config_path = os.getenv("EXECUTOR_CONFIG_PATH")
    if not config_path:
        raise ValueError("EXECUTOR_CONFIG_REQUIRED")
    return ExecutorApplication(
        Path(config_path),
        Path(os.environ["EXECUTOR_STATE_ROOT"]),
        Path(os.environ["EXECUTOR_OBJECT_ROOT"]),
        Path(os.environ["EXECUTOR_WORK_ROOT"]),
        os.environ["DATA_SERVICE_URL"],
        os.environ["PLATFORM_PROJECT_ID"],
    )
