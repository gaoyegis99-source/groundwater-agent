"""Smoke-test the real assessment service through a Worker or the V12 Gateway."""

from __future__ import annotations

import argparse
import asyncio
from datetime import date, timedelta
import hashlib
import json
import math
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

from scripts.lib.e2e_http import public_api_headers


async def worker_probe(config_path: Path, fixture: Path) -> dict:
    from platform_executor_service.remote_http import RemoteOperationRunner

    config = json.loads(config_path.read_bytes())
    service = "groundwater-sustainability"
    pair = {"mediaType": "application/json", "format": "json"}
    with TemporaryDirectory(prefix="sustainability-probe-") as workspace:
        (Path(workspace) / "outputs").mkdir()
        lease = {
            "jobId": uuid4().hex,
            "nodeId": "evaluate",
            "timeoutSeconds": 60,
            "remoteBinding": {"serviceId": service, "operationType": "sustainability.evaluate"},
            "parameters": {},
            "workspace": workspace,
            "inputs": {"assessment_input": [{"path": str(fixture.resolve()), **pair}]},
            "outputContracts": [
                {"name": "sustainability_result", "kind": "value"},
                *[
                    {"name": port, "kind": "artifact", "formatPairs": [pair]}
                    for port in ("result_file", "run_receipt")
                ],
            ],
        }

        async def checkpoint(_state: dict) -> None:
            pass

        runner = RemoteOperationRunner(config, lease, checkpoint)
        async with runner._client() as client:
            for path in ("/readyz", "/version", "/api/v1/capabilities"):
                (await client.get(path)).raise_for_status()
            rejected = await client.get("/api/v1/capabilities", headers={"Authorization": ""})
            assert rejected.status_code == 401
        result = await runner.run()
        assert {item["port"] for item in result.outputs} == {"result_file", "run_receipt"}
        assert runner.state["remoteStatus"] == "succeeded"
        return {
            "status": "SUCCEEDED",
            "remoteTaskId": runner.state["remoteTaskId"],
            "grade": result.values["sustainability_result"]["final_grade"],
        }


def _normalized_evidence(payload: dict) -> dict:
    from moperators.operators.sustainability_preparation import (
        MANAGEMENT,
        SIMULATION_FIELDS,
        TEXT_FIELDS,
        UNITS,
    )

    assert payload["synthetic_example"] is True
    scope = {
        "waterSource": payload["context"]["water_source"],
        "startDate": "2024-01-01",
        "endDate": payload["context"]["evaluation_time"],
    }
    documents = {
        role: {"scope": dict(scope), "synthetic": True, "fields": {}}
        for role in ("simulation", "monitoring", "management")
    }
    for field in sorted(UNITS.keys() | TEXT_FIELDS):
        if field in MANAGEMENT:
            role = "management"
        elif field in SIMULATION_FIELDS:
            role = "simulation"
        else:
            role = "monitoring"
        documents[role]["fields"][field] = {
            "value": payload[field],
            "unit": UNITS.get(field),
            "basis": "合成验收示例",
            "source": "sustainability_task_request.json",
        }
    return documents


def _download_json(client, result: dict) -> dict:
    from tests.e2e.groundwater.model_lifecycle import PROJECT

    documents = {}
    for item in result["output_references"]:
        response = client.get(
            f"/api/v12/data/versions/{item['version_id']}/objects/{item['object_id']}/content",
            headers={"X-Project-ID": PROJECT, "X-Client-Type": "cli"},
        )
        response.raise_for_status()
        documents[item["output_name"]] = response.json()
    return documents


def _load_case(fixture: Path) -> tuple[dict, dict, dict]:
    payload = json.loads(fixture.read_bytes())
    if payload.get("schemaVersion") != "sustainability-synthetic-case.v1":
        return _normalized_evidence(payload), {
            key: value for key, value in payload.items()
            if key not in {"client_request_id", "evidence_note"}
        }, {"grade": "I"}

    assert payload["dataNature"] == "SYNTHETIC_TEST_ONLY"
    roles = {"simulation", "monitoring", "management"}
    assert set(payload["evidence"]) == roles
    root = fixture.resolve().parent

    def read(reference: dict) -> bytes:
        path = (root / reference["path"]).resolve(strict=True)
        assert path.is_relative_to(root), path
        content = path.read_bytes()
        assert "sha256:" + hashlib.sha256(content).hexdigest() == reference["sha256"], path
        return content

    for item in payload["rawInputs"]:
        read(item)
    documents = {role: json.loads(read(ref)) for role, ref in payload["evidence"].items()}
    scope = documents["management"]["scope"]
    assert all(doc["synthetic"] is True and doc["scope"] == scope for doc in documents.values())
    candidate = {
        "context": {
            "water_source": scope["waterSource"],
            "groundwater_type": "孔隙型",
            "evaluation_time": scope["endDate"],
        },
        "synthetic_example": True,
        **{
            field: record["value"]
            for doc in documents.values()
            for field, record in doc["fields"].items()
        },
    }
    return documents, candidate, payload["expected"]


