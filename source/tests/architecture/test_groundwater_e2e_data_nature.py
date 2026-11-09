from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / "tests/e2e/groundwater/model_lifecycle.py"


def _load_module():
    spec = importlib.util.spec_from_file_location(
        "groundwater_model_lifecycle_data_nature_test", MODULE_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def lifecycle():
    return _load_module()


@pytest.mark.parametrize(
    ("data_nature", "required_text"),
    (
        ("REAL_DERIVED_TEST_SAMPLE", "受治理真实来源的确定性衍生测试样本"),
        ("REAL_OBSERVED", "受治理真实观测资料"),
    ),
)
def test_real_data_messages_are_not_labelled_as_synthetic(
    lifecycle, data_nature: str, required_text: str
) -> None:
    message = lifecycle._execution_message(
        model_id="groundwater.monitoring-trend",
        tool="run_monitoring_trend",
        arguments={"versionId": "version-real"},
        data_nature=data_nature,
    )

    assert data_nature in message
    assert required_text in message
    assert "SYNTHETIC_TEST_ONLY" not in message
    assert "合成输入" not in message


def test_real_derived_publication_keeps_source_and_nature(lifecycle) -> None:
    output = lifecycle._publication_output(
        name="真实衍生地下水月表",
        format_="csv",
        media_type="text/csv",
        data_nature="REAL_DERIVED_TEST_SAMPLE",
        source="locked-groundwater-monthly-sample-v1",
    )

    assert output["tags"] == ["REAL_DERIVED_TEST_SAMPLE", "six-model-lifecycle"]
    assert output["semantics"]["source"] == "locked-groundwater-monthly-sample-v1"
    assert "SYNTHETIC_TEST_ONLY" not in output["semantics"]["limitations"]
    assert "合成" not in output["semantics"]["description"]


def test_synthetic_input_remains_explicit(lifecycle) -> None:
    message = lifecycle._execution_message(
        model_id="groundwater.summary",
        tool="run_summary",
        arguments={"versionId": "version-synthetic"},
        data_nature="SYNTHETIC_TEST_ONLY",
    )
    output = lifecycle._publication_output(
        name="固定种子样本",
        format_="csv",
        media_type="text/csv",
        data_nature="SYNTHETIC_TEST_ONLY",
        source="v11-six-model-lifecycle",
    )

    assert "SYNTHETIC_TEST_ONLY" in message
    assert "合成输入" in message
    assert output["semantics"]["limitations"] == ["SYNTHETIC_TEST_ONLY"]


@pytest.mark.parametrize("helper", ["message", "publication"])
def test_unknown_data_nature_is_rejected(lifecycle, helper: str) -> None:
    with pytest.raises(ValueError, match="DATA_NATURE_UNSUPPORTED"):
        if helper == "message":
            lifecycle._execution_message(
                model_id="groundwater.summary",
                tool="run_summary",
                arguments={},
                data_nature="UNCLASSIFIED",
            )
        else:
            lifecycle._publication_output(
                name="未分类输入",
                format_="csv",
                media_type="text/csv",
                data_nature="UNCLASSIFIED",
                source="unknown",
            )
