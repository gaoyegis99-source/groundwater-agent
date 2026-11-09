from __future__ import annotations

from pathlib import Path


SOURCE_ROOT = Path(__file__).resolve().parents[2]


ROUTER = SOURCE_ROOT / "kernel/src/platform_kernel/intent_routing/router.py"


def test_router_delegates_context_provider_and_result_algorithms() -> None:
    source = ROUTER.read_text(encoding="utf-8")

    assert "compile_route_context(" in source
    assert "execute_route_attempts(" in source
    assert "run_provider_call(" not in source
    assert "Runner.run(" not in source
    assert "ContextSourceSnapshotV1(" not in source
    assert "_semantic_literal_decision(" not in source


def test_router_algorithm_modules_do_not_import_the_router_facade() -> None:
    root = ROUTER.parent
    for name in (
        "route_context.py",
        "provider_call.py",
        "result_mapping.py",
        "route_execution.py",
    ):
        source = (root / name).read_text(encoding="utf-8")
        assert "intent_routing.router" not in source
        assert "from .router import" not in source


def test_generation_transport_is_single_attempt_and_protocol_repair_is_bounded() -> None:
    root = ROUTER.parent
    facade = ROUTER.read_text(encoding="utf-8")
    execution = (root / "route_execution.py").read_text(encoding="utf-8")
    availability = (
        SOURCE_ROOT
        / "kernel/src/platform_kernel/agent_runtime/availability.py"
    ).read_text(encoding="utf-8")

    assert "for provider_attempt" not in facade
    assert "for protocol_attempt" not in facade
    assert "for provider_attempt" not in execution
    assert "for protocol_attempt in (1, 2)" in execution
    assert "refresh_readiness" not in execution
    assert "for attempt in (1, 2)" not in availability
    assert availability.count("attempt=1, endpoint=endpoint") == 4
