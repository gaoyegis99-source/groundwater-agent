"""A capability may require only checks supported by its pinned model."""

import hashlib
import json
from pathlib import Path

import pytest

from tests.assembler.test_business_capabilities import business_bundle as business_bundle
from assembler_business_capabilities import load_business_capabilities


@pytest.mark.parametrize("check", ["CRS", "CRS_PARAMETER", "VERTICAL_BASIS"])
def test_requires_checks_supported_by_the_pinned_model(business_bundle, tmp_path: Path, check):
    source = Path(__file__).resolve().parents[2] / "products/groundwater/capabilities/business-capabilities.v1.json"
    document = json.loads(source.read_bytes())
    monitoring = next(item for item in document["capabilities"] if item["id"] == "groundwater.monitoring-trend")
    monitoring["profiles"][0]["requiredChecks"] = [check]
    payload = json.dumps({"schemaVersion": document["schemaVersion"], "capabilities": [monitoring]}).encode()
    (tmp_path / "capabilities.json").write_bytes(payload)
    ref = {"path": "capabilities.json", "checksum": "sha256:" + hashlib.sha256(payload).hexdigest()}
    config = json.loads((business_bundle / "kernel-config.json").read_bytes())
    with pytest.raises(ValueError, match="unsupported mandatory preparation check"):
        load_business_capabilities(
            tmp_path, ref,
            platform=json.loads((business_bundle / "workflow-platform.json").read_bytes()),
            models=json.loads((business_bundle / "executor-config.json").read_bytes())["models"],
            ui=json.loads((business_bundle / "product-ui.json").read_bytes()),
            service_names=set(config["services"]),
        )
