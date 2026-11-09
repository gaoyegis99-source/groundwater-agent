from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


@pytest.fixture
def client(tmp_path):
    settings = Settings(
        data_dir=tmp_path,
        database_path=tmp_path / "tasks.sqlite3",
        results_dir=tmp_path / "results",
        log_dir=tmp_path / "logs",
        log_level="WARNING",
    )
    with TestClient(create_app(settings)) as test_client:
        test_client.headers["Authorization"] = f"Bearer {settings.api_key}"
        yield test_client


@pytest.fixture
def valid_payload():
    return {
        "context": {
            "evaluation_unit": "示例评价单元A",
            "aquifer": "示例含水层I",
            "scenario": "现状年方案",
        },
        "qa": 800000,
        "qe": 1000000,
        "qa_unit": "m3/a",
        "qe_unit": "m3/a",
        "time_basis": "annual_average",
    }
