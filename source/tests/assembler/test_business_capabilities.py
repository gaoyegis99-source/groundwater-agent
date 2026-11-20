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


@pytest.mark.parametrize("capability_id", ["groundwater.monitoring-trend", "groundwater.flow-field"])
def test_business_capability_is_generated_from_exact_profile_and_result(business_bundle, capability_id):
    ui_payload = (business_bundle / "product-ui.json").read_bytes()
    ui = json.loads(ui_payload)
    config = _read(business_bundle / "kernel-config.json")
    lock = _read(business_bundle / "assembly-lock.json")
    platform = _read(business_bundle / "workflow-platform.json")
    matches = [item for item in ui["businessCapabilities"] if item["ref"]["id"] == capability_id]
    assert len(matches) == 1, f"Missing or duplicate Business Capability {capability_id}"
    capability, = matches
    runtime, = [item for item in config["businessCapabilities"] if item["ref"]["id"] == capability_id]
    assert runtime["ref"] == capability["ref"]
    assert capability["ref"] in lock["businessCapabilities"]["refs"]
    assert capability["tier"] == "CORE" and capability["defaultVisible"]
    assert capability["validationStatus"] == "TECHNICALLY_VALIDATED"
    assert lock["productUi"]["profileOutputChecksum"] == "sha256:" + hashlib.sha256(ui_payload).hexdigest()
    option, = capability["profiles"]
    original, = [item for item in platform["profiles"] if item["ref"] == option["profileRef"]]
    assert option["profileRef"] == original["ref"]
    assert all(option[key] == original[key] for key in ("modelRef", "workflowRef", "resultContractRef"))
    assembled_outputs = {item["name"]: item for item in option["outputs"]}
    contract, = [item for item in platform["resultContracts"] if item["ref"] == option["resultContractRef"]]
    assert assembled_outputs.keys() == {item["name"] for item in contract["outputs"]}
    for output in contract["outputs"]:
        display = assembled_outputs[output["name"]]
        assert display["mediaType"] == output["mediaType"]
        assert display["qualitySource"] == output["qualitySource"]
        assert display.get("unit") == output.get("unit")
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
    capability, = [item for item in document["capabilities"] if item["id"] == "groundwater.monitoring-trend"]
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


def test_flow_field_core_launch_uses_the_exact_profile_and_requires_physical_parameters(business_bundle):
    config = _read(business_bundle / "kernel-config.json")
    ui = _read(business_bundle / "product-ui.json")
    flow, = [item for item in ui["businessCapabilities"] if item["ref"]["id"] == "groundwater.flow-field"]
    assert flow["exampleId"] == "flow"
    assert flow["roles"] == ["admin", "researcher"]
    assert flow["requiredServices"] == ["data", "executor"]
    option, = flow["profiles"]
    assert (option["profileRef"]["id"], option["profileRef"]["version"]) == (
        "groundwater.flow-field.same-time-darcy", "1.0.0",
    )
    assert (option["modelRef"]["id"], option["modelRef"]["version"]) == ("groundwater.flow-field", "3.1.0")
    assert (option["workflowRef"]["id"], option["workflowRef"]["version"]) == ("groundwater.flow-field", "1.1.0")
    assert (option["resultContractRef"]["id"], option["resultContractRef"]["version"]) == (
        "groundwater.flow-field.results", "1.0.0",
    )
    assert {item["name"] for item in option["outputs"]} == {
        "snapshot", "snapshot-diagnostics", "head-raster", "gradient", "flow-direction",
        "darcy-flux", "seepage-velocity", "summary",
    }
    snapshot = next(item for item in option["outputs"] if item["name"] == "snapshot")
    assert snapshot["qualitySource"] == {"outputName": "summary", "fields": ["resultKind"]}

    runtime = tuple(config["businessCapabilities"])
    selection = select_business_capability(runtime, flow["prompt"], frozenset({"researcher"}), [])
    business = selection["business_capability"]
    assert all(business[key] == option[key] for key in ("profileRef", "modelRef", "workflowRef", "resultContractRef"))
    assert set(business["requiredChecks"]) == {
        "FIELD_METADATA", "FIELD_UNIT", "TIME_FIELD", "UTC_TIME_FIELD", "TIME_RANGE", "CRS", "CRS_PARAMETER",
        "VERTICAL_BASIS",
    }
    physical_parameters = {
        "targetTime", "sourceCrs", "targetCrs", "pixelSize", "hydraulicConductivityMetersPerDay", "effectivePorosity",
    }
    assert physical_parameters <= {item["name"] for item in business["requiredParameters"]}
    assert physical_parameters.isdisjoint(selection["parameters"])
    assert selection["parameters"]["selection"] == "exact"
    assert selection["parameters"]["maxAgeDays"] == 0


