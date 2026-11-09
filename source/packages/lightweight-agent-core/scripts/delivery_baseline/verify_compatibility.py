"""Verify a frozen V6 baseline with the currently installed consumer wheel.

The verifier deliberately consumes only public workflow/journal/recovery APIs.
It does not rewrite snapshots or emulate an older runtime.  The first archive
therefore establishes a producer baseline; only a later, differently
identified consumer can create evidence for a cross-version compatibility
claim.
"""

from __future__ import annotations

import argparse
import base64
import csv
import hashlib
from importlib.metadata import distribution, version
import io
import json
from pathlib import Path, PurePosixPath
import shutil
import sys
import tempfile
from typing import Any, Callable
import zipfile

import platform_lightweight_agent as agent


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return "sha256:" + digest.hexdigest()


def _normalized_distribution_name(value: str) -> str:
    return value.lower().replace("-", "_").replace(".", "_")


def _record_digest(path: Path) -> str:
    digest = hashlib.sha256(path.read_bytes()).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def _consumer_wheel_association(wheel: Path) -> dict[str, Any]:
    """Prove that ``wheel`` is the distribution imported by this process.

    A caller-provided digest alone says nothing about the code running the
    verifier. Wheel ``RECORD`` hashes give us a standard, upstream-owned link:
    compare every immutable wheel entry with the installed distribution and
    confirm that the imported package resolves to that distribution.
    """

    resolved = wheel.resolve()
    report: dict[str, Any] = {
        "verified": False,
        "wheel": str(resolved),
        "reason": "unverified",
        "verified_entries": 0,
    }
    if not resolved.is_file():
        report["reason"] = "wheel_missing"
        return report
    try:
        with zipfile.ZipFile(resolved) as archive:
            names = archive.namelist()
            metadata_name = next(
                name for name in names if name.endswith(".dist-info/METADATA")
            )
            record_name = next(
                name for name in names if name.endswith(".dist-info/RECORD")
            )
            metadata_text = archive.read(metadata_name).decode("utf-8")
            metadata = {
                key: value
                for line in metadata_text.splitlines()
                if ": " in line
                for key, value in (line.split(": ", 1),)
                if key in {"Name", "Version"}
            }
            installed = distribution("platform-lightweight-agent-core")
            if (
                _normalized_distribution_name(metadata.get("Name", ""))
                != "platform_lightweight_agent_core"
                or metadata.get("Version") != installed.version
            ):
                report["reason"] = "distribution_metadata_mismatch"
                return report
            imported = Path(agent.__file__).resolve()
            installed_init = Path(
                installed.locate_file("platform_lightweight_agent/__init__.py")
            ).resolve()
            if imported != installed_init:
                report["reason"] = "loaded_module_not_from_distribution"
                return report
            rows = csv.reader(
                io.StringIO(archive.read(record_name).decode("utf-8"))
            )
            mismatches: list[str] = []
            verified_entries = 0
            recorded_entries: set[str] = set()
            archive_entries = {
                name for name in names if name != record_name and not name.endswith("/")
            }
            for row in rows:
                if len(row) != 3:
                    mismatches.append("malformed-record-row")
                    continue
                relative, encoded_digest, raw_size = row
                if relative == record_name or not encoded_digest:
                    continue
                relative_path = PurePosixPath(relative)
                if (
                    relative_path.is_absolute()
                    or ".." in relative_path.parts
                    or relative not in archive_entries
                    or relative in recorded_entries
                ):
                    mismatches.append(relative)
                    continue
                recorded_entries.add(relative)
                if not encoded_digest.startswith("sha256="):
                    mismatches.append(relative)
                    continue
                wheel_bytes = archive.read(relative)
                installed_path = Path(installed.locate_file(relative))
                if not installed_path.is_file():
                    mismatches.append(relative)
                    continue
                expected_digest = encoded_digest.removeprefix("sha256=")
                wheel_digest = (
                    base64.urlsafe_b64encode(hashlib.sha256(wheel_bytes).digest())
                    .rstrip(b"=")
                    .decode("ascii")
                )
                if (
                    wheel_digest != expected_digest
                    or _record_digest(installed_path) != expected_digest
                    or (
                        raw_size
                        and (
                            len(wheel_bytes) != int(raw_size)
                            or installed_path.stat().st_size != int(raw_size)
                        )
                    )
                ):
                    mismatches.append(relative)
                    continue
                verified_entries += 1
            missing_record_entries = archive_entries - recorded_entries
            if missing_record_entries:
                mismatches.extend(sorted(missing_record_entries)[:8])
            report["verified_entries"] = verified_entries
            if mismatches:
                report["reason"] = "installed_files_do_not_match_wheel"
                report["mismatches"] = mismatches[:8]
                return report
            if verified_entries < 1:
                report["reason"] = "wheel_has_no_verifiable_entries"
                return report
    except (OSError, StopIteration, UnicodeDecodeError, ValueError, zipfile.BadZipFile):
        report["reason"] = "wheel_identity_invalid"
        return report
    report["verified"] = True
    report["reason"] = "record_matches_loaded_distribution"
    return report


