from dataclasses import replace
from types import SimpleNamespace

import pytest

from platform_kernel.work_items.lifecycle import (
    ResultLink,
    derive_track,
    summarize_tracks,
)


def facts(**overrides):
    return SimpleNamespace(**({
        "session_id": "session", "task": None, "preparation": None, "workflows": (),
        "evidence_workflows": (), "confirmation_id": None, "confirmation_decision_state": None,
        "rejected": False, "stop_pending": False, "agent_status": None, "agent_error": None,
        "stopped_before_start": False, "continuation_stop": None, "goal_revision": None,
    } | overrides))


def run(status, run_id="run", names=("output",)):
    return SimpleNamespace(run_id=run_id, status=status, output_names=names)


def task(*, kind="REPLY", satisfied=True, reviewed=False, unresolved=(), revision=1):
    criteria = [{"id": "deliver", "kind": kind, "output_names": ["output"] if kind == "RESULT" else []}]
    proof = {"criterion_id": "deliver", "agent_run_id": "agent", "workflow_run_id": "run"} if satisfied else None
    checks = [{"id": "deliver", "satisfied": satisfied, "evidence": proof}]
    if reviewed:
        criteria.append({"id": "review", "kind": "USER_REVIEW"})
        checks.append({
            "id": "review", "satisfied": True,
            "evidence": {"criterion_id": "review", "agent_run_id": "reviewer", "review_quote": "确认完成：交付"},
        })
    return {
        "spec": {"task_id": "task", "revision": revision, "criteria": criteria, "unresolved_items": list(unresolved)},
        "progress": {"criteria": checks, "steps": []},
    }


RESULT = ResultLink("asset", {
    "kind": "RESULT", "runId": "run", "outputName": "output",
    "publicationId": "publication", "dataRef": {"versionId": "version"},
})


@pytest.mark.parametrize(
    ("values", "state", "reason"),
    [
        ({}, "DRAFT", "NO_BUSINESS_TARGET"),
        ({"preparation": {"status": "READY", "entries": []}}, "READY", "PREPARATION_READY"),
        ({"preparation": {"status": "READY", "entries": [], "disposition": "ABANDONED"}},
         "DRAFT", "NO_BUSINESS_TARGET"),
        ({"task": task(satisfied=False)}, "READY", "TASK_READY"),
        ({"task": task(unresolved=("unit",))}, "WAITING_INPUT", "TASK_INPUT_REQUIRED"),
        ({"task": task(kind="USER_REVIEW", satisfied=False)}, "WAITING_REVIEW", "TASK_REVIEW_REQUIRED"),
        ({"task": task()}, "WAITING_REVIEW", "DELIVERY_REVIEW_REQUIRED"),
        ({"confirmation_id": "approval"}, "WAITING_CONFIRMATION", "CONFIRMATION_PENDING"),
        ({"confirmation_id": "approval", "confirmation_decision_state": "RUNNING"},
         "WAITING_CONFIRMATION", "CONFIRMATION_DECISION_PROCESSING"),
        ({"workflows": (run("RUNNING"),)}, "EXECUTING", "WORKFLOW_ACTIVE"),
        ({"workflows": (run("QUEUED"),), "stop_pending": True}, "EXECUTING", "STOP_REQUESTED"),
        ({"workflows": (run("FAILED"),)}, "BLOCKED", "WORKFLOW_FAILED"),
        ({"rejected": True}, "BLOCKED", "CONFIRMATION_REJECTED"),
        ({"rejected": True, "workflows": (run("FAILED"),)}, "BLOCKED", "CONFIRMATION_REJECTED"),
        ({"workflows": (run("CANCELLED"),)}, "CANCELLED", "WORKFLOW_CANCELLED"),
        ({"stopped_before_start": True}, "CANCELLED", "AGENT_STOPPED"),
        ({"agent_status": "CANCELLED", "agent_error": "AGENT_STOPPED"}, "CANCELLED", "AGENT_STOPPED"),
        ({"task": task(reviewed=True)}, "COMPLETED", "TASK_USER_REVIEWED"),
    ],
)
def test_business_state_table(values, state, reason):
    view = derive_track("work", facts(**values), (), {})
    assert (view.state, view.reason) == (state, reason)


