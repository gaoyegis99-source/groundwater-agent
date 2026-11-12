"""Same held-fixed 297 questions, through the independent HTTP interface."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import time

from platform_knowledge_client import KnowledgeClient, models
from platform_knowledge_next.package_conversion import convert_domain_package
from scripts.development.product_selection import load_selected_product_by_id


def as_dict(value):
    return value.model_dump(mode="json", by_alias=True)


def retrieve(client, payload):
    return as_dict(client.retrieve(models.RetrievalRequest.model_validate({
        "mode": "hybrid", "purpose": "evaluation", **payload,
    })))


def evidence_identity(item):
    """Return a receipt-safe identity, never the evidence text itself."""
    keys = ("versionId", "unitId", "checksum")
    value = {key: item.get(key) for key in keys}
    valid = (
        isinstance(value["versionId"], str)
        and bool(value["versionId"])
        and isinstance(value["unitId"], str)
        and bool(value["unitId"])
        and isinstance(value["checksum"], str)
        and value["checksum"].startswith("sha256:")
        and len(value["checksum"]) == 71
    )
    return value if valid else None


def classify_case(response, expected_knowledge_id):
    """Classify retrieval quality without treating a discovery hit as a fact."""
    returned = [item for item in response["items"] if item["status"] == "OK"]
    identities = [identity for item in returned if (identity := evidence_identity(item))]
    ids = [item["knowledgeId"] for item in returned]
    if response["status"] == "NO_MATCH":
        failure = "NO_MATCH"
    elif len(identities) != len(returned):
        failure = "EVIDENCE_IDENTITY_MISSING"
    elif expected_knowledge_id not in ids:
        failure = "EXPECTED_KNOWLEDGE_NOT_RETURNED"
    else:
        failure = "OK"
    return ids, identities, failure


def wait(client, iid):
    deadline = time.monotonic() + 600
    while time.monotonic() < deadline:
        state = as_dict(client.get_import(iid))
        if state["state"] == "FAILED":
            raise RuntimeError(state["error"])
        if state["state"] == "SUCCEEDED":
            return state
        time.sleep(0.5)
    raise TimeoutError(iid)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8120")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--fixture", type=Path)
    parser.add_argument("--domain", type=Path)
    parser.add_argument("--skip-import", action="store_true")
    parser.add_argument("--data-receipt", type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[3]
    fixture = args.fixture or root / "tests/fixtures/groundwater-knowledge-retrieval.v1.json"
    selected = load_selected_product_by_id(root, "groundwater")
    domain = args.domain or selected.domain.root / "knowledge"
    cases = json.loads(fixture.read_text())["cases"]
    assert len(cases) == 297
    model_ids = {
        d["knowledgeId"]
        for d in json.loads((domain / "model-cards.v1.json").read_text())["documents"]
    }
    client = KnowledgeClient(args.url, writes=True)
    caps = as_dict(client.capabilities())
    if not args.skip_import:
        for path in sorted(domain.glob("*.json")):
            package = convert_domain_package(
                json.loads(path.read_text()),
                project=caps["project"],
                idempotency_key="eval-" + path.stem,
            )
            accepted = as_dict(client.submit_import(
                models.KnowledgePackageRequest.model_validate(package)
            ))
            wait(client, accepted["importId"])
    categories = {c["expectedKnowledgeId"]: c["category"] for c in cases}
    rows = []
    for case in cases:
        start = time.perf_counter()
        response = retrieve(client, {"query": case["query"]})
        ids, identities, failure = classify_case(response, case["expectedKnowledgeId"])
        top_category = categories.get(ids[0]) if ids else None
        rows.append(
            {
                **case,
                "requestStatus": response["status"],
                "returned": ids,
                "evidenceIdentities": identities,
                "failureClassification": failure,
                "top1": bool(ids and ids[0] == case["expectedKnowledgeId"]),
                "top5": case["expectedKnowledgeId"] in ids,
                "cohort": "modelCards"
                if case["expectedKnowledgeId"] in model_ids
                else "legacy",
                "reverse": case["category"] in {"TREND", "FLOW"}
                and top_category in {"TREND", "FLOW"}
                and case["category"] != top_category,
                "elapsed_ms": round((time.perf_counter() - start) * 1000, 3),
                "timings_ms": response["timingsMs"],
            }
        )
    metrics = {
        "cases": len(rows),
        "top1": sum(r["top1"] for r in rows),
        "top5": sum(r["top5"] for r in rows),
        "reverse": sum(r["reverse"] for r in rows),
        "verifiableEvidence": sum(
            r["failureClassification"] != "EVIDENCE_IDENTITY_MISSING" for r in rows
        ),
        "requestFailures": sum(r["failureClassification"] != "OK" for r in rows),
    }
    for cohort in ("legacy", "modelCards"):
        group = [r for r in rows if r["cohort"] == cohort]
        metrics[cohort] = {
            "cases": len(group),
            "top1": sum(r["top1"] for r in group),
            "top5": sum(r["top5"] for r in group),
        }
    passed = (
        metrics["top5"] == 297
        and metrics["top1"] >= 269
        and metrics["reverse"] == 0
        and metrics["verifiableEvidence"] == 297
        and metrics["requestFailures"] == 0
        and metrics["legacy"]["top5"] == 279
        and metrics["legacy"]["top1"] >= 252
        and metrics["modelCards"]["top5"] == 18
        and metrics["modelCards"]["top1"] >= 17
    )
    binding_rows = []
    if args.data_receipt:
        receipt = json.loads(args.data_receipt.read_text())
        for item in receipt["outputs"]:
            ref = item["reference"]
            binding = {
                "serviceBinding": "data-primary",
                "projectId": receipt["projectId"],
                "datasetId": ref["datasetId"],
                "versionId": ref["versionId"],
                "semanticId": ref["semanticId"],
            }
            expected = "data-binding-" + item["name"]
            exact = retrieve(client, {
                "query": item["name"] + " 数据绑定说明",
                "scope": "bound", "binding": binding,
            })
            exact_ids = [
                value["knowledgeId"] for value in exact["items"]
                if value["status"] == "OK" and value["bindingMatch"] == "EXACT"
            ]
            wrong = retrieve(client, {
                "query": item["name"] + " 数据绑定说明", "scope": "bound",
                "binding": {**binding, "versionId": ref["versionId"] + "-wrong"},
            })
            wrong_ids = [value.get("knowledgeId") for value in wrong["items"]]
            binding_rows.append({
                "name": item["name"], "expectedKnowledgeId": expected,
                "exactReturned": exact_ids, "exactPass": expected in exact_ids,
                "wrongVersionReturned": wrong_ids,
                "wrongVersionPass": expected not in wrong_ids,
            })
        passed = passed and all(
            row["exactPass"] and row["wrongVersionPass"] for row in binding_rows
        )
    report = {
        "status": "PASS" if passed else "FAIL",
        "time": datetime.now(timezone.utc).isoformat(),
        "fixture_sha256": hashlib.sha256(fixture.read_bytes()).hexdigest(),
        "capabilities": caps,
        "metrics": metrics,
        "retrievalGate": {
            "rule": (
                "Every question must return the expected candidate together with a valid "
                "version/unit/checksum identity; discovery does not itself authorise a fact."
            ),
            "rowsWithValidEvidenceIdentity": metrics["verifiableEvidence"],
            "failedRows": [
                {
                    "case": row.get("id", row["query"]),
                    "failureClassification": row["failureClassification"],
                    "requestStatus": row["requestStatus"],
                }
                for row in rows
                if row["failureClassification"] != "OK"
            ],
        },
        "bindingGate": {
            "cases": len(binding_rows),
            "exact": sum(row["exactPass"] for row in binding_rows),
            "wrongVersionIsolated": sum(row["wrongVersionPass"] for row in binding_rows),
            "rows": binding_rows,
        },
        "rows": rows,
    }
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(
        json.dumps({"status": report["status"], "metrics": metrics}, ensure_ascii=False)
    )
    client.close()
    raise SystemExit(0 if passed else 1)


if __name__ == "__main__":
    main()
