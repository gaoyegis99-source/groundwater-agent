from __future__ import annotations

import json
from pathlib import Path
import runpy
import subprocess
import sys

import pytest
from platform_lightweight_agent import WorkflowRunError

ROOT = Path(__file__).resolve().parents[1]
SOURCES = runpy.run_path(str(ROOT / "scripts" / "source_inputs.py"))
SUITES = runpy.run_path(str(ROOT / "scripts" / "verify_test_suite.py"))
REPORTING = runpy.run_path(
    str(ROOT / "tests/defect_discovery/reporting/compile_report.py")
)
CAPTURE_GOLDENS = runpy.run_path(
    str(ROOT / "scripts/delivery_baseline/capture_goldens.py")
)
COMPATIBILITY = runpy.run_path(
    str(ROOT / "scripts/delivery_baseline/verify_compatibility.py")
)


def _input_source(root: Path) -> None:
    (root / "src").mkdir()
    (root / "src" / "tool.py").write_text("value = 1\n", encoding="utf-8")
    (root / "verification-profile.json").write_text(
        json.dumps(
            {
                "schema_version": "v6-developer-verification-profile-v1",
                "input_patterns": ["verification-profile.json", "src/**/*.py"],
            }
        ),
        encoding="utf-8",
    )


def test_input_identity_uses_declared_inputs_not_caches_or_git(tmp_path: Path) -> None:
    _input_source(tmp_path)
    manifest = SOURCES["input_manifest"]
    before = manifest(tmp_path)
    (tmp_path / ".import_linter_cache").mkdir()
    (tmp_path / ".import_linter_cache" / "cache.json").write_text("{}")
    (tmp_path / "arbitrary-new-cache").mkdir()
    (tmp_path / "arbitrary-new-cache" / "state.json").write_text("{}")
    assert manifest(tmp_path) == before
    (tmp_path / "src" / "untracked.py").write_text("value = 2\n")
    assert manifest(tmp_path)["sha256"] != before["sha256"]
    assert any(
        item["path"] == "src/untracked.py" for item in manifest(tmp_path)["files"]
    )


def test_materialized_source_matches_the_input_identity(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    _input_source(source)
    destination = tmp_path / "captured"
    expected = SOURCES["copy_inputs"](source, destination)
    assert SOURCES["input_manifest"](destination) == expected
    (source / "src" / "escape.py").symlink_to(destination / "src" / "tool.py")
    with pytest.raises(ValueError, match="symlink"):
        SOURCES["input_manifest"](source)


def test_suite_evidence_rejects_missing_skipped_and_incomplete_tests(
    tmp_path: Path,
) -> None:
    recorder = SUITES["SuiteEvidence"](tmp_path, ["tests/test_required.py"])
    assert recorder.result(0)["result"] == "FAIL"
    recorder.collected = ["tests/test_required.py::test_one"]
    recorder.phases = {
        recorder.collected[0]: {
            "setup": "passed",
            "call": "passed",
            "teardown": "passed",
        }
    }
    assert recorder.result(0)["result"] == "PASS"
    recorder.phases[recorder.collected[0]]["call"] = "skipped"
    assert recorder.result(0)["result"] == "FAIL"
    recorder.phases[recorder.collected[0]]["call"] = "passed"
    recorder.phases[recorder.collected[0]]["teardown"] = "failed"
    assert recorder.result(0)["result"] == "FAIL"


@pytest.mark.parametrize(
    "variant", ["pass", "skip", "missing_dependency", "missing_suite"]
)
def test_real_pytest_gate_is_fail_closed(tmp_path: Path, variant: str) -> None:
    tests = tmp_path / "tests"
    tests.mkdir()
    body = "import pytest\n\ndef test_example():\n    "
    body += (
        "pytest.skip('intentional negative control')\n"
        if variant == "skip"
        else "assert True\n"
    )
    (tests / "test_example.py").write_text(body, encoding="utf-8")
    profile = tmp_path / "profile.json"
    profile.write_text(
        json.dumps(
            {
                "schema_version": "v6-developer-verification-profile-v1",
                "required_distributions": {
                    "v6-intentionally-unavailable-test-dependency": "1"
                }
                if variant == "missing_dependency"
                else {},
                "required_suites": [
                    "tests/test_absent.py"
                    if variant == "missing_suite"
                    else "tests/test_example.py"
                ],
            }
        ),
        encoding="utf-8",
    )
    output = tmp_path / "evidence.json"
    completed = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "verify_test_suite.py"),
            "--root",
            str(tmp_path),
            "--profile",
            str(profile),
            "--output",
            str(output),
            "tests",
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    report = json.loads(output.read_text(encoding="utf-8"))
    assert (completed.returncode == 0) == (variant == "pass"), completed.stderr
    assert report["result"] == ("PASS" if variant == "pass" else "FAIL")


