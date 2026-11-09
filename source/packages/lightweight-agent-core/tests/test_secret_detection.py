from __future__ import annotations

from collections import UserDict

import pytest
from platform_lightweight_agent import RunSnapshot, WorkflowValidationError
from platform_lightweight_agent.spec import contains_secret_name


@pytest.mark.parametrize(
    "value",
    (
        {"apiKey": "not persisted"},
        {"nested": [{"private-key": "not persisted"}]},
        {"serviceAuthorization": "not persisted"},
        UserDict({"token": "not persisted"}),
    ),
)
def test_secret_name_detection_keeps_camel_case_and_mapping_compatibility(
    value: object,
) -> None:
    assert contains_secret_name(value) is True


def test_secret_name_detection_accepts_ordinary_json_state_keys() -> None:
    assert (
        contains_secret_name(
            {
                "step_outputs": {"write": {"value": 1}},
                "actions": [{"action_id": "action-1", "status": "RESOLVED"}],
            }
        )
        is False
    )


@pytest.mark.parametrize("field", ("inputs", "state", "driver_bindings"))
@pytest.mark.parametrize(
    "secret",
    (
        {"apiKey": "hidden"},
        {"nested": [{"private-key": "hidden"}]},
        {"serviceAuthorization": "hidden"},
    ),
)
def test_snapshot_rejects_secret_fields_during_json_decode(
    field: str, secret: dict[str, object]
) -> None:
    sections = {"inputs": {}, "state": {}, "driver_bindings": {}}
    sections[field] = secret
    with pytest.raises(
        WorkflowValidationError, match="run checkpoints must not contain credentials"
    ):
        RunSnapshot(
            run_id="secret-check",
            workflow={},
            status="RUNNING",
            **sections,
        )


def test_snapshot_allows_ordinary_nested_state_fields() -> None:
    snapshot = RunSnapshot(
        run_id="ordinary-check",
        workflow={},
        inputs={"request": {"action_id": "a"}},
        state={"actions": [{"status": "RESOLVED", "output": {"value": 1}}]},
        status="RUNNING",
        driver_bindings={},
    )
    assert snapshot._state_copy() == snapshot.state
