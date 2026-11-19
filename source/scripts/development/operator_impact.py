"""Trace pinned operator dependencies and check an upgrade against real runs."""

import json
from pathlib import Path
import subprocess

import httpx

from assembler_domain import _compile_domain, _compile_services, _load_operator_catalog
from assembler_manifests import _load_manifest, _load_modules, _selected_domain
from operator_metadata import operator_uses
from scripts.lib.e2e_http import public_api_headers


def impact_plan(root, product, operator_id, version, previous_version=None, baseline=None):
    root = Path(root).resolve()
    product_path, manifest = _load_manifest(root, f"{product}/product-manifest.json")
    catalog, _ = _load_operator_catalog(root)
    candidate = catalog.get((operator_id, version))
    previous = catalog.get((operator_id, previous_version)) if previous_version else None
    if candidate is None:
        raise ValueError("OPERATOR_IMPACT_CANDIDATE_NOT_FOUND")
    if previous_version and (previous is None or previous_version == version):
        raise ValueError("OPERATOR_IMPACT_PREVIOUS_VERSION_REQUIRED")
    if baseline is not None:
        document = json.loads(Path(baseline).read_bytes())
        if document["productId"] != product:
            raise ValueError("OPERATOR_IMPACT_BASELINE_PRODUCT_MISMATCH")
        for row in document["workflowAuthoring"]["operators"]:
            key = (row["id"], row["version"])
            if key not in catalog or catalog[key]["implementationRevision"] != row["implementationRevision"]:
                raise ValueError(f"OPERATOR_PUBLISHED_IDENTITY_CHANGED: {key[0]}@{key[1]}")
    domain = _selected_domain(root, manifest["domainPackage"])
    services, modules = _compile_services(tuple(manifest["services"]), _load_modules(root))
    _, workflows, models = _compile_domain(domain, services, modules, catalog)
    uses = operator_uses(workflows, models)
    candidate_key = f"{operator_id}@{version}"
    previous_key = f"{operator_id}@{previous_version}"
    refs = {
        (use["workflowId"], use["version"]): use
        for key in (previous_key, candidate_key)
        for use in uses.get(key, [])
    }
    golden_file = product_path.parent / "development/workflow-golden-cases.v2.json"
    goldens = json.loads(golden_file.read_bytes())["cases"] if golden_file.is_file() else {}
    affected = []
    for workflow in workflows:
        key = workflow["workflowId"], workflow["version"]
        if key not in refs:
            continue
        pins = sorted({node["operatorVersion"] for node in workflow["nodes"] if node["operatorId"] == operator_id})
        affected.append({
            **refs[key], "workflowChecksum": workflow["definitionChecksum"], "operatorVersions": pins,
            "goldenCases": sorted(name for name, case in goldens.items() if case["workflowId"] == key[0]),
        })
    return {
        "productId": product, "projectId": manifest["projectId"],
        "operatorId": operator_id, "version": version, "runtime": candidate["runtime"],
        "implementationRevision": candidate["implementationRevision"],
        "previousVersion": previous_version, "compositionEnabled": candidate_key in manifest.get("compositionOperators", []),
        "affectedWorkflows": affected,
    }


def _run_dev(root, args):
    completed = subprocess.run(
        [str(root.parent / "bin/dev"), *args], cwd=root, check=False,
        capture_output=True, text=True,
    )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip()
        return {"status": "FAILED", "returncode": completed.returncode, "detail": detail[-2400:]}
    evidence = json.loads(completed.stdout)
    return {"status": evidence["status"], "evidence": evidence.get("summaryPath", evidence.get("runDirectory"))}


def _live_run(client, plan, run_id):
    base = f"/api/v12/workflow-runs/{run_id}"
    params = {"projectId": plan["projectId"]}
    run = client.get(base, params=params).raise_for_status().json()
    nodes = client.get(base + "/nodes", params=params).raise_for_status().json()["nodes"]
    publication = client.get(base + "/publication", params=params).raise_for_status().json()
    if run["status"] != "SUCCEEDED" or not nodes or any(n["status"] not in {"SUCCEEDED", "SKIPPED"} for n in nodes) or publication["state"] != "PUBLISHED":
        raise ValueError("OPERATOR_IMPACT_RUN_NOT_PUBLISHED")
    if not any(n["operatorId"] == plan["operatorId"] and n["status"] == "SUCCEEDED" for n in nodes):
        raise ValueError("OPERATOR_IMPACT_RUN_TARGET_NOT_EXECUTED")
    return run, nodes