def _consumer_identity_state(
    producer: dict[str, Any],
    consumer_wheel_sha256: str | None,
    association: dict[str, Any] | None,
) -> str:
    """Return ``verified_same``, ``verified_different`` or ``unknown``."""

    if (
        consumer_wheel_sha256 is None
        or association is None
        or association.get("verified") is not True
    ):
        return "unknown"
    return (
        "verified_same"
        if consumer_wheel_sha256 == producer.get("wheel_sha256")
        else "verified_different"
    )


def baseline_id(components: dict[str, str]) -> str:
    payload = json.dumps(
        components, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _load_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path.name} must contain an object")
    return value


def _verify_files(goldens: Path, report: dict[str, Any]) -> None:
    raw_files = report.get("files")
    if not isinstance(raw_files, list):
        raise ValueError("golden file inventory is missing")
    expected: set[str] = set()
    for item in raw_files:
        if (
            not isinstance(item, dict)
            or set(item) != {"path", "sha256"}
            or not isinstance(item["path"], str)
            or not isinstance(item["sha256"], str)
        ):
            raise ValueError("golden file inventory is malformed")
        relative = Path(item["path"])
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("golden file path escapes its archive")
        path = goldens / relative
        if not path.is_file() or _sha256(path) != item["sha256"]:
            raise ValueError(f"golden file failed integrity check: {relative}")
        expected.add(relative.as_posix())
    actual = {
        path.relative_to(goldens).as_posix()
        for path in goldens.rglob("*")
        if path.is_file() and path.name != "goldens.json"
    }
    if actual != expected:
        raise ValueError("golden file inventory is incomplete")


class _NoReplayExecutor:
    def __init__(self, bindings: list[dict[str, Any]]) -> None:
        self._bindings = {
            json.dumps(item["tool"], sort_keys=True): item["binding"]
            for item in bindings
        }
        self.calls = 0

    def execution_binding(self, tool: agent.ToolReference) -> dict[str, Any]:
        binding = self._bindings.get(json.dumps(tool.to_mapping(), sort_keys=True))
        if not isinstance(binding, dict):
            raise RuntimeError("completed fixture requested an unknown tool binding")
        return json.loads(json.dumps(binding))

    def execute(self, request: object) -> object:
        del request
        self.calls += 1
        raise AssertionError("a completed tool action must not be replayed")

    def reconcile(self, request: object) -> object:
        del request
        self.calls += 1
        raise AssertionError("a completed tool action must not be reconciled again")


def _definition_check(goldens: Path) -> dict[str, Any]:
    definitions = []
    for name in ("workflow.json", "tool-completed-workflow.json"):
        definition = agent.WorkflowSpec.from_mapping(_load_object(goldens / name))
        definitions.append(
            {
                "workflow_id": definition.workflow_id,
                "version": definition.version,
                "schema_version": definition.schema_version,
                "definition_digest": definition.definition_digest,
            }
        )
    return {"definitions": definitions}


def _approval_check(goldens: Path) -> dict[str, Any]:
    expected = _load_object(goldens / "waiting.json")
    with tempfile.TemporaryDirectory(prefix="v6-compat-approval-") as raw:
        restored, _workspace = agent.restore_local_recovery_package(
            goldens / "local-recovery.zip", Path(raw) / "restored"
        )
        released = restored.release_backup_recovery(
            str(expected["run_id"]), source_owner_stopped=True
        )
        if released.execution_id != expected["execution_id"]:
            raise AssertionError("approval fixture execution identity changed")
        original_approvals = set(
            expected["state"]["v3"]["pending_approvals"]
        )
        restored_approvals = set(released.state["v3"]["pending_approvals"])
        if restored_approvals != original_approvals or len(restored_approvals) != 1:
            raise AssertionError("approval identity changed during restore")
        approval_id = next(iter(restored_approvals))
        completed = agent.WorkflowEngine(
            restored, agent.LocalToolExecutor(), {}
        ).resume(
            released.handle,
            approvals=(agent.ApprovalDecision(approval_id, True),),
        )
        if completed.status != "SUCCEEDED" or completed.state["v3"]["output"] != {
            "approved": True
        }:
            raise AssertionError("approval fixture did not complete")
        return {
            "run_id": completed.run_id,
            "execution_id_preserved": completed.execution_id
            == expected["execution_id"],
            "approval_id_preserved": True,
            "final_status": completed.status,
        }


