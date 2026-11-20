import hashlib
import json
from pathlib import Path
import sys

import pytest


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "platform/assembler"))
from assembler import compile_product  # noqa: E402
from assembler_business_capabilities import load_business_capabilities  # noqa: E402
from executor_profile import ExecutorOperationalProfile  # noqa: E402
from platform_kernel.business_capabilities import select_business_capability  # noqa: E402


PRODUCT = ROOT / "products/groundwater"


def _read(path):
    return json.loads(path.read_bytes())


@pytest.fixture(scope="module")
def business_bundle(tmp_path_factory):
    with pytest.MonkeyPatch.context() as env:
        env.setenv("PLATFORM_GENERATED_ROOT", str(tmp_path_factory.mktemp("business-capabilities")))
        return compile_product(
            ROOT, PRODUCT / "product-manifest.json", executor_profile=ExecutorOperationalProfile(),
        )


def test_business_capability_is_generated_from_exact_profile_and_result(business_bundle):
    ui_payload = (business_bundle / "product-ui.json").read_bytes()
    ui = json.loads(ui_payload)
    config = _read(business_bundle / "kernel-config.json")
    lock = _read(business_bundle / "assembly-lock.json")
    platform = _read(business_bundle / "workflow-platform.json")
    capability, = ui["businessCapabilities"]
    runtime, = config["businessCapabilities"]
    assert runtime["ref"] == capability["ref"] == lock["businessCapabilities"]["refs"][0]
    assert capability["ref"]["id"] == "groundwater.monitoring-trend"
    assert capability["tier"] == "CORE" and capability["defaultVisible"]
    assert capability["validationStatus"] == "TECHNICALLY_VALIDATED"
    assert lock["productUi"]["profileOutputChecksum"] == "sha256:" + hashlib.sha256(ui_payload).hexdigest()
    option, = capability["profiles"]
    original, = platform["profiles"]
    assert option["profileRef"] == original["ref"]
    assert all(option[key] == original[key] for key in ("modelRef", "workflowRef", "resultContractRef"))
    assert {item["name"] for item in option["outputs"]} == {
        item["name"] for item in platform["resultContracts"][0]["outputs"]
    }
    assert option["inputs"][0]["formats"] == ["arrow-ipc-file", "csv", "parquet"]
    linked = next(item for item in platform["workflows"] if item["workflowRef"] == original["workflowRef"])
    assert {
        "kind": "BUSINESS_CAPABILITY", "ref": capability["ref"], "profileRef": option["profileRef"],
    } in linked["models"][0]["entries"]


@pytest.mark.parametrize(("suffix", "threshold", "unit"), [
    ("补充参数 eventThreshold：10 m", 10.0, "m"),
    ("识别高于 1e1 米的阈值事件", 10.0, "米"),
    ("识别高于 10 ft 的阈值事件", 10.0, "ft"),
    ("筛查高于 10英尺的事件", 10.0, "英尺"),
    ("识别高于 10,000 m 的阈值事件", None, None),
    ("旧报告使用高于 10 m，本次阈值尚未确定", None, None),
    ("识别高于 10 m 的阈值事件仅供示例。本次事件阈值待定", None, None),
])
def test_product_threshold_parser_keeps_an_explicit_unit_and_never_truncates_a_number(
    business_bundle, suffix, threshold, unit,
):
    runtime = tuple(_read(business_bundle / "kernel-config.json")["businessCapabilities"])
    selection = select_business_capability(
        runtime, f"监测趋势，检查季节趋势和阈值事件。{suffix}", frozenset({"researcher"}), [],
    )
    assert selection["parameters"].get("eventThreshold") == threshold
    assert selection["business_capability"]["thresholdUnit"] == unit


@pytest.mark.parametrize("mutation", [
    "bad-pin", "missing-roles", "missing-service", "advanced-default", "unknown-parameter",
    "fixed-parameter", "unsupported-pattern", "unvalidated-default", "missing-example",
])
def test_registry_refuses_invalid_capability_even_after_repinning(business_bundle, tmp_path, mutation):
    manifest = _read(PRODUCT / "product-manifest.json")
    document = _read(PRODUCT / manifest["businessCapabilities"]["path"])
    capability, = document["capabilities"]
    option, = capability["profiles"]
    if mutation == "bad-pin":
        option["profileRef"]["checksum"] = "sha256:" + "0" * 64
    elif mutation == "missing-roles":
        capability["roles"] = ["viewer"]
    elif mutation == "missing-service":
        capability["requiredServices"].append("other-missing-service")
    elif mutation == "advanced-default":
        capability["tier"] = "ADVANCED"
    elif mutation == "unknown-parameter":
        option["textParameters"][0]["name"] = "notAnArgument"
    elif mutation == "fixed-parameter":
        option["textParameters"][0]["name"] = "thresholdEventsEnabled"
    elif mutation == "unsupported-pattern":
        option["textParameters"][0]["patterns"][0]["pattern"] = "(?P<ignored>.*)"
    elif mutation == "unvalidated-default":
        capability["validationStatus"] = "UNVERIFIED"
    else:
        capability["exampleId"] = "absent"
    source = tmp_path / "capabilities.json"
    source.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
    ref = {"path": source.name, "checksum": "sha256:" + hashlib.sha256(source.read_bytes()).hexdigest()}
    config = _read(business_bundle / "kernel-config.json")
    with pytest.raises(ValueError, match="BUSINESS_CAPABILITY"):
        load_business_capabilities(
            tmp_path, ref, platform=_read(business_bundle / "workflow-platform.json"),
            models=_read(business_bundle / "executor-config.json")["models"],
            ui=_read(business_bundle / "product-ui.json"), service_names=set(config["services"]),
        )