def test_delivery_gate_cannot_pass_when_a_required_phase_is_skipped() -> None:
    delivery = runpy.run_path(str(ROOT / "scripts/verify_developer_delivery.py"))
    result = delivery["delivery_result"]
    assert result([{"result": "passed"}], []) == "passed"
    assert result([{"result": "passed"}], ["clean_wheel"]) == "incomplete"
    assert result([{"result": "failed"}], []) == "failed"
    assert result([], []) == "failed"


def test_clean_wheel_gate_requires_all_facets_and_same_source() -> None:
    verify = runpy.run_path(str(ROOT / "scripts/verify_developer_delivery.py"))[
        "clean_wheel_passed"
    ]
    report = {
        "schema_version": "v6-delivery-baseline-v2",
        "source_baseline": {"source_tree_digest": "sha256:expected"},
        "acceptance": {
            name: True
            for name in (
                "clean_wheel_install",
                "cli_contract_check",
                "execution",
                "run_query",
                "execution_recovery",
                "resource_cleanup",
            )
        },
    }
    report["acceptance"]["skipped"] = []
    assert verify(report, "sha256:expected")
    assert not verify(report, "sha256:other")
    report["acceptance"]["execution_recovery"] = False
    assert not verify(report, "sha256:expected")
    del report["acceptance"]["execution_recovery"]
    assert not verify(report, "sha256:expected")
    assert not verify({}, "sha256:expected")


def test_compatibility_baseline_resumes_approval_and_never_replays_completed_tool(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive = tmp_path / "archive"
    archive.mkdir()
    goldens = archive / "goldens"
    golden_report = CAPTURE_GOLDENS["capture"](goldens)
    wheel = archive / (
        f"platform_lightweight_agent_core-{golden_report['producer_version']}"
        "-py3-none-any.whl"
    )
    wheel.write_bytes(b"unit-test-wheel-identity")
    lock = archive / "uv.lock"
    lock.write_text("version = 1\n", encoding="utf-8")
    source_sha256 = "sha256:" + "1" * 64
    (archive / "source-inputs.json").write_text(
        json.dumps({"schema_version": "v6-source-inputs-v1", "sha256": source_sha256}),
        encoding="utf-8",
    )
    components = {
        "source_sha256": source_sha256,
        "wheel_sha256": COMPATIBILITY["_sha256"](wheel),
        "lock_sha256": COMPATIBILITY["_sha256"](lock),
        "goldens_sha256": COMPATIBILITY["_sha256"](goldens / "goldens.json"),
    }
    producer = {
        "package_version": golden_report["producer_version"],
        "wheel_name": wheel.name,
        **components,
    }
    (archive / "provenance.json").write_text(
        json.dumps(
            {
                "schema_version": "v6-compatibility-baseline-v1",
                "baseline_id": COMPATIBILITY["baseline_id"](components),
                "identity_components": components,
                "producer": producer,
            }
        ),
        encoding="utf-8",
    )

    unknown = COMPATIBILITY["verify"](archive)
    assert unknown["result"] == "IDENTITY_UNKNOWN"
    assert unknown["classification"] == "consumer_identity_not_proven"
    assert unknown["matrix"]["historical_cross_version_claim"] is False
    assert {
        item["status"] for item in unknown["matrix"]["claims"][:2]
    } == {"identity_unknown"}

    monkeypatch.setitem(
        COMPATIBILITY["verify"].__globals__,
        "_consumer_wheel_association",
        lambda _path: {
            "verified": True,
            "reason": "unit-test-associated-wheel",
            "verified_entries": 1,
        },
    )
    report = COMPATIBILITY["verify"](
        archive,
        consumer_source_sha256=source_sha256,
        consumer_wheel=wheel,
    )

    assert report["result"] == "BASELINE_ESTABLISHED"
    assert report["classification"] == (
        "first_real_baseline_not_prior_version_evidence"
    )
    assert [item["result"] for item in report["checks"]] == [
        "PASS",
        "PASS",
        "PASS",
    ]
    completed = next(
        item for item in report["checks"] if item["name"] == "tool_completed_no_replay"
    )
    assert completed["details"]["tool_dispatches_or_queries"] == 0
    assert report["matrix"]["historical_cross_version_claim"] is False
    assert report["matrix"]["claims"][2]["status"] == (
        "exact_driver_binding_only_not_exercised"
    )

    (goldens / "tool-completed-snapshot.json").write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="integrity"):
        COMPATIBILITY["verify"](
            archive,
            consumer_source_sha256=source_sha256,
            consumer_wheel=wheel,
        )