def _completed_check(goldens: Path) -> dict[str, Any]:
    expected = _load_object(goldens / "tool-completed-snapshot.json")
    manifest = _load_object(goldens / "tool-completed-execution-manifest.json")
    raw_bindings = manifest.get("tool_bindings")
    if not isinstance(raw_bindings, list):
        raise ValueError("completed fixture has no frozen tool bindings")
    with tempfile.TemporaryDirectory(prefix="v6-compat-completed-") as raw:
        target = Path(raw) / "tool-completed.sqlite"
        for source in goldens.glob("tool-completed.sqlite*"):
            shutil.copy2(source, Path(raw) / source.name)
        journal = agent.SQLiteRunJournal(target)
        before = journal.get(str(expected["run_id"]))
        if before.execution_id != expected["execution_id"]:
            raise AssertionError("completed fixture execution identity changed")
        executor = _NoReplayExecutor(raw_bindings)
        resumed = agent.WorkflowEngine(journal, executor, {}).resume(before.handle)
        if resumed.status != "SUCCEEDED" or resumed.state["v3"]["output"] != {
            "value": 7
        }:
            raise AssertionError("completed fixture could not be read")
        if executor.calls != 0:
            raise AssertionError("completed fixture replayed an action")
        return {
            "run_id": resumed.run_id,
            "execution_id_preserved": resumed.execution_id
            == expected["execution_id"],
            "final_status": resumed.status,
            "tool_dispatches_or_queries": executor.calls,
        }


