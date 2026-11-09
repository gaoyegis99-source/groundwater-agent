from __future__ import annotations

from pathlib import Path


SOURCE_ROOT = Path(__file__).resolve().parents[2]
SDK = SOURCE_ROOT / "kernel/src/platform_kernel/agent_runtime/sdk_execution"


def test_usage_capture_receives_one_typed_ledger_not_optional_callbacks() -> None:
    capture = (SDK / "usage_capture.py").read_text(encoding="utf-8")
    dependencies = (SDK / "types.py").read_text(encoding="utf-8")
    ports = (SDK / "usage_ports.py").read_text(encoding="utf-8")

    assert "usage_ledger: UsageLedgerPort | None" in capture
    assert "usage_ledger: UsageLedgerPort | None" in dependencies
    assert "activity_recorder: ActivityRecorder" in capture
    assert "activity_log: ActivityLogPort" in dependencies
    assert "class UsageLedgerPort" in ports
    assert "class ActivityLogPort" in ports
    assert "class ActivityRecorder" in ports
    assert "class SQLiteUsageLedger" in ports
    for callback in (
        "create_context_view:",
        "finish_context_view:",
        "begin_provider_request:",
        "finish_provider_request:",
        "mark_provider_protocol_failure:",
        "record_activity:",
    ):
        assert callback not in capture
        assert callback not in dependencies


def test_usage_ports_do_not_depend_on_intent_router_or_prompt_bodies() -> None:
    source = (SDK / "usage_ports.py").read_text(encoding="utf-8")

    assert "intent_routing" not in source
    assert "response.output" not in source
    assert "input_items" not in source


def test_usage_capture_delegates_output_validation_repair_and_settlement() -> None:
    capture = (SDK / "usage_capture.py").read_text(encoding="utf-8")

    assert "from .usage_output_validation import" in capture
    assert "from .usage_protocol_repair import rejected_call_repair_input" in capture
    assert "from .usage_settlement import settle_output" in capture
    assert "def _rejected_call_repair_input" not in capture
    for name in (
        "usage_output_validation.py",
        "usage_protocol_repair.py",
        "usage_settlement.py",
    ):
        source = (SDK / name).read_text(encoding="utf-8")
        assert "from .usage_capture import" not in source