def _operator_sample(client, plan, receipt):
    session_id, _, run_id = receipt.partition("=")
    if not session_id or not run_id:
        raise ValueError("OPERATOR_IMPACT_SAMPLE_REFERENCE_INVALID")
    run, nodes = _live_run(client, plan, run_id)
    session = f"/api/v12/sessions/{session_id}"
    state = client.get(session).raise_for_status().json()
    history = client.get(session + "/messages").raise_for_status().json()
    turns = [t for t in history["turns"] if t["agentRunId"] == run["agentRunId"] and t.get("workflowDraftId")]
    if run_id not in state["workflowRunIds"] or len(nodes) != 1 or len(turns) != 1:
        raise ValueError("OPERATOR_IMPACT_SAMPLE_NOT_SINGLE_NODE")
    payload = client.get(session + "/workflow-drafts/" + turns[0]["workflowDraftId"]).raise_for_status().json()["payload"]
    operators = payload["operators"]
    if (payload["composition"]["definitionDigest"] != run["workflowChecksum"] or len(operators) != 1
            or (operators[0]["operatorId"], operators[0]["operatorVersion"], operators[0]["implementationRevision"])
            != (plan["operatorId"], plan["version"], plan["implementationRevision"])):
        raise ValueError("OPERATOR_IMPACT_SAMPLE_IDENTITY_MISMATCH")
    return {"status": "PASS", "workflowRunId": run_id}


def verify_impact(root, plan, *, gateway_url=None, operator_run=None, workflow_runs=(), runtime_image=None):
    receipts = dict(item.split("=", 1) for item in workflow_runs)
    known = {f"{row['workflowId']}@{row['version']}" for row in plan["affectedWorkflows"]}
    if set(receipts) - known or len(receipts) != len(workflow_runs):
        raise ValueError("OPERATOR_IMPACT_WORKFLOW_REFERENCE_INVALID")
    if (operator_run or receipts) and not gateway_url:
        raise ValueError("OPERATOR_IMPACT_GATEWAY_REQUIRED")
    with httpx.Client(base_url=gateway_url or "http://127.0.0.1", headers=public_api_headers(), timeout=25, trust_env=False) as client:
        if operator_run:
            sample = _operator_sample(client, plan, operator_run)
        elif plan["runtime"] == "native":
            sample = _run_dev(root, ["operator-smoke", "--operator-id", plan["operatorId"], "--operator-version", plan["version"]])
        else:
            sample = {"status": "OPERATOR_SAMPLE_REQUIRED"}
        for row in plan["affectedWorkflows"]:
            key = f"{row['workflowId']}@{row['version']}"
            if run_id := receipts.get(key):
                run, _ = _live_run(client, plan, run_id)
                if (run["workflowId"], run["workflowVersion"], run["workflowChecksum"]) != (
                    row["workflowId"], row["version"], row["workflowChecksum"],
                ):
                    raise ValueError("OPERATOR_IMPACT_WORKFLOW_IDENTITY_MISMATCH")
                checks = [{"status": "PASS", "workflowRunId": run_id}]
            else:
                image = ["--runtime-image", runtime_image] if runtime_image else []
                checks = [
                    {"case": case, **_run_dev(root, ["workflow-golden", "--product", plan["productId"], "--case", case, *image])}
                    for case in row["goldenCases"]
                ] or [{"status": "WORKFLOW_REGRESSION_REQUIRED"}]
            row["checks"] = checks
    plan["operatorSample"] = sample
    plan["status"] = "PASS" if sample["status"] == "PASS" and all(
        check["status"] == "PASS" for workflow in plan["affectedWorkflows"] for check in workflow["checks"]
    ) else "FAILED"
    return plan
