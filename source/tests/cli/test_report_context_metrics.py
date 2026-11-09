from __future__ import annotations

from agents.testing import ScriptedModel
from platform_kernel.agent_runtime.context_policy import (
    ContextPolicyV1,
    ContextSourceSnapshotV1,
)
from platform_kernel.agent_runtime.memory import O200kTokenEstimator
from platform_kernel.agent_runtime.persistence import create_context_view
from platform_kernel.composition import PlatformApplication


from scripts.evaluation.report_context_metrics import build_report


def test_context_report_aggregates_only_numeric_manifest_data(kernel_settings):
    application = PlatformApplication(
        settings=kernel_settings(),
        model=ScriptedModel([]),
    )
    session = application.interaction.create_session().session.session_id
    request = application.interaction.message(
        session,
        "report-owner",
        "private report body",
        "DIALOGUE",
    )
    manifest = (
        ContextPolicyV1(O200kTokenEstimator())
        .route_view(
            ContextSourceSnapshotV1(
                session_id=session,
                goal_revision=1,
                message="private report body",
                turns=(),
            ),
            available_input_tokens=10_000,
            fixed_input_tokens=100,
        )
        .manifest()
    )
    create_context_view(
        application.database,
        request_agent_run_id=request["agent_run_id"],
        segment_id="route",
        request_ordinal=1,
        purpose="INTENT_ROUTING",
        manifest=manifest,
        status="REJECTED",
        error_code="AGENT_CONTEXT_TOO_LARGE",
    )

    report = build_report(application.database)

    assert report["database_schema_version"] == 34
    assert report["view_count"] == 1
    assert report["groups"][0]["statuses"] == {"REJECTED": 1}
    assert report["blocks"][0]["tokens"] >= 0
    assert "private report body" not in str(report)
