"""Frozen fixture, configuration, and release identity validation."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from scripts.evaluation.evaluate_groundwater_agent import dictionary, document, fixture
from scripts.lib.wire_json import to_wire_json

from .definitions import (
    EXPECTED_DATA_FIXTURE_CHECKSUM,
    EXPECTED_DOMAIN_PACKAGE,
    EXPECTED_MODEL_IDS,
    FIXTURE,
    PROJECT,
    REQUIRED_ENVIRONMENT,
    ROOT,
    SUPPORTED_PROVIDERS,
    _canonical_json,
    _sha256,
)

def data_fixture_checksum() -> str:
    content, analyses = fixture()
    resource = {
        "dataset_id": "{dataset_id}",
        "version_id": "{version_id}",
        "semantic_id": "{semantic_id}",
        "object_id": "{object_id}",
        "checksum": _sha256(content),
    }
    body = dictionary(resource, analyses, "m")
    body["evaluation_scope"] = "REPLY_QUALITY_FROZEN_SYNTHETIC"
    knowledge = document(
        PROJECT, body, "{resource_name}数据字典与项目约定",
    )
    checksum = _sha256(
        content + b"\0" + _canonical_json(analyses) + b"\0"
        + _canonical_json(knowledge)
    )
    if checksum != EXPECTED_DATA_FIXTURE_CHECKSUM:
        raise ValueError("REPLY_QUALITY_DATA_FIXTURE_CHANGED")
    return checksum


def load_release_identity(config_path: Path) -> dict:
    product = json.loads(
        (ROOT / "products/groundwater/product-manifest.json").read_text(
            encoding="utf-8"
        )
    )
    if product.get("domainPackage") != EXPECTED_DOMAIN_PACKAGE:
        raise ValueError("REPLY_QUALITY_DOMAIN_IDENTITY_MISMATCH")
    config = json.loads(config_path.read_text(encoding="utf-8"))
    lock_path = config_path.with_name("assembly-lock.json")
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    if (
        config.get("schemaVersion") != "kernel-config.v8"
        or config.get("productId") != "groundwater"
        or not isinstance(config.get("manifestChecksum"), str)
        or lock.get("schemaVersion") != "assembly-lock.v8"
        or lock.get("productId") != "groundwater"
        or {
            key: lock.get("domainPackage", {}).get(key)
            for key in ("domainId", "version", "checksum")
        }
        != EXPECTED_DOMAIN_PACKAGE
        or lock.get("productManifestChecksum") != config["manifestChecksum"]
    ):
        raise ValueError("REPLY_QUALITY_KERNEL_CONFIG_INVALID")
    models = sorted(({
        "modelId": item["modelId"],
        "modelVersion": item["version"],
        "modelChecksum": item["modelChecksum"],
        "workflowId": item["workflowRef"]["workflowId"],
        "workflowVersion": item["workflowRef"]["version"],
        "workflowChecksum": item["workflowRef"]["checksum"],
        "tool": item["tool"]["name"],
    } for item in config["models"]), key=lambda item: item["modelId"])
    if {item["modelId"] for item in models} != EXPECTED_MODEL_IDS:
        raise ValueError("REPLY_QUALITY_CAPABILITY_IDENTITY_MISMATCH")
    final_outputs = {
        item["tool"]["name"]: list(item["finalOutputs"])
        for item in config["models"]
    }
    execution_contracts = {
        item["tool"]["name"]: {
            "inputSchemaChecksum": item["inputSchemaChecksum"],
            "defaultParameters": item["defaultParameters"],
            "executionService": item["executionService"],
            "executorId": item["executorId"],
            "finalOutputs": list(item["finalOutputs"]),
        }
        for item in config["models"]
    }
    authoring = config["workflowAuthoring"]
    required_operators = {
        item["id"]: {
            "version": item["version"],
            "implementationRevision": item["implementationRevision"],
        }
        for item in authoring["operators"]
        if item["id"] in {"table.sort", "statistics.describe"}
    }
    if set(required_operators) != {"table.sort", "statistics.describe"}:
        raise ValueError("REPLY_QUALITY_AUTHORING_IDENTITY_MISMATCH")
    return {
        "domain_package": dict(EXPECTED_DOMAIN_PACKAGE),
        "assembly_manifest_checksum": config["manifestChecksum"],
        "assembly_lock_checksum": _sha256(lock_path.read_bytes()),
        "models": models,
        "model_identity_checksum": _sha256(_canonical_json(models)),
        "final_outputs": final_outputs,
        "execution_contracts": execution_contracts,
        "operator_catalog_digest": authoring["operatorCatalogDigest"],
        "draft_operators": required_operators,
    }


def verify_capabilities(capabilities: dict, release_identity: dict) -> None:
    capabilities = to_wire_json(capabilities)
    if (
        capabilities.get("productId") != "groundwater"
        or capabilities.get("projectId") != PROJECT
    ):
        raise ValueError("REPLY_QUALITY_PRODUCT_IDENTITY_MISMATCH")
    actual = sorted(({
        "modelId": item["modelId"],
        "modelVersion": item["modelVersion"],
        "modelChecksum": item["modelChecksum"],
        "workflowId": item["workflowId"],
        "workflowVersion": item["workflowVersion"],
        "workflowChecksum": item["workflowChecksum"],
        "tool": item["tool"],
    } for item in capabilities["models"]), key=lambda item: item["modelId"])
    if actual != release_identity["models"]:
        raise ValueError("REPLY_QUALITY_CAPABILITY_IDENTITY_MISMATCH")


def load_cases(path: Path = FIXTURE) -> list[dict]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or set(raw) != {"schemaVersion", "cases"}:
        raise ValueError("REPLY_QUALITY_FIXTURE_INVALID")
    if raw["schemaVersion"] != "reply-quality-evaluation.v1":
        raise ValueError("REPLY_QUALITY_FIXTURE_INVALID")
    cases = raw["cases"]
    expected_categories = {
        "simple_direct",
        "evidence_fact",
        "not_found",
        "clarification",
        "method_comparison",
        "workflow_draft",
        "execution_success",
        "failure_or_rejection",
    }
    if (
        not isinstance(cases, list)
        or len(cases) != 8
        or {case.get("category") for case in cases} != expected_categories
        or len({case.get("case_id") for case in cases}) != 8
    ):
        raise ValueError("REPLY_QUALITY_FIXTURE_INVALID")
    required = {
        "case_id", "category", "requested_mode", "expected_mode",
        "expected_policy", "expected_shape", "message", "asset",
        "requires_source", "read_only",
    }
    for case in cases:
        if not required <= set(case):
            raise ValueError("REPLY_QUALITY_FIXTURE_INVALID")
        if (
            not all(isinstance(case[name], str) and case[name] for name in (
                "case_id", "category", "requested_mode", "expected_mode",
                "expected_shape", "message",
            ))
            or type(case["asset"]) is not bool
            or type(case["requires_source"]) is not bool
            or type(case["read_only"]) is not bool
            or (
                case["expected_policy"] is not None
                and case["expected_policy"] not in {
                    "READ_ONLY", "EXECUTION_PROPOSAL",
                }
            )
        ):
            raise ValueError("REPLY_QUALITY_FIXTURE_INVALID")
    return cases


def fixture_checksum(path: Path = FIXTURE) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def configuration_identity() -> dict[str, str]:
    return {
        "provider": os.environ["PLATFORM_AGENT_PROVIDER"],
        "protocol": os.environ["PLATFORM_AGENT_PROTOCOL"],
        "model": os.environ["PLATFORM_AGENT_MODEL"],
        "context_window_tokens": os.environ["PLATFORM_AGENT_CONTEXT_WINDOW_TOKENS"],
        "max_output_tokens": os.environ["PLATFORM_AGENT_MAX_OUTPUT_TOKENS"],
        "request_timeout_seconds": os.environ["PLATFORM_AGENT_REQUEST_TIMEOUT_SECONDS"],
        "segment_timeout_seconds": os.environ["PLATFORM_AGENT_SEGMENT_TIMEOUT_SECONDS"],
    }


def configuration_problem() -> tuple[str, list[str]] | None:
    missing = [name for name in REQUIRED_ENVIRONMENT if not os.environ.get(name)]
    if missing:
        return "MISSING_MODEL_CONFIGURATION", missing
    if os.environ["PLATFORM_AGENT_PROVIDER"] not in SUPPORTED_PROVIDERS:
        return "UNSUPPORTED_GATE_PROVIDER", ["PLATFORM_AGENT_PROVIDER"]
    return None
