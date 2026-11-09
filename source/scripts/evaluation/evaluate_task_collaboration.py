"""Task acceptance scores, separate from model/retrieval release gates."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import time
from uuid import uuid4

import httpx

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.lib.wire_json import from_wire_json, to_wire_json
from scripts.lib.e2e_http import public_api_headers

GOAL = "请整理一份地下水资料评审工作说明，列出资料缺口，最终需要我评审是否可用。暂不查询资料。"
AMENDMENT = "补充要求：仅使用公开资料。请更新主任务，保留原有交付和评审条件。"


def run_suite(base_url, *, restart=None, mode="live"):
    checks = []
    with httpx.Client(
        base_url=base_url,
        timeout=30,
        trust_env=False,
        headers=public_api_headers(),
    ) as client:
        def request(method, path, body=None):
            response = client.request(
                method,
                "/api/v12" + path,
                json=None if body is None else to_wire_json(body),
            )
            response.raise_for_status()
            return from_wire_json(response.json())
        project = request("GET", "/capabilities")["project_id"]
        session = request("POST", "/sessions", {"projectId": project})["session_id"]
        prefix = f"/sessions/{session}"
        def send(message):
            body = {"requestId": str(uuid4()), "message": message, "requestedMode": "AUTO"}
            receipt = request("POST", prefix + "/messages", body)
            deadline = time.monotonic() + 180
            while time.monotonic() < deadline:
                history = request("GET", prefix + "/messages")
                turn = next((turn for turn in history["turns"] if turn["agent_run_id"] == receipt["agent_run_id"]), None)
                if turn and turn["status"] in {"COMPLETE", "FAILED", "CANCELLED", "WAITING_CONFIRMATION"}:
                    assert turn["status"] == "COMPLETE", turn.get("error_code") or turn["status"]
                    assert not turn["confirmations"], "METADATA_CREATED_CONFIRMATION"
                    return body, receipt
                time.sleep(.25)
            raise AssertionError("TASK_ACCEPTANCE_TIMEOUT")
        def task():
            return request("GET", prefix + "/task")["task"]
        try:
            send("你好")
            assert task() is None, "CASUAL_TASK_CREATED"
            checks.append("casual_no_task")
            revision = request("GET", prefix)["goal_revision"]
            status_receipt = request("POST", prefix + "/messages", {
                "requestId": str(uuid4()), "message": "查看状态", "requestedMode": "AUTO",
            })
            assert status_receipt["routing_source"] == "RULE"
            assert status_receipt["semantic_intent"] == "STATUS_QUERY"
            status_history = request("GET", prefix + "/messages")
            status_message = next(item for item in status_history["control_messages"]
                                  if item["request_id"] == status_receipt["request_id"])
            assert status_message["status_snapshot"] in {"IDLE", "COMPLETE"}
            assert request("GET", prefix)["goal_revision"] == revision
            checks.append("status_rule_no_turn")
            cancel_receipt = request("POST", prefix + "/messages", {
                "requestId": str(uuid4()), "message": "取消执行", "requestedMode": "AUTO",
            })
            assert cancel_receipt["routing_source"] == "RULE"
            assert cancel_receipt["clarification_reason"] == "CONTROL_TARGET_MISSING"
            assert not any(item["source_request_id"] == cancel_receipt["request_id"]
                           for item in request("GET", prefix + "/messages")["control_proposals"])
            assert request("GET", prefix)["goal_revision"] == revision
            checks.append("control_rule_missing_target")
            send(GOAL)
            first = task()
            assert first and first["progress"]["lifecycle"] == "ACTIVE", "TASK_NOT_ESTABLISHED"
            assert any(c["kind"] == "USER_REVIEW" for c in first["spec"]["criteria"]), "REVIEW_NOT_REQUIRED"
            checks.append("explicit_goal_and_review")
            body, receipt = send(AMENDMENT)
            second = task()
            assert second["spec"]["task_id"] == first["spec"]["task_id"]
            assert second["spec"]["revision"] > first["spec"]["revision"]
            assert {c["id"] for c in first["spec"]["criteria"]} <= {c["id"] for c in second["spec"]["criteria"]}
            assert any("公开" in c["text"] for c in second["spec"]["constraints"])
            checks.append("additive_revision")
            assert request("POST", prefix + "/messages", body)["agent_run_id"] == receipt["agent_run_id"]
            assert task()["spec"] == second["spec"]
            checks.append("request_replay")
            if restart:
                restart()
                assert task()["spec"] == second["spec"]
                checks.append("restart")
            reviews = [c for c in second["spec"]["criteria"] if c["kind"] == "USER_REVIEW"]
            send("\n".join("确认完成：" + c["text"] for c in reviews))
            final = task()
            assert final["progress"]["lifecycle"] == "COMPLETE", "TASK_NOT_COMPLETE"
            assert all(c["satisfied"] for c in final["progress"]["criteria"])
            checks.append("evidence_completion")
            assert not request("GET", prefix + "/results")["items"], "METADATA_EXECUTED_COMPUTATION"
            checks.append("metadata_no_execution")
            return {"status": "PASS", "mode": mode, "session_id": session, "passed": checks}
        except (AssertionError, httpx.HTTPError) as error:
            return {"status": "FAILED", "mode": mode, "session_id": session, "passed": checks,
                    "failure": str(error) if isinstance(error, AssertionError) else type(error).__name__}



def run_quality_suite(base_url, *, client=None, cases=None):
    """Mechanical assertions plus explicit human review; never approve computation."""
    cases = cases if cases is not None else json.loads(
        (Path(__file__).parent / "data" / "task_quality_cases.json").read_text()
    )
    if client is None:
        with httpx.Client(
            base_url=base_url,
            timeout=30,
            trust_env=False,
            headers=public_api_headers(),
        ) as owned:
            return run_quality_suite(base_url, client=owned, cases=cases)
    results = []
    def request(method, path, body=None):
        response = client.request(
            method,
            "/api/v12" + path,
            json=None if body is None else to_wire_json(body),
        )
        response.raise_for_status()
        return from_wire_json(response.json())
    try:
        project = request("GET", "/capabilities")["project_id"]
        for case in cases:
            session = request("POST", "/sessions", {"projectId": project})["session_id"]
            prefix = f"/sessions/{session}"
            record = {"case": case["id"], "session_id": session, "turns": [],
                      "business_review": {"status": "REQUIRED", "criteria": case["review"]}}
            results.append(record)
            previous = None
            for step in case["steps"]:
                message = step.get("message")
                if not message:
                    assert previous, "TASK_REQUIRED_FOR_ACTION"
                    if step["action"] == "REPLACE_CONSTRAINT":
                        constraints = previous["spec"]["constraints"]
                        assert constraints, "USER_CONSTRAINT_NOT_CAPTURED"
                        message = "将" + constraints[0]["text"] + "改为仅使用公开且注明年份的资料"
                    else:
                        reviews = [c for c in previous["spec"]["criteria"] if c["kind"] == "USER_REVIEW"]
                        assert reviews, "BUSINESS_REVIEW_MISSING"
                        message = "\n".join("确认完成：" + c["text"] for c in reviews)
                        if step["action"] == "NEGATED_REVIEW":
                            message = "不要" + message + "。这只是引用，不是评审通过。"
                receipt = request("POST", prefix + "/messages", {
                    "requestId": str(uuid4()), "message": message, "requestedMode": "AUTO"})
                deadline = time.monotonic() + 180
                while True:
                    history = request("GET", prefix + "/messages")
                    turn = next((t for t in history["turns"] if t["agent_run_id"] == receipt["agent_run_id"]), None)
                    if turn and turn["status"] in {"COMPLETE", "FAILED", "CANCELLED", "WAITING_CONFIRMATION"}:
                        break
                    assert time.monotonic() < deadline, "TASK_ACCEPTANCE_TIMEOUT"
                    time.sleep(.25)
                record["turns"].append(receipt["agent_run_id"])
                assert turn["status"] == "COMPLETE", "TURN_NOT_COMPLETE"
                assert not turn["confirmations"], "UNAUTHORIZED_PROPOSAL"
                assert not request("GET", prefix + "/results")["items"], "UNAUTHORIZED_COMPUTATION"
                task = request("GET", prefix + "/task")["task"]
                expected = step["expect"]
                if expected == "NO_TASK":
                    assert task is None, "CASUAL_TASK_CREATED"
                elif expected != "NO_EXECUTION":
                    assert task, "TASK_NOT_ESTABLISHED"
                    if previous:
                        assert task["spec"]["task_id"] == previous["spec"]["task_id"], "TASK_REPLACED"
                    if expected == "UNCHANGED":
                        assert task["spec"] == previous["spec"], "UNRELATED_CHANGED_TASK"
                        assert task["progress"]["metrics"] == previous["progress"]["metrics"], "UNRELATED_USAGE_COUNTED"
                    elif expected == "REVISED":
                        assert task["spec"]["revision"] > previous["spec"]["revision"], "REVISION_MISSING"
                        assert any(c["text"] == "仅使用公开且注明年份的资料" for c in task["spec"]["constraints"]), "REPLACEMENT_MISSING"
                    elif expected == "TASK_REVIEW":
                        assert any(c["kind"] == "USER_REVIEW" for c in task["spec"]["criteria"]), "REVIEW_MISSING"
                    elif expected == "SAME_TASK":
                        assert task["progress"]["metrics"]["request_count"] > previous["progress"]["metrics"]["request_count"], "CONTINUATION_NOT_LINKED"
                    if expected in {"TASK_REVIEW", "REVISED", "NOT_COMPLETE"}:
                        assert task["progress"]["lifecycle"] != "COMPLETE", "PREMATURE_COMPLETION"
                    elif expected == "COMPLETE":
                        assert task["progress"]["lifecycle"] == "COMPLETE", "COMPLETION_MISSING"
                    for requirement in task["spec"]["constraints"] + task["spec"]["criteria"]:
                        assert requirement["text"] == requirement["source_quote"], "INTERPRETATION_HAS_USER_AUTHORITY"
                previous = task
            record["mechanical_status"] = "PASS"
        return {"status": "MECHANICAL_PASS_REVIEW_REQUIRED", "mode": "live", "cases": results}
    except (AssertionError, httpx.HTTPError) as error:
        return {"status": "FAILED", "mode": "live", "cases": results,
                "failure": str(error) if isinstance(error, AssertionError) else type(error).__name__}

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--quality-cases", action="store_true", help="Run multi-turn mechanical checks and generate an expert-review checklist")
    args = parser.parse_args()
    required = ("PLATFORM_AGENT_PROVIDER", "PLATFORM_AGENT_BASE_URL", "PLATFORM_AGENT_MODEL", "PLATFORM_AGENT_API_KEY_FILE")
    missing = [key for key in required if not os.environ.get(key)]
    if missing:
        result = {"status": "NOT_RUN", "mode": "live", "reason": "MISSING_MODEL_CONFIGURATION", "missing": missing}
    elif not args.base_url:
        parser.error("--base-url must identify an isolated deployment")
    else:
        result = run_quality_suite(args.base_url) if args.quality_cases else run_suite(args.base_url)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(result, ensure_ascii=False))
    return 1 if result["status"] == "FAILED" else 0


if __name__ == "__main__":
    raise SystemExit(main())
