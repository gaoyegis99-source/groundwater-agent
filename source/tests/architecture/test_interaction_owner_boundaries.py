from __future__ import annotations

from pathlib import Path


SOURCE_ROOT = Path(__file__).resolve().parents[2]
OWNER = SOURCE_ROOT / "kernel/src/platform_kernel/interaction/owner.py"


def test_owner_composes_durable_decision_and_start_lifecycles() -> None:
    source = OWNER.read_text(encoding="utf-8")

    assert "DecisionExecutor(" in source
    assert "PendingAgentStartExecutor(" in source
    assert "ConfirmationExecutor(" in source
    assert "ControlProposalExecutor(" in source
    assert "WorkflowRecoveryExecutor(" in source
    assert "def _drive_decision" not in source
    assert "def _apply_agent_run" not in source
    assert "claim_control_proposal(" not in source
    assert "create_recovery_confirmation(" not in source
    assert "request_decision(" not in source


def test_command_components_do_not_import_the_owner_facade() -> None:
    root = OWNER.parent
    for name in (
        "confirmation_commands.py",
        "control_proposal_execution.py",
        "workflow_recovery_commands.py",
    ):
        source = (root / name).read_text(encoding="utf-8")
        assert "from .owner import" not in source
        assert "interaction.owner" not in source
