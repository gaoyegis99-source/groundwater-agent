from __future__ import annotations

from pathlib import Path


SOURCE_ROOT = Path(__file__).resolve().parents[2]
ROUTES = SOURCE_ROOT / "kernel/src/platform_kernel/api/routes"


def test_session_facade_only_registers_explicit_route_groups() -> None:
    source = (ROUTES / "sessions.py").read_text(encoding="utf-8")

    for route_group in (
        "query_router",
        "command_router",
        "workflow_router",
        "stream_router",
    ):
        assert f"router.include_router({route_group})" in source
    assert "@router." not in source
    assert "def _session_wire" not in source


def test_session_route_groups_do_not_import_the_facade() -> None:
    for name in (
        "session_queries.py",
        "session_commands.py",
        "session_workflows.py",
        "session_stream_routes.py",
    ):
        source = (ROUTES / name).read_text(encoding="utf-8")
        assert "from .sessions import" not in source
        assert "api.routes.sessions" not in source


def test_session_route_operation_ids_remain_owned_by_one_group() -> None:
    groups = {
        name: (ROUTES / name).read_text(encoding="utf-8")
        for name in (
            "session_queries.py",
            "session_commands.py",
            "session_workflows.py",
            "session_stream_routes.py",
        )
    }
    operation_ids = [
        "kernel_create_session",
        "kernel_list_sessions",
        "kernel_get_messages",
        "kernel_get_session",
        "kernel_get_session_preparation",
        "kernel_stop_run",
        "kernel_confirm_control_proposal",
        "kernel_dismiss_control_proposal",
        "kernel_get_task",
        "kernel_get_session_results",
        "kernel_create_message",
        "kernel_get_workflow_draft",
        "kernel_export_workflow_draft",
        "kernel_confirm",
        "kernel_revise_confirmation",
        "kernel_recover_workflow",
        "kernel_reject",
        "kernel_preview",
        "kernel_events",
    ]
    all_sources = tuple(groups.values())
    for operation_id in operation_ids:
        assert sum(operation_id in source for source in all_sources) == 1