def _gateway_chain(
    client, capabilities: dict, fixture: Path, output_directory: Path | None = None,
) -> dict:
    from platform_executor_service.contracts import canonical_json

    from tests.e2e.groundwater.model_lifecycle import SYNTHETIC_TEST_ONLY, _execute, _publish

    fixture_content = fixture.read_bytes()
    documents, expected_candidate, expected = _load_case(fixture)

    def publish(role: str, document: dict) -> dict:
        return _publish(
            client,
            name=f"{document['scope']['waterSource']} 合成证据 {role}",
            content=json.dumps(document, ensure_ascii=False, allow_nan=False).encode("utf-8"),
            format_="json",
            media_type="application/json",
            data_nature=SYNTHETIC_TEST_ONLY,
            source=fixture.name,
        )

    refs = {role: publish(role, document) for role, document in documents.items()}

    def prepare(monitoring: dict, phase: str) -> tuple[dict, dict]:
        arguments = {
            "simulationEvidenceVersionId": refs["simulation"]["version_id"],
            "monitoringEvidenceVersionId": monitoring["version_id"],
            "managementEvidenceVersionId": refs["management"]["version_id"],
        }
        result = _execute(
            client, capabilities, model_id="groundwater.sustainability-input-preparation",
            tool="prepare_source_sustainability", arguments=arguments,
            input_versions=set(arguments.values()), data_nature=SYNTHETIC_TEST_ONLY,
            output_directory=output_directory / phase if output_directory is not None else None,
        )
        return result, _download_json(client, result)

    monitoring = documents["monitoring"]
    end_date = date.fromisoformat(monitoring["scope"]["endDate"]) - timedelta(days=1)
    invalid = {**monitoring, "scope": {**monitoring["scope"], "endDate": end_date.isoformat()}}
    blocked, blocked_outputs = prepare(publish("monitoring-scope-mismatch", invalid), "blocked")
    assert blocked_outputs["assessment-input-candidate"] == {
        "status": "BLOCKED", "assessmentInput": None,
    }
    assert {"role": "monitoring", "code": "SCOPE_MISMATCH"} in (
        blocked_outputs["assessment-input-sources"]["issues"]
    )
    prepared, prepared_outputs = prepare(refs["monitoring"], "prepared")
    candidate = prepared_outputs["assessment-input-candidate"]
    assert candidate == expected_candidate
    sources = prepared_outputs["assessment-input-sources"]
    assert sources["status"] == "REVIEW_REQUIRED" and not sources["issues"]
    assert sources["inputSha256"] == {role: ref["checksum"] for role, ref in refs.items()}
    for role, field in (
        ("simulation", "recharge_r_m3_a"),
        ("monitoring", "class_iii_count_nd"),
        ("management", "prohibited_area"),
    ):
        assert sources["fields"][field]["role"] == role
    candidate_ref = next(
        item for item in prepared["output_references"]
        if item["output_name"] == "assessment-input-candidate"
    )
    candidate_version = candidate_ref["version_id"]
    evaluated = _execute(
        client, capabilities, model_id="groundwater.source-sustainability",
        tool="run_source_sustainability", arguments={"assessmentVersionId": candidate_version},
        input_versions={candidate_version}, data_nature=SYNTHETIC_TEST_ONLY,
        output_directory=output_directory / "evaluated" if output_directory is not None else None,
    )
    evaluation_outputs = _download_json(client, evaluated)
    value = evaluation_outputs["sustainability-result"]
    assert value == evaluation_outputs["sustainability-result-file"]
    assert value["final_grade"] == expected["grade"] and value["synthetic_example"] is True
    if "score" in expected:
        assert math.isclose(value["overall_score_selected"], expected["score"], abs_tol=1e-8)
        for bound in ("min", "max"):
            assert math.isclose(value["overall_score"][bound], expected[bound], abs_tol=1e-8)
    assert "取中点不是指南明文规定" in value["scope_notice"]
    assert "演示结果不替代正式评估" in value["scope_notice"]
    receipt = evaluation_outputs["sustainability-run-receipt"]
    assert receipt["serviceId"] == "groundwater-sustainability"
    assert receipt["operationType"] == "sustainability.evaluate"
    assert receipt["status"] == "succeeded" and receipt["remoteTerminationConfirmed"] is True
    assert receipt["inputs"]["assessment_input"]["sha256"] == candidate_ref["checksum"]
    result_file = next(
        item for item in evaluated["output_references"]
        if item["output_name"] == "sustainability-result-file"
    )
    result_artifact = next(item for item in receipt["artifacts"] if item["port"] == "result_file")
    assert result_artifact["sha256"] == result_file["checksum"]
    assert receipt["valuesSha256"] == "sha256:" + hashlib.sha256(
        canonical_json({"sustainability_result": value})
    ).hexdigest()
    return {
        **evaluated,
        "data_nature": SYNTHETIC_TEST_ONLY,
        "fixture_sha256": "sha256:" + hashlib.sha256(fixture_content).hexdigest(),
        "blocked": blocked,
        "prepared": prepared,
        "field_sources": sources,
        "grade": value["final_grade"],
        "score": value["overall_score_selected"],
        "run_receipt": receipt,
    }


def gateway_probe(
    url: str, token_file: Path, fixture: Path, output_directory: Path | None = None,
) -> dict:
    import httpx

    from tests.e2e.groundwater.model_lifecycle import _request

    from scripts.lib.e2e_http import TOKEN_ENVIRONMENT

    previous = os.environ.get(TOKEN_ENVIRONMENT)
    os.environ[TOKEN_ENVIRONMENT] = token_file.read_text().strip()
    try:
        with httpx.Client(
            base_url=url, headers=public_api_headers(), timeout=60, trust_env=False
        ) as client:
            capabilities = _request(client, "GET", "/api/v12/capabilities")
            return _gateway_chain(client, capabilities, fixture, output_directory)
    finally:
        if previous is None:
            os.environ.pop(TOKEN_ENVIRONMENT, None)
        else:
            os.environ[TOKEN_ENVIRONMENT] = previous


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("fixture", type=Path)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--executor-config", type=Path)
    mode.add_argument("--gateway")
    parser.add_argument("--token-file", type=Path)
    args = parser.parse_args()
    if args.executor_config:
        result = asyncio.run(worker_probe(args.executor_config, args.fixture))
    else:
        if args.token_file is None:
            parser.error("--gateway requires --token-file")
        result = gateway_probe(args.gateway, args.token_file, args.fixture)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