def test_compatibility_identity_requires_an_associated_consumer_wheel() -> None:
    classify = COMPATIBILITY["_consumer_identity_state"]
    producer = {"wheel_sha256": "sha256:producer"}

    assert classify(producer, None, None) == "unknown"
    assert (
        classify(
            producer,
            "sha256:other",
            {"verified": False, "reason": "not-loaded"},
        )
        == "unknown"
    )
    assert (
        classify(producer, "sha256:producer", {"verified": True})
        == "verified_same"
    )
    assert (
        classify(producer, "sha256:other", {"verified": True})
        == "verified_different"
    )


def test_compatibility_verifier_distinguishes_safe_rejection_from_test_failure() -> (
    None
):
    rejected = COMPATIBILITY["_run_check"](
        "old_manifest",
        lambda: (_ for _ in ()).throw(
            WorkflowRunError("run has no compatible frozen execution manifest")
        ),
    )
    broken = COMPATIBILITY["_run_check"](
        "broken_probe",
        lambda: (_ for _ in ()).throw(RuntimeError("probe crashed")),
    )

    assert rejected["result"] == "SAFE_REJECTION"
    assert broken["result"] == "FAIL"


@pytest.mark.parametrize(
    "variant,expected",
    [
        ("all", "PASS"),
        ("missing", "NOT_RUN"),
        ("skipped", "NOT_RUN"),
        ("duplicate", "NOT_RUN"),
        ("wrong_schema", "NOT_RUN"),
        ("failed", "FAIL"),
        ("error", "ERROR"),
        ("conflict", "FAIL"),
    ],
)
def test_evidence_facets_cannot_cover_missing_dimensions(
    tmp_path: Path, variant: str, expected: str
) -> None:
    facet = {
        "schema": "probe-v1",
        "match": {"variant": "crash"},
        "dimensions": {"window": ["before", "after"], "repetition": [1, 2]},
    }
    assessments = {
        "schema_version": "v6-defect-discovery-assessments-v2",
        "cases": {
            case: {"disposition": "complete", "required_evidence": [facet]}
            for case in REPORTING["CASE_IDS"]
        },
    }
    entries = [
        {
            "case": "R01",
            "variant": "crash",
            "window": window,
            "repetition": repetition,
            "contract_result": "PASS",
        }
        for window in ["before", "after"]
        for repetition in [1, 2]
    ]
    if variant in {"missing", "duplicate"}:
        entries.pop()
        if variant == "duplicate":
            entries.extend([dict(entries[0])] * 20)
    if variant in {"skipped", "failed", "error"}:
        entries[-1]["contract_result"] = {
            "skipped": "NOT_ASSESSED",
            "failed": "FAIL",
            "error": "ERROR",
        }[variant]
    if variant == "conflict":
        entries.append({**entries[-1], "contract_result": "FAIL"})
    # An unrelated PASS and a partial NOT_ASSESSED cannot satisfy a required cell.
    entries += [
        {"case": "R01", "variant": "unrelated", "contract_result": "PASS"},
        {"case": "R01", "variant": "partial", "contract_result": "NOT_ASSESSED"},
    ]
    path = tmp_path / "evidence.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": "wrong-v1"
                if variant == "wrong_schema"
                else "probe-v1",
                "results": entries,
            }
        )
    )
    manifest = tmp_path / "assessments.json"
    manifest.write_text(json.dumps(assessments))
    report = REPORTING["compile_report"]([path], manifest)
    case = next(item for item in report["coverage"] if item["case"] == "R01")
    assert case["contract_result"] == expected
    assert bool(case["missing_evidence"]) == (variant != "all")


def test_shipped_evidence_manifest_requires_every_case_and_live_facets() -> None:
    cases, _ = REPORTING["_assessments"](
        ROOT / "tests/defect_discovery/reporting/assessments.json"
    )
    assert len(cases) == 52
    assert any(
        f["schema"] == "v6-real-provider-evidence-v1"
        for f in cases["M04"]["required_evidence"]
    )
    assert any(
        f["schema"] == "v6-real-container-evidence-v1"
        for f in cases["X03"]["required_evidence"]
    )


@pytest.mark.parametrize("result", ["ERROR", "FAIL"])
def test_unattributed_source_error_cannot_be_hidden_by_passing_facets(tmp_path, result):
    facet = {"schema": "probe-v1", "match": {"variant": "test"}}
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "schema_version": "v6-defect-discovery-assessments-v2",
                "cases": {
                    case: {"disposition": "complete", "required_evidence": [facet]}
                    for case in REPORTING["CASE_IDS"]
                },
            }
        )
    )
    evidence = tmp_path / "evidence.json"
    evidence.write_text(
        json.dumps(
            {
                "schema_version": "probe-v1",
                "result": result,
                "results": [
                    {"case": case, "variant": "test", "contract_result": "PASS"}
                    for case in REPORTING["CASE_IDS"]
                ],
            }
        )
    )
    report = REPORTING["compile_report"]([evidence], manifest)
    assert report["result"] == "ERROR"
    assert report["source_errors"]
