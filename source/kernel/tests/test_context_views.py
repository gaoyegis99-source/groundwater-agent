from __future__ import annotations

import pytest

from agents.testing import ScriptedModel
from platform_kernel.agent_runtime.context_policy import (
    ContextPolicyV1,
    ContextSourceSnapshotV1,
)
from platform_kernel.agent_runtime.memory import O200kTokenEstimator
from platform_kernel.agent_runtime.persistence import (
    create_context_view,
    finish_context_view,
    list_context_views,
)
from platform_kernel.composition import PlatformApplication


def _owner(kernel_settings):
    application = PlatformApplication(
        settings=kernel_settings(), model=ScriptedModel([]),
    )
    session = application.interaction.create_session().session.session_id
    request = application.interaction.message(
        session, "context-owner", "敏感正文不得进入清单", "DIALOGUE",
    )
    return application, request["agent_run_id"]


def _manifest():
    snapshot = ContextSourceSnapshotV1(
        session_id="session", goal_revision=1,
        message="敏感正文不得进入清单", turns=(),
    )
    return ContextPolicyV1(O200kTokenEstimator()).route_view(
        snapshot, available_input_tokens=10_000, fixed_input_tokens=100,
    ).manifest()


def test_context_manifest_is_body_free_and_terminal_update_is_idempotent(
    kernel_settings,
):
    application, run_id = _owner(kernel_settings)
    view_id = create_context_view(
        application.database,
        request_agent_run_id=run_id,
        segment_id="route",
        request_ordinal=1,
        purpose="INTENT_ROUTING",
        manifest=_manifest(),
    )
    finish_context_view(
        application.database, view_id,
        status="COMPLETE", actual_input_tokens=120,
    )
    finish_context_view(
        application.database, view_id,
        status="COMPLETE", actual_input_tokens=120,
    )

    row = list_context_views(application.database, run_id)[0]
    encoded = row["manifest_json"]
    assert row["status"] == "COMPLETE"
    assert row["actual_input_tokens"] == 120
    assert "敏感正文不得进入清单" not in encoded
    assert "content_hash" in encoded
    assert "content" not in row["manifest"]["blocks"][0]


def test_context_manifest_rejects_payload_fields(kernel_settings):
    application, run_id = _owner(kernel_settings)
    manifest = _manifest()
    manifest["blocks"][0]["content"] = "leak"
    with pytest.raises(ValueError, match="CONTEXT_MANIFEST_INVALID"):
        create_context_view(
            application.database,
            request_agent_run_id=run_id,
            segment_id="route",
            request_ordinal=1,
            purpose="INTENT_ROUTING",
            manifest=manifest,
        )

    for field, value in (
        ("source_id", "用户正文 不得藏入标识"),
        ("reason", "arbitrary model text"),
        ("kind", "USER_SUPPLIED_KIND"),
    ):
        invalid = _manifest()
        invalid["blocks"][0][field] = value
        with pytest.raises(ValueError, match="CONTEXT_MANIFEST_INVALID"):
            create_context_view(
                application.database,
                request_agent_run_id=run_id,
                segment_id="route",
                request_ordinal=1,
                purpose="INTENT_ROUTING",
                manifest=invalid,
            )


def test_context_view_rejected_state_is_terminal(kernel_settings):
    application, run_id = _owner(kernel_settings)
    view_id = create_context_view(
        application.database,
        request_agent_run_id=run_id,
        segment_id="route",
        request_ordinal=1,
        purpose="INTENT_ROUTING",
        manifest=_manifest(),
        status="REJECTED",
        error_code="AGENT_CONTEXT_TOO_LARGE",
    )
    row = list_context_views(application.database, run_id)[0]
    assert row["context_view_id"] == view_id
    assert row["status"] == "REJECTED"
    assert row["finished_at"] is not None
    with pytest.raises(ValueError, match="CONTEXT_VIEW_TRANSITION_INVALID"):
        finish_context_view(
            application.database, view_id,
            status="FAILED", error_code="AGENT_FAILED",
        )