@pytest.mark.parametrize(("text", "name", "expected"), [
    ("渗透系数=1e1 米/天", "hydraulicConductivityMetersPerDay", 10.0),
    ("K 为 10 米/日", "hydraulicConductivityMetersPerDay", 10.0),
    ("补充参数 hydraulicConductivityMetersPerDay：10 m/day", "hydraulicConductivityMetersPerDay", 10.0),
    ("渗透系数=10", "hydraulicConductivityMetersPerDay", None),
    ("渗透系数=10 m/s", "hydraulicConductivityMetersPerDay", None),
    ("渗透系数=10 m/day/s", "hydraulicConductivityMetersPerDay", None),
    ("渗透系数=10 m/day，渗透系数=2 m/s", "hydraulicConductivityMetersPerDay", None),
    ("渗透系数=10 m/day。本次渗透系数待定", "hydraulicConductivityMetersPerDay", None),
    ("格网大小=250 米", "pixelSize", 250.0),
    ("格网大小=250 cm", "pixelSize", None),
    ("有效孔隙率=0.25", "effectivePorosity", 0.25),
    ("有效孔隙率=0.25%", "effectivePorosity", None),
    ("有效孔隙率=0.25 任意单位", "effectivePorosity", None),
    ("目标时间=2024-01-03T00:00:00+00:00", "targetTime", "2024-01-03T00:00:00+00:00"),
    ("目标时间=2024-01-03T08:00:00+08:00", "targetTime", None),
    ("目标时间=2024-01-03T08:00:00", "targetTime", None),
    ("目标坐标系=epsg:3857", "targetCrs", "epsg:3857"),
    ('井号字段是 "Well ID"', "wellField", "Well ID"),
])
def test_flow_field_parameter_parser_uses_the_declared_units_and_time_offset(business_bundle, text, name, expected):
    config = _read(business_bundle / "kernel-config.json")
    query = f"估算地下水流场。{text}"
    selection = select_business_capability(tuple(config["businessCapabilities"]), query, frozenset({"researcher"}), [])
    assert selection is not None
    assert selection["parameters"].get(name) == expected


@pytest.mark.parametrize("query", [
    "估算地下水流场，使用最接近目标时刻的观测。",
    "估算地下水流场，选择 latestAtOrBefore。",
    "Estimate a groundwater flow field from the nearest observation for each well.",
    "估算地下水流场，selection = nearest。",
    "估算地下水流场，maxAgeDays=2。",
    "估算地下水流场，neighborCount=10。",
    "估算地下水流场，power = 4。",
    "估算地下水流场，referenceElevationUnit=cm。",
])
def test_flow_field_declines_requests_that_override_fixed_profile_settings(business_bundle, query):
    config = _read(business_bundle / "kernel-config.json")
    assert select_business_capability(tuple(config["businessCapabilities"]), query, frozenset({"researcher"}), []) is None


@pytest.mark.parametrize("query", [
    "估算非稳态地下水流场。",
    "Estimate a transient groundwater flow field.",
])
def test_flow_field_declines_requests_for_transient_simulation(business_bundle, query):
    config = _read(business_bundle / "kernel-config.json")
    assert select_business_capability(tuple(config["businessCapabilities"]), query, frozenset({"researcher"}), []) is None
