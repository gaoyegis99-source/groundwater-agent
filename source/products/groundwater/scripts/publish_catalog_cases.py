"""Publish catalog examples from pinned, already available local Data versions."""

from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path

import httpx

from publish_workspace_cases import publish, request


PRODUCT = Path(__file__).resolve().parents[1]
NATURE_LABELS = {
    "REAL_DERIVED_TEST_SAMPLE": "公开实测衍生样本",
    "SYNTHETIC_TEST_ONLY": "合成验收资料",
}


def read_source(client: httpx.Client, version_id: str, expected_sha256: str) -> tuple[dict, dict, bytes]:
    detail = request(client, "GET", f"/versions/{version_id}")
    raw, = [item for item in detail["objects"] if item["role"] == "raw"]
    if detail["versionId"] != version_id or raw["checksum"] != expected_sha256:
        raise ValueError(f"CASE_SOURCE_IDENTITY_MISMATCH: {version_id}")
    response = client.get(f'/api/v12/data/versions/{version_id}/objects/{raw["objectId"]}/content')
    response.raise_for_status()
    if "sha256:" + sha256(response.content).hexdigest() != expected_sha256:
        raise ValueError(f"CASE_SOURCE_CHECKSUM_MISMATCH: {version_id}")
    return detail, raw, response.content


def read_case(source: httpx.Client, profile: dict, model_id: str, case: dict) -> list[tuple[dict, bytes, dict]]:
    roles = case["inputs"]
    name = profile["displayName"]
    nature = case["nature"]
    if set(roles) != set(profile["inputs"]) or not roles or not profile.get("inputCaseTag"):
        raise ValueError(f"CASE_INPUT_ROLES_MISMATCH: {model_id}")
    source_versions = {pin["versionId"] for pin in roles.values()}
    previous_result = case.get("previousResultVersionId")
    if previous_result:
        result = request(source, "GET", f"/versions/{previous_result}")
        if set(result["inputVersions"]) != source_versions:
            raise ValueError(f"CASE_PREVIOUS_RUN_INPUTS_MISMATCH: {model_id}")
    batch = sha256(json.dumps({"model": model_id, "roles": roles}, sort_keys=True).encode()).hexdigest()
    sources = {
        version: read_source(source, version, next(pin["sha256"] for pin in roles.values() if pin["versionId"] == version))
        for version in sorted(source_versions)
    }
    outputs = []
    for role, pin in roles.items():
        version = pin["versionId"]
        detail, raw, content = sources[version]
        presentation = profile["inputs"][role]
        format_ = raw["format"]
        if format_ not in presentation["formats"] or raw["checksum"] != pin["sha256"]:
            raise ValueError(f"CASE_INPUT_CONTRACT_MISMATCH: {model_id}/{role}")
        semantics = detail["semantics"]
        limitations = list(dict.fromkeys([*(semantics.get("limitations") or []), nature]))
        if case.get("note"):
            limitations.append(case["note"])
        output = {
            "name": role,
            "title": f'{name}案例 · {presentation["label"]}（{NATURE_LABELS[nature]}）',
            "format": format_,
            "mediaType": raw["mediaType"],
            "policy": "FILE_QUERYABLE" if format_ in {"csv", "parquet"} else "FILE_ONLY",
            "tags": [profile["inputCaseTag"], f"case-batch:{batch}", f"input:{role}", nature],
            "semantics": {**semantics, "limitations": limitations},
        }
        origin = {"datasetId": detail["datasetId"], "versionId": version, "semanticId": detail["semanticId"],
                  "objectId": raw["objectId"], "checksum": raw["checksum"]}
        outputs.append((output, content, origin))
    return outputs


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-port", type=int, default=28186)
    parser.add_argument("--target-port", type=int, required=True)
    parser.add_argument("--case", action="append", help="Publish only the given model ID. Repeat for multiple models.")
    arguments = parser.parse_args()
    spec = json.loads((PRODUCT / "development/workspace-catalog-cases.v1.json").read_text())
    product = json.loads((PRODUCT / "product-manifest.json").read_text())
    profiles = json.loads((PRODUCT / product["uiProfile"]["path"]).read_text())["models"]
    case_ids = arguments.case if arguments.case else list(spec["cases"])
    common = {"timeout": 60, "trust_env": False, "headers": {"X-Client-Type": "cli"}}
    with (
        httpx.Client(base_url=f"http://127.0.0.1:{arguments.source_port}", **common) as source,
        httpx.Client(base_url=f"http://127.0.0.1:{arguments.target_port}", **common) as target,
    ):
        receipts = {}
        for model_id in case_ids:
            outputs = read_case(source, profiles[model_id], model_id, spec["cases"][model_id])
            receipt = publish(target, outputs, arguments.source_port == arguments.target_port)
            pins = {output["name"]: origin for output, _, origin in outputs}
            if {item["name"] for item in receipt["outputs"]} != set(pins):
                raise ValueError(f"CASE_PUBLICATION_ROLES_MISMATCH: {model_id}")
            for item in receipt["outputs"]:
                item["source"] = pins[item["name"]]
            receipts[model_id] = receipt
        print(json.dumps(receipts, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
