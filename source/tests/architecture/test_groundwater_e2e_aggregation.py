from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / "tests/e2e/groundwater/model_lifecycle.py"


def _module():
    spec = importlib.util.spec_from_file_location(
        "groundwater_model_lifecycle_aggregation_test", MODULE_PATH
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_independent_model_failures_are_aggregated(monkeypatch):
    module = _module()
    attempted = []

    def execute(_client, _capabilities, *, model_id, **_kwargs):
        attempted.append(model_id)
        if model_id == "model-b":
            raise RuntimeError("MODEL_B_BROKEN")
        return {
            "model": model_id,
            "workflow_run_id": f"run-{model_id}",
            "status": "PASS",
        }

    monkeypatch.setattr(module, "_execute", execute)
    cases = [
        ("model-a", "tool-a", {}, set(), 1),
        ("model-b", "tool-b", {}, set(), 1),
        ("model-c", "tool-c", {}, set(), 1),
    ]

    reports = module._run_model_cases(object(), {}, cases)

    assert attempted == ["model-a", "model-b", "model-c"]
    assert [report["status"] for report in reports] == ["PASS", "FAIL", "PASS"]
    assert reports[1]["error_type"] == "RuntimeError"
    assert reports[1]["error"] == "MODEL_B_BROKEN"
    assert all("elapsed_seconds" in report for report in reports)