def test_success_requires_published_output_then_exact_business_review():
    delivered = facts(task=task(kind="RESULT"), workflows=(run("SUCCEEDED"),))
    ready = derive_track("work", delivered, (), {"run": "PENDING"})
    assert (ready.state, ready.reason, ready.review) == ("BLOCKED", "PUBLICATION_PENDING", None)
    unknown = derive_track("work", delivered, (), {"run": "UNAVAILABLE"})
    assert (unknown.state, unknown.reason) == ("BLOCKED", "PUBLICATION_UNVERIFIED")
    selection = derive_track("work", delivered, (), {"run": "PUBLISHED"})
    assert (selection.state, selection.reason, selection.review) == ("RESULT_REVIEW", "RESULT_LINK_REQUIRED", None)

    proven = derive_track("work", delivered, (RESULT,), {})
    assert (proven.state, proven.reason) == ("RESULT_REVIEW", "RESULT_ACCEPTANCE_REQUIRED")
    assert proven.review.links == (RESULT,)
    replaced = replace(RESULT, reference=RESULT.reference | {"publicationId": "replacement"})
    assert derive_track("work", delivered, (replaced,), {}).review.token != proven.review.token
    revised = facts(task=task(kind="RESULT", revision=2), workflows=(run("SUCCEEDED"),))
    assert derive_track("work", revised, (RESULT,), {}).review.token != proven.review.token


def test_positive_delivery_wins_stop_and_identical_names_do_not_follow_a_retry():
    published = facts(workflows=(run("SUCCEEDED"),), agent_status="CANCELLED",
                      agent_error="AGENT_STOPPED", stop_pending=True)
    assert derive_track("work", published, (RESULT,), {}).state == "RESULT_REVIEW"
    recovered = facts(workflows=(run("SUCCEEDED", run_id="new-run"),))
    replayed = derive_track("work", recovered, (RESULT,), {"new-run": "PUBLISHED"})
    assert replayed.review is None and replayed.reason == "RESULT_LINK_REQUIRED"
    partial = facts(workflows=(run("CANCELLED", run_id="other"), run("SUCCEEDED")))
    assert derive_track("work", partial, (RESULT,), {}).reason == "PARTIAL_EXECUTION_CANCELLED"


def test_empty_workflow_outputs_are_not_a_deliverable():
    no_outputs = facts(workflows=(run("SUCCEEDED", names=()),))
    assert derive_track("work", no_outputs, (), {"run": "PUBLISHED"}).reason == "WORKFLOW_OUTPUTS_MISSING"


def test_consumed_earlier_input_variant_does_not_block_published_delivery():
    preparation = {
        "status": "BLOCKED",
        "disposition": "CONSUMED",
        "entries": [
            {"status": "BLOCKED", "blocking_issues": [{"kind": "MISSING_PARAMETER"}]},
            {"status": "READY", "blocking_issues": []},
        ],
    }
    delivered = facts(task=task(kind="RESULT"), preparation=preparation, workflows=(run("SUCCEEDED"),))
    current = derive_track("work", delivered, (RESULT,), {})
    assert current.state == "RESULT_REVIEW" and current.review is not None
    still_missing = facts(preparation=preparation)
    assert derive_track("work", still_missing, (), {}).state == "WAITING_INPUT"


@pytest.mark.parametrize("stop", [{"workflows": (run("CANCELLED"),)}, {"stopped_before_start": True}])
def test_terminal_stop_is_visible_even_when_an_unresolved_task_was_abandoned(stop):
    stopped = facts(task=task(unresolved=("missing input",)), **stop)
    assert derive_track("work", stopped, (), {}).state == "CANCELLED"


def test_aggregate_keeps_pending_work_and_archives_only_the_item():
    draft = derive_track("work", facts(), (), {})
    ready = derive_track("work", facts(task=task(satisfied=False)), (), {})
    complete = derive_track("work", facts(task=task(reviewed=True)), (), {})
    active = derive_track("work", facts(workflows=(run("RUNNING"),)), (), {})
    assert summarize_tracks([draft, complete], archived=False) == ("COMPLETED", "TASK_USER_REVIEWED")
    assert summarize_tracks([complete, ready], archived=False) == ("READY", "TASK_READY")
    assert summarize_tracks([draft, active, ready], archived=False) == ("EXECUTING", "WORKFLOW_ACTIVE")
    assert summarize_tracks([active], archived=True) == ("ARCHIVED", "WORK_ITEM_ARCHIVED")
