"""Project business status from the owning services' facts."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal, TYPE_CHECKING

from platform_protocol_contracts import canonical_json, sha256_bytes

if TYPE_CHECKING:
    from ..application.work_item_lifecycle_facts import SessionFacts, WorkflowFact


BusinessState = Literal[
    "DRAFT", "READY", "WAITING_INPUT", "WAITING_REVIEW", "WAITING_CONFIRMATION",
    "EXECUTING", "RESULT_REVIEW", "BLOCKED", "COMPLETED", "CANCELLED", "ARCHIVED",
]
PublicationState = Literal["PENDING", "PUBLISHED", "FAILED", "UNAVAILABLE"]
_STATE_ORDER: tuple[BusinessState, ...] = (
    "EXECUTING", "WAITING_CONFIRMATION", "WAITING_INPUT", "BLOCKED", "RESULT_REVIEW",
    "WAITING_REVIEW", "READY", "CANCELLED", "COMPLETED", "DRAFT",
)
_INPUT_ISSUES = {
    "MISSING_DATA", "MISSING_PARAMETER", "INVALID_PARAMETER", "UNRESOLVED_ITEM",
    "DATA_VERSION_NOT_CURRENT",
}
_INACTIVE_PREPARATION = {"FAILED", "REJECTED", "ABANDONED", "STALE"}


@dataclass(frozen=True)
class ResultLink:
    link_id: str
    reference: dict

    @property
    def output(self) -> tuple[str, str]:
        return self.reference["runId"], self.reference["outputName"]


@dataclass(frozen=True)
class ReviewCandidate:
    token: str
    links: tuple[ResultLink, ...]


@dataclass(frozen=True)
class Track:
    state: BusinessState
    reason: str
    review: ReviewCandidate | None = None


def _checks(facts: SessionFacts) -> list[tuple[dict, dict]]:
    if facts.task is None:
        return []
    checks = {check["id"]: check for check in facts.task["progress"]["criteria"]}
    return [(criterion, checks[criterion["id"]]) for criterion in facts.task["spec"]["criteria"]]


def _result_requirements(checks: list[tuple[dict, dict]]) -> set[tuple[str, str]] | None:
    result_checks = [(criterion, check) for criterion, check in checks if criterion["kind"] == "RESULT"]
    if not result_checks:
        return None
    if any(not check["satisfied"] or not check["evidence"].get("workflow_run_id")
           for _, check in result_checks):
        return set()
    return {
        (check["evidence"]["workflow_run_id"], output)
        for criterion, check in result_checks for output in criterion["output_names"]
    }


def delivery_workflows(facts: SessionFacts) -> tuple[WorkflowFact, ...]:
    required = _result_requirements(_checks(facts))
    required_runs = {run_id for run_id, _ in required} if required else set()
    workflows = {
        run.run_id: run
        for run in (*facts.workflows, *facts.evidence_workflows)
        if run.status == "SUCCEEDED" and (not required_runs or run.run_id in required_runs)
    }
    return tuple(workflows[key] for key in sorted(workflows))


def publication_for(
    run: WorkflowFact, links: Sequence[ResultLink], observed: Mapping[str, PublicationState],
) -> PublicationState:
    if any(link.output[0] == run.run_id and link.output[1] in run.output_names for link in links):
        return "PUBLISHED"
    return observed.get(run.run_id, "UNAVAILABLE")


def _review(
    work_item_id: str, facts: SessionFacts, checks: list[tuple[dict, dict]], links: tuple[ResultLink, ...],
) -> ReviewCandidate:
    scope = (
        {"goalRevision": facts.goal_revision}
        if facts.task is None else
        {"taskId": facts.task["spec"]["task_id"], "taskRevision": facts.task["spec"]["revision"]}
    )
    payload = {
        "workItemId": work_item_id,
        "sessionId": facts.session_id,
        **scope,
        "evidence": [check["evidence"] for _, check in checks],
        "results": [{"linkId": link.link_id, "reference": link.reference} for link in links],
    }
    return ReviewCandidate(sha256_bytes(canonical_json(payload)), links)


def _preparation_blocker(preparation: dict | None) -> Track | None:
    if preparation is None or preparation["status"] != "BLOCKED":
        return None
    entries = [entry for entry in preparation["entries"] if entry["status"] == "BLOCKED"]
    issues = [issue for entry in entries for issue in entry.get("blocking_issues", ())]
    if issues and all(issue["kind"] in _INPUT_ISSUES for issue in issues):
        return Track("WAITING_INPUT", "PREPARATION_INPUT_REQUIRED")
    return Track("BLOCKED", "PREPARATION_BLOCKED")


def derive_track(
    work_item_id: str,
    facts: SessionFacts,
    result_links: Sequence[ResultLink],
    publications: Mapping[str, PublicationState],
) -> Track:
    statuses = {run.status for run in facts.workflows}
    if statuses & {"QUEUED", "RUNNING"}:
        return Track("EXECUTING", "STOP_REQUESTED" if facts.stop_pending else "WORKFLOW_ACTIVE")
    if facts.confirmation_id:
        reason = (
            "CONFIRMATION_DECISION_PROCESSING"
            if facts.confirmation_decision_state in {"REQUESTED", "RUNNING"}
            else "CONFIRMATION_PENDING"
        )
        return Track("WAITING_CONFIRMATION", reason)
    if facts.rejected:
        return Track("BLOCKED", "CONFIRMATION_REJECTED")
    if statuses & {"FAILED", "TIMED_OUT"}:
        reason = "WORKFLOW_FAILED" if "FAILED" in statuses else "WORKFLOW_TIMED_OUT"
        return Track("BLOCKED", reason)

    deliveries = delivery_workflows(facts)
    if "CANCELLED" in statuses:
        if deliveries or statuses != {"CANCELLED"}:
            return Track("BLOCKED", "PARTIAL_EXECUTION_CANCELLED")
        return Track("CANCELLED", "WORKFLOW_CANCELLED")
    if not deliveries and (
        facts.stopped_before_start or (facts.agent_status, facts.agent_error) == ("CANCELLED", "AGENT_STOPPED")
    ):
        return Track("CANCELLED", "AGENT_STOPPED")

    task = facts.task
    if task and (task["spec"]["unresolved_items"] or any(step["gaps"] for step in task["spec"].get("steps", ()))):
        return Track("WAITING_INPUT", "TASK_INPUT_REQUIRED")
    preparation = facts.preparation
    if preparation and preparation.get("disposition") in _INACTIVE_PREPARATION:
        preparation = None
    if not (deliveries and preparation and preparation.get("disposition") == "CONSUMED"):
        if blocker := _preparation_blocker(preparation):
            return blocker
    if not deliveries and (facts.agent_status == "FAILED" or facts.continuation_stop in {"BUDGET", "DUPLICATE"}):
        return Track("BLOCKED", "ANALYSIS_BLOCKED")

    checks = _checks(facts)
    scoped = _result_requirements(checks)
    ready = task is not None and bool(checks) and all(check["satisfied"] for _, check in checks) and all(
        step["complete"] for step in task["progress"]["steps"]
    )
    waiting_review = any(criterion["kind"] == "USER_REVIEW" and not check["satisfied"]
                         for criterion, check in checks)
    if deliveries or scoped:
        return _result_track(
            work_item_id, facts, result_links, publications, deliveries, scoped, checks, ready, waiting_review,
        )
    if waiting_review:
        return Track("WAITING_REVIEW", "TASK_REVIEW_REQUIRED")
    if ready:
        if any(criterion["kind"] == "USER_REVIEW" and check["evidence"].get("review_quote")
               for criterion, check in checks):
            return Track("COMPLETED", "TASK_USER_REVIEWED")
        return Track("WAITING_REVIEW", "DELIVERY_REVIEW_REQUIRED", _review(work_item_id, facts, checks, ()))
    if facts.stop_pending:
        return Track("READY" if task else "DRAFT", "STOP_REQUESTED")
    if preparation and preparation["status"] == "READY":
        return Track("READY", "PREPARATION_READY")
    if task:
        return Track("READY", "TASK_READY")
    return Track("DRAFT", "ANALYZING" if facts.agent_status == "RUNNING" else "NO_BUSINESS_TARGET")


def _result_track(
    work_item_id: str,
    facts: SessionFacts,
    links: Sequence[ResultLink],
    publications: Mapping[str, PublicationState],
    deliveries: tuple[WorkflowFact, ...],
    required: set[tuple[str, str]] | None,
    checks: list[tuple[dict, dict]],
    task_ready: bool,
    waiting_review: bool,
) -> Track:
    outputs = {(run.run_id, name) for run in deliveries for name in run.output_names}
    if not outputs or any(not run.output_names for run in deliveries) or (required and not required <= outputs):
        return Track("BLOCKED", "WORKFLOW_OUTPUTS_MISSING")
    publication_states = {publication_for(run, links, publications) for run in deliveries}
    if "FAILED" in publication_states:
        return Track("BLOCKED", "PUBLICATION_FAILED")
    if "UNAVAILABLE" in publication_states:
        return Track("BLOCKED", "PUBLICATION_UNVERIFIED")
    if "PENDING" in publication_states:
        return Track("BLOCKED", "PUBLICATION_PENDING")
    if waiting_review:
        return Track("WAITING_REVIEW", "TASK_REVIEW_REQUIRED")
    if facts.task and not task_ready:
        return Track("RESULT_REVIEW", "TASK_DELIVERY_EVIDENCE_REQUIRED")

    selected = tuple(sorted(
        (link for link in links if link.output in (required if required else outputs)),
        key=lambda link: link.link_id,
    ))
    if required:
        complete = required <= {link.output for link in selected}
    else:
        complete = {run.run_id for run in deliveries} <= {link.output[0] for link in selected}
    if not complete:
        return Track("RESULT_REVIEW", "RESULT_LINK_REQUIRED")
    return Track("RESULT_REVIEW", "RESULT_ACCEPTANCE_REQUIRED", _review(work_item_id, facts, checks, selected))


def summarize_tracks(tracks: Sequence[Track], *, archived: bool) -> tuple[BusinessState, str]:
    if archived:
        return "ARCHIVED", "WORK_ITEM_ARCHIVED"
    states = {track.state: track.reason for track in reversed(tracks)}
    return next(((state, states[state]) for state in _STATE_ORDER if state in states), ("DRAFT", "NO_BUSINESS_TARGET"))