def _run_check(name: str, function: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    try:
        details = function()
    except (agent.WorkflowRunError, agent.WorkflowValidationError) as error:
        # A future consumer may intentionally refuse an older manifest/schema.
        # That is a valid, fail-closed upgrade outcome, distinct from both a
        # compatible resume and an infrastructure/test failure.
        return {
            "name": name,
            "result": "SAFE_REJECTION",
            "error": type(error).__name__,
            "message": str(error)[:512],
        }
    except Exception as error:  # noqa: BLE001 - report a total compatibility result
        return {
            "name": name,
            "result": "FAIL",
            "error": type(error).__name__,
            "message": str(error)[:512],
        }
    return {"name": name, "result": "PASS", "details": details}


def verify(
    baseline: Path,
    *,
    consumer_source_sha256: str | None = None,
    consumer_wheel: Path | None = None,
) -> dict[str, Any]:
    baseline = baseline.resolve()
    provenance = _load_object(baseline / "provenance.json")
    if provenance.get("schema_version") != "v6-compatibility-baseline-v1":
        raise ValueError("unsupported compatibility baseline")
    producer = provenance.get("producer")
    components = provenance.get("identity_components")
    if not isinstance(producer, dict) or not isinstance(components, dict):
        raise ValueError("compatibility provenance is malformed")
    if provenance.get("baseline_id") != baseline_id(components):
        raise ValueError("compatibility baseline identity is invalid")
    source_inputs = _load_object(baseline / "source-inputs.json")
    wheel = baseline / str(producer["wheel_name"])
    goldens = baseline / "goldens"
    goldens_report = _load_object(goldens / "goldens.json")
    integrity = {
        "source": source_inputs.get("sha256") == producer.get("source_sha256"),
        "wheel": wheel.is_file()
        and _sha256(wheel) == producer.get("wheel_sha256"),
        "lock": _sha256(baseline / "uv.lock") == producer.get("lock_sha256"),
        "goldens": _sha256(goldens / "goldens.json")
        == producer.get("goldens_sha256"),
        "golden_schema": goldens_report.get("schema_version")
        == "v6-current-wheel-goldens-v2",
        "producer_version": goldens_report.get("producer_version")
        == producer.get("package_version"),
    }
    if not all(integrity.values()):
        raise ValueError("compatibility baseline failed provenance checks")
    _verify_files(goldens, goldens_report)

    consumer_wheel_sha256 = (
        _sha256(consumer_wheel.resolve()) if consumer_wheel is not None else None
    )
    association = (
        _consumer_wheel_association(consumer_wheel)
        if consumer_wheel is not None
        else None
    )
    identity_state = _consumer_identity_state(
        producer, consumer_wheel_sha256, association
    )
    consumer = {
        "package_version": version("platform-lightweight-agent-core"),
        "python": sys.version,
        "module": agent.__file__,
        "source_sha256": consumer_source_sha256,
        "wheel_sha256": consumer_wheel_sha256,
        "wheel_association": association,
        "identity_status": identity_state,
        "source_claim_status": (
            "absent"
            if consumer_source_sha256 is None
            else (
                "matches_producer"
                if consumer_source_sha256 == producer.get("source_sha256")
                else "differs_from_producer"
            )
        ),
    }
    checks = [
        _run_check("workflow_definitions", lambda: _definition_check(goldens)),
        _run_check("approval_waiting_resume", lambda: _approval_check(goldens)),
        _run_check("tool_completed_no_replay", lambda: _completed_check(goldens)),
    ]
    passed = all(item["result"] == "PASS" for item in checks)
    assessed = all(
        item["result"] in {"PASS", "SAFE_REJECTION"} for item in checks
    )
    safely_rejected = assessed and not passed
    if not assessed:
        result = "INCOMPATIBLE"
    elif safely_rejected:
        result = "INCOMPATIBLE_SAFE_REJECTION"
    elif identity_state == "verified_same":
        result = "BASELINE_ESTABLISHED"
    elif identity_state == "verified_different":
        result = "COMPATIBLE_CANDIDATE"
    else:
        result = "IDENTITY_UNKNOWN"

    def claim_status(*names: str) -> str:
        selected = [item for item in checks if item["name"] in names]
        if selected and all(item["result"] == "PASS" for item in selected):
            if identity_state == "verified_same":
                return "baseline_established"
            if identity_state == "verified_different":
                return "compatible"
            return "identity_unknown"
        if selected and all(
            item["result"] in {"PASS", "SAFE_REJECTION"} for item in selected
        ):
            return "safe_rejection"
        return "invalid_or_unexpected_failure"

    matrix = {
        "schema_version": "v6-compatibility-matrix-v1",
        "baseline_id": provenance["baseline_id"],
        "producer": producer,
        "consumer": consumer,
        "claims": [
            {
                "scope": "workflow_definition",
                "status": claim_status("workflow_definitions"),
                "evidence": "workflow_definitions",
            },
            {
                "scope": "runtime_record",
                "status": claim_status(
                    "approval_waiting_resume", "tool_completed_no_replay"
                ),
                "evidence": [
                    "approval_waiting_resume",
                    "tool_completed_no_replay",
                ],
            },
            {
                "scope": "driver_continuation",
                "status": "exact_driver_binding_only_not_exercised",
                "cross_driver": "unsupported",
                "evidence": None,
            },
        ],
        "historical_cross_version_claim": bool(
            passed and identity_state == "verified_different"
        ),
    }
    return {
        "schema_version": "v6-compatibility-verification-v1",
        "result": result,
        "baseline_id": provenance["baseline_id"],
        "classification": (
            "first_real_baseline_not_prior_version_evidence"
            if result == "BASELINE_ESTABLISHED"
            else (
                "consumer_safely_rejected_frozen_producer"
                if safely_rejected
                else (
                    "consumer_candidate_checked_against_frozen_producer"
                    if result == "COMPATIBLE_CANDIDATE"
                    else "consumer_identity_not_proven"
                )
            )
        ),
        "integrity": integrity,
        "consumer_identity": consumer,
        "checks": checks,
        "matrix": matrix,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--matrix-output", type=Path)
    parser.add_argument("--consumer-source-sha256")
    parser.add_argument("--consumer-wheel", type=Path)
    args = parser.parse_args()
    try:
        report = verify(
            args.baseline,
            consumer_source_sha256=args.consumer_source_sha256,
            consumer_wheel=args.consumer_wheel,
        )
    except Exception as error:  # noqa: BLE001 - always leave inspectable evidence
        report = {
            "schema_version": "v6-compatibility-verification-v1",
            "result": "INVALID_BASELINE",
            "error": type(error).__name__,
            "message": str(error)[:512],
        }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    matrix = report.get("matrix")
    if args.matrix_output is not None and isinstance(matrix, dict):
        args.matrix_output.write_text(
            json.dumps(matrix, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report.get("result") in {
        "BASELINE_ESTABLISHED",
        "COMPATIBLE_CANDIDATE",
        "INCOMPATIBLE_SAFE_REJECTION",
    } else 1


if __name__ == "__main__":
    raise SystemExit(main())
