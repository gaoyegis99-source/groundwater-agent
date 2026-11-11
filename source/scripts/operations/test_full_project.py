#!/usr/bin/env python3
"""Run the complete deterministic project validation pipeline.

This is an orchestration facade.  The component and Compose checks remain the
owners of their assertions; this command records their execution in one
machine-readable report and never reuses an existing deployment.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys

# Pin direct checks to the owning project's Docker launcher.
import sys as _maintenance_sys  # noqa: E402
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from pathlib import Path as _MaintenancePath  # noqa: E402
from typing import Any, Sequence

_maintenance_source = next(
    parent
    for parent in _MaintenancePath(__file__).resolve().parents
    if (parent / "scripts/maintenance/project_environment.py").is_file()
)
_maintenance_sys.path.insert(0, str(_maintenance_source))
from scripts.checks.v6_test_suite import (  # noqa: E402
    V6_PYTEST_SLICES,
    V6_UI_WORKSPACES,
    pytest_command,
)
from scripts.maintenance.project_environment import (  # noqa: E402
    docker_executable,
    validation_cache_root,
    validation_state_root,
)
from scripts.maintenance.dependency_materializer import (  # noqa: E402
    root as dependency_material_root,
)
from scripts.maintenance.xdg_layout import resolve_lifecycle_paths  # noqa: E402
from scripts.operations.render_full_project_report import (  # noqa: E402
    write_markdown_report,
)

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT = validation_state_root(ROOT) / "current/full-project-test-report.json"
PYTHON = sys.executable


def _build_tool() -> list[str]:
    if shutil.which("uv") is None:
        raise RuntimeError("UV_MISSING")
    return ["uv", "build"]


def _build_check_command() -> list[str]:
    tool = _build_tool()
    return [*tool, "--help"]


def _offline_wheel_build_command(project: Path, wheel_dir: Path) -> list[str]:
    """Build with only the digest-addressed, hash-locked backend wheelhouse."""

    wheelhouse = (
        dependency_material_root(ROOT, resolve_lifecycle_paths()) / "python" / "build"
    )
    if not wheelhouse.is_dir():
        raise RuntimeError("BUILD_WHEELHOUSE_MISSING")
    return [
        *_build_tool(),
        "--wheel",
        "--out-dir",
        str(wheel_dir),
        "--build-constraints",
        str(ROOT / "environments" / "build" / "requirements.lock"),
        "--require-hashes",
        "--no-index",
        "--find-links",
        str(wheelhouse),
        "--offline",
        "--no-cache",
        "--no-config",
        "--python",
        PYTHON,
        "--no-python-downloads",
        str(project),
    ]


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _git_status() -> list[str]:
    result = subprocess.run(
        ["git", "status", "--short"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.splitlines()


def _status_sha256(status: list[str]) -> str:
    return hashlib.sha256(("\n".join(status) + "\n").encode("utf-8")).hexdigest()


def _version(command: Sequence[str]) -> str:
    result = subprocess.run(
        list(command), cwd=ROOT, capture_output=True, text=True, check=False
    )
    output = (result.stdout or result.stderr).strip().splitlines()
    return output[0][:240] if output else f"exit:{result.returncode}"


def _sha256_tree(path: Path) -> str:
    digest = hashlib.sha256()
    if path.is_file():
        digest.update(path.read_bytes())
        return digest.hexdigest()
    for child in sorted(item for item in path.rglob("*") if item.is_file()):
        digest.update(child.relative_to(path).as_posix().encode("utf-8"))
        digest.update(child.read_bytes())
    return digest.hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


_SENSITIVE_LOG_KEYS = {
    "actor",
    "body",
    "caseid",
    "content",
    "datasetid",
    "detail",
    "displayname",
    "error",
    "filename",
    "importid",
    "inputreferences",
    "inputversions",
    "jobid",
    "knowledgeid",
    "markdown",
    "message",
    "objectid",
    "path",
    "payloadjson",
    "projectid",
    "prompt",
    "raw",
    "requestid",
    "requestjson",
    "resourceid",
    "responsejson",
    "semanticid",
    "sessionid",
    "subject",
    "traceid",
    "versionid",
    "workflowrunid",
}
_JSON_DECODER = json.JSONDecoder()
_LOG_QUERY = re.compile(
    r"(?P<method>\b(?:GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)\s+\S+?)\?\S+(?P<tail>\s+HTTP/[0-9.]+)"
)
_UUID = re.compile(
    r"\b[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\b",
    re.IGNORECASE,
)
_HEX_ID = re.compile(r"\b[0-9a-f]{32}\b", re.IGNORECASE)


def _redact_log_value(value: Any, key: str | None = None) -> Any:
    """Remove user/resource payloads from evidence JSON while retaining shape."""
    normalized = key.replace("_", "").lower() if isinstance(key, str) else ""
    if normalized in _SENSITIVE_LOG_KEYS:
        return "<redacted>"
    if isinstance(value, dict):
        return {
            name: _redact_log_value(item, str(name)) for name, item in value.items()
        }
    if isinstance(value, list):
        return [_redact_log_value(item) for item in value]
    return value


def _redact_log_line(line: str) -> str:
    """Redact a single line without hiding stable status and timing fields."""
    brace = line.find("{")
    if brace >= 0:
        try:
            value, consumed = _JSON_DECODER.raw_decode(line[brace:])
        except json.JSONDecodeError:
            pass
        else:
            redacted = json.dumps(
                _redact_log_value(value), ensure_ascii=False, separators=(",", ":")
            )
            line = line[:brace] + redacted + line[brace + consumed :]
    line = _LOG_QUERY.sub(r"\g<method>\g<tail>", line)
    line = _UUID.sub("<id>", line)
    return _HEX_ID.sub("<id>", line)


def _redact_evidence_text(text: str) -> str:
    """Create safe log evidence while leaving the raw generated log untouched."""
    return "".join(_redact_log_line(line) for line in text.splitlines(keepends=True))


def _snapshot_evidence(run_id: str, sources: dict[str, Path]) -> dict[str, Any]:
    """Copy mutable generated evidence into an immutable run directory.

    The canonical report must not point only at ``*-current.json`` files: later
    runs are allowed to replace those paths.  A run-specific copy plus a hash
    makes the evidence referenced by a PASS report reproducible.
    """
    root = validation_state_root(ROOT) / "runs" / run_id
    if root.exists():
        # A resumed run may already have an immutable snapshot written by an
        # earlier attempt before a hard session timeout.  Never mutate that
        # evidence; create a new immutable snapshot for the completed attempt
        # while retaining the same run ID in its manifest.
        suffix = 2
        candidate = root.with_name(f"{run_id}-snapshot-{suffix}")
        while candidate.exists():
            suffix += 1
            candidate = root.with_name(f"{run_id}-snapshot-{suffix}")
        root = candidate
    root.mkdir(parents=True, exist_ok=False)
    files: dict[str, dict[str, Any]] = {}
    for name, source in sources.items():
        entry: dict[str, Any] = {"source": str(source)}
        if not source.is_file():
            entry.update({"status": "MISSING"})
            files[name] = entry
            continue
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        source_bytes = source.read_bytes()
        target_bytes = source_bytes
        redacted = False
        if name.endswith(".log"):
            target_bytes = _redact_evidence_text(
                source_bytes.decode("utf-8", errors="replace")
            ).encode("utf-8")
            redacted = target_bytes != source_bytes
        target.write_bytes(target_bytes)
        entry.update(
            {
                "status": "COPIED",
                "snapshot": str(target),
                "bytes": target.stat().st_size,
                "sha256": _sha256_file(target),
                **(
                    {"sourceSha256": hashlib.sha256(source_bytes).hexdigest()}
                    if redacted
                    else {}
                ),
                **({"redacted": True} if redacted else {}),
            }
        )
        files[name] = entry
    manifest = {
        "schemaVersion": "full-project-evidence-manifest.v1",
        "runId": run_id,
        "createdAt": _now(),
        "files": files,
    }
    manifest_path = root / "evidence-manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return {
        "status": "PASS"
        if all(item["status"] == "COPIED" for item in files.values())
        else "PARTIAL",
        "runId": run_id,
        "directory": str(root),
        "manifest": str(manifest_path),
        "manifestSha256": _sha256_file(manifest_path),
        "files": files,
    }


def _failure_class(output: str, return_code: int) -> str:
    text = output.lower()
    if return_code == 0:
        return "NONE"
    if any(
        marker in text
        for marker in (
            "not_run:",
            "missing prerequisites",
            "docker engine",
            "connection refused",
            "cannot connect to the docker daemon",
            "modulenotfounderror",
            "no module named build",
            "no such file or directory",
            "command not found",
        )
    ):
        return "BLOCKED[environment]"
    return "SOURCE_OR_TEST_FAILURE"


def _pytest_summary(output: str) -> dict[str, int]:
    """Extract aggregate test counts without retaining output or test data.

    Vitest prints both file and test totals (for example ``25`` files and
    ``173`` tests).  Prefer the labelled test total so the canonical report
    cannot accidentally mix file counts with test counts.
    """
    summary: dict[str, int] = {}
    test_total = re.search(r"(?:^|\n)\s*Tests\s+(\d+)\s+passed", output)
    if test_total:
        summary["passed"] = int(test_total.group(1))
    file_total = re.search(r"(?:^|\n)\s*Test Files\s+(\d+)\s+passed", output)
    if file_total:
        summary["testFilesPassed"] = int(file_total.group(1))
    for label in ("passed", "failed", "skipped", "error", "xfailed", "xpassed"):
        if label == "passed" and "passed" in summary:
            continue
        match = re.search(rf"(?<!\d)(\d+)\s+{label}(?!\w)", output)
        if match:
            summary[label] = int(match.group(1))
    return summary


def _pytest_skip_reasons(output: str) -> list[str]:
    """Extract pytest's explicit skip explanations without retaining output."""
    reasons: list[str] = []
    for line in output.splitlines():
        match = re.match(r"\s*SKIPPED\s+\[\d+\]\s+(.+?)\s*$", line)
        if match:
            reasons.append(match.group(1)[:240])
    return reasons[:256]


def _diagnostic_codes(output: str) -> list[str]:
    """Keep stable error identifiers without persisting command output."""
    codes = sorted(set(re.findall(r"\b[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+\b", output)))
    return codes[:32]


@dataclass
class CommandResult:
    command: list[str]
    return_code: int
    duration_seconds: float
    failure_class: str = "NONE"
    test_summary: dict[str, int] = field(default_factory=dict)
    diagnostic_codes: list[str] = field(default_factory=list)
    output_log: str | None = None
    skip_reasons: list[str] = field(default_factory=list)


@dataclass
class StageResult:
    name: str
    status: str = "NOT_RUN"
    commands: list[CommandResult] = field(default_factory=list)
    started_at: str | None = None
    finished_at: str | None = None
    failure: dict[str, Any] | None = None


class Runner:
    def __init__(
        self,
        *,
        product: str,
        run_id: str,
        output: Path,
        force_stages: set[str] | None = None,
    ) -> None:
        self.product = product
        self.run_id = run_id
        self.output = output
        self.stages: list[StageResult] = []
        self.history: list[dict[str, Any]] = []
        self._stage: StageResult | None = None
        self._last_output = ""
        self.force_stages = force_stages or set()

    def load_report(self, report: dict[str, Any]) -> None:
        """Restore completed stages from a prior interrupted run."""
        self.history = list(report.get("history", []))
        restored: list[StageResult] = []
        for raw in report.get("stages", []):
            commands = [
                CommandResult(
                    command=list(item["command"]),
                    return_code=int(item["returnCode"]),
                    duration_seconds=float(item["durationSeconds"]),
                    failure_class=str(item.get("failureClass", "NONE")),
                    test_summary=dict(item.get("testSummary", {})),
                    diagnostic_codes=list(item.get("diagnosticCodes", [])),
                    output_log=item.get("outputLog"),
                    skip_reasons=list(item.get("skipReasons", [])),
                )
                for item in raw.get("commands", [])
            ]
            restored.append(
                StageResult(
                    name=str(raw["name"]),
                    status=str(raw["status"]),
                    commands=commands,
                    started_at=raw.get("startedAt"),
                    finished_at=raw.get("finishedAt"),
                    failure=raw.get("failure"),
                )
            )
        self.stages = restored

    def completed(self, name: str) -> bool:
        return name not in self.force_stages and any(
            stage.name == name and stage.status == "PASS" for stage in self.stages
        )

    def replace_stage(self, name: str) -> None:
        for stage in self.stages:
            if stage.name != name or stage.status not in {
                "FAIL",
                "BLOCKED",
                "INTERRUPTED",
            }:
                continue
            self.history.append(
                {
                    "stage": stage.name,
                    "status": stage.status,
                    "startedAt": stage.started_at,
                    "finishedAt": stage.finished_at,
                    "failure": stage.failure,
                    "commands": [
                        {
                            "command": item.command,
                            "returnCode": item.return_code,
                            "failureClass": item.failure_class,
                            **(
                                {"testSummary": item.test_summary}
                                if item.test_summary
                                else {}
                            ),
                            **(
                                {"diagnosticCodes": item.diagnostic_codes}
                                if item.diagnostic_codes
                                else {}
                            ),
                            **(
                                {"outputLog": item.output_log}
                                if item.output_log
                                else {}
                            ),
                            **(
                                {"skipReasons": item.skip_reasons}
                                if item.skip_reasons
                                else {}
                            ),
                        }
                        for item in stage.commands
                    ],
                }
            )
        self.stages = [stage for stage in self.stages if stage.name != name]

    def command(
        self,
        command: Sequence[str],
        *,
        env: dict[str, str] | None = None,
        cwd: Path = ROOT,
        timeout: int | None = None,
    ) -> subprocess.CompletedProcess[str]:
        print("+", " ".join(command), flush=True)
        started = time.monotonic()
        merged = os.environ.copy()
        if env:
            merged.update(env)
        if merged.get("FORCE_COLOR"):
            merged.pop("NO_COLOR", None)
        try:
            result = subprocess.run(
                list(command),
                cwd=cwd,
                env=merged,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
            output = (result.stdout or "") + (result.stderr or "")
        except subprocess.TimeoutExpired as error:
            output = f"TIMEOUT:{error}"
            result = subprocess.CompletedProcess(list(command), 124, output, output)
        except OSError as error:
            output = f"OSERROR:{error}"
            result = subprocess.CompletedProcess(list(command), 127, "", output)
        self._last_output = output
        output_log: Path | None = None
        if any(Path(item).name == "check_compose_e2e.py" for item in command):
            output_log = (
                validation_cache_root(ROOT)
                / "full-project-e2e"
                / self.run_id
                / "final-compose.log"
            )
            output_log.parent.mkdir(parents=True, exist_ok=True)
            # Keep the evidence bounded while retaining both stdout and stderr.
            output_log.write_text(output[:10_000_000], encoding="utf-8")
        if self._stage is not None:
            test_summary = _pytest_summary(output)
            self._stage.commands.append(
                CommandResult(
                    command=list(command),
                    return_code=result.returncode,
                    duration_seconds=round(time.monotonic() - started, 3),
                    failure_class=_failure_class(output, result.returncode),
                    test_summary=test_summary,
                    diagnostic_codes=(
                        _diagnostic_codes(output) if result.returncode else []
                    ),
                    output_log=str(output_log) if output_log else None,
                    skip_reasons=_pytest_skip_reasons(output),
                )
            )
            if (
                result.returncode == 0
                and self._stage.name in {"python_contracts", "native_operator_suite"}
                and test_summary.get("skipped", 0) > 0
            ):
                raise RuntimeError(
                    "APPLICABLE_TEST_SKIPPED:" + str(test_summary["skipped"])
                )
        if result.returncode:
            diagnostic = output[-4000:].strip()
            if diagnostic:
                print(
                    "! command diagnostic (tail):\n" + diagnostic,
                    file=sys.stderr,
                    flush=True,
                )
            raise subprocess.CalledProcessError(
                result.returncode,
                list(command),
                output=result.stdout,
                stderr=result.stderr,
            )
        return result

    def stage(self, name: str, commands: Sequence[Sequence[str]]) -> None:
        self.replace_stage(name)
        stage = StageResult(name=name, status="RUNNING", started_at=_now())
        self.stages.append(stage)
        self._stage = stage
        try:
            for command in commands:
                self.command(command)
            stage.status = "PASS"
        except subprocess.CalledProcessError as error:
            stage.status = (
                "BLOCKED"
                if stage.commands[-1].failure_class.startswith("BLOCKED[")
                else "FAIL"
            )
            stage.failure = {
                "command": list(error.cmd),
                "returnCode": error.returncode,
                "failureClass": stage.commands[-1].failure_class,
                "minimalReproduction": " ".join(error.cmd),
            }
            raise
        except KeyboardInterrupt as error:
            stage.status = "INTERRUPTED"
            stage.failure = {
                "failureClass": "INTERRUPTED[operator]",
                "error": str(error) or "KeyboardInterrupt",
                "minimalReproduction": "resume with the same --output and --resume",
            }
            raise
        finally:
            stage.finished_at = _now()
            self._stage = None

    def record_not_run(self, name: str, reason: str) -> None:
        self.replace_stage(name)
        self.stages.append(
            StageResult(
                name=name,
                status="NOT_RUN",
                started_at=_now(),
                finished_at=_now(),
                failure={"failureClass": reason},
            )
        )

    def write_report(
        self, *, status: str, baseline: dict[str, Any], extra: dict[str, Any]
    ) -> None:
        final_status = _git_status()
        report = {
            "schemaVersion": "full-project-test-report.v1",
            "status": status,
            "startedAt": baseline["startedAt"],
            "finishedAt": _now(),
            "runId": self.run_id,
            "product": self.product,
            "baseline": baseline,
            "finalWorktree": {
                "capturedAt": _now(),
                "status": final_status,
                "statusSha256": _status_sha256(final_status),
            },
            "stages": [
                {
                    "name": stage.name,
                    "status": stage.status,
                    "startedAt": stage.started_at,
                    "finishedAt": stage.finished_at,
                    "commands": [
                        {
                            "command": item.command,
                            "returnCode": item.return_code,
                            "durationSeconds": item.duration_seconds,
                            "failureClass": item.failure_class,
                            **(
                                {"testSummary": item.test_summary}
                                if item.test_summary
                                else {}
                            ),
                            **(
                                {"diagnosticCodes": item.diagnostic_codes}
                                if item.diagnostic_codes
                                else {}
                            ),
                            **(
                                {"outputLog": item.output_log}
                                if item.output_log
                                else {}
                            ),
                            **(
                                {"skipReasons": item.skip_reasons}
                                if item.skip_reasons
                                else {}
                            ),
                        }
                        for item in stage.commands
                    ],
                    **({"failure": stage.failure} if stage.failure else {}),
                }
                for stage in self.stages
            ],
            "defectPolicy": {
                "applicableSkip": "FAIL",
                "fullSuiteExternalSkip": "RECORDED_NOT_ASSESSED",
                "writeRetry": "DISALLOWED",
                "idempotentRetry": "BOUNDED_RETRY_AFTER_ONLY",
                "existingDeploymentMutation": "DISALLOWED",
            },
            "scientificStatus": "NOT_ASSESSED",
            "engineeringStatus": (
                "PASS" if status == "PASS" else "FAIL" if status == "FAIL" else status
            ),
            "agentExperienceStatus": "NOT_ASSESSED",
            "history": self.history,
            **extra,
        }
        self.output.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.output.with_suffix(self.output.suffix + ".tmp")
        temporary.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        temporary.replace(self.output)

    def checkpoint(self, *, baseline: dict[str, Any], extra: dict[str, Any]) -> None:
        self.write_report(status="IN_PROGRESS", baseline=baseline, extra=extra)


def _wheel_projects() -> dict[str, Path]:
    return {
        "workflow-compiler": ROOT / "packages/workflow-compiler",
        "assembler": ROOT / "platform/assembler",
        "executor-service": ROOT / "services/executor",
        "kernel-services": ROOT / "kernel",
        "embedding-client": ROOT / "packages/embedding-client",
        "operator-pack": ROOT / "packages/operator-pack",
        "data-service": ROOT / "services/data",
        "data-client": ROOT / "services/data/client",
        "knowledge-next": ROOT / "services/knowledge-next",
        "knowledge-client": ROOT / "services/knowledge-next/client",
        "protocol-contracts": ROOT / "packages/protocol-contracts",
        "executor-client": ROOT / "services/executor/client",
        "groundwater-api-client": ROOT / "packages/groundwater-api-client",
    }


def _prepare_wheel_build_input(project: Path, destination: Path) -> Path:
    """Copy one project without generated state before invoking its backend.

    Setuptools can reuse an existing ``build/lib`` tree and silently place
    deleted modules or package data in a new wheel. The full-project release
    gate must assess current source without deleting a developer's local build
    artifacts, so every wheel is built from an isolated clean snapshot.
    """

    ignored = shutil.ignore_patterns(
        ".git",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".tox",
        ".venv",
        "*.egg-info",
        "*.pyc",
        "*.pyo",
        "__pycache__",
        "build",
        "dist",
        "node_modules",
    )
    shutil.copytree(project, destination, symlinks=True, ignore=ignored)
    return destination


def _build_and_check_wheels(runner: Runner) -> None:
    with tempfile.TemporaryDirectory(prefix="platform-full-wheels-") as temporary:
        wheel_dir = Path(temporary)
        source_dir = wheel_dir / "source"
        source_dir.mkdir()
        wheel_paths: dict[str, Path] = {}
        for option, project in _wheel_projects().items():
            build_input = _prepare_wheel_build_input(project, source_dir / option)
            before = set(wheel_dir.glob("*.whl"))
            runner.command(_offline_wheel_build_command(build_input, wheel_dir))
            candidates = sorted(set(wheel_dir.glob("*.whl")) - before)
            if not candidates:
                raise RuntimeError(f"WHEEL_NOT_BUILT:{option}")
            if len(candidates) != 1:
                raise RuntimeError(f"WHEEL_BUILD_AMBIGUOUS:{option}")
            wheel_paths[option] = candidates[0]
        command = [PYTHON, "-m", "scripts.release.check_release_wheels"]
        for option, path in wheel_paths.items():
            command.extend([f"--{option}", str(path)])
        runner.command(command)


def _compile_digests(runner: Runner) -> tuple[list[str], dict[str, str]]:
    digests: list[str] = []
    output_paths: list[Path] = []
    for _ in range(2):
        result = runner.command(
            [
                PYTHON,
                "scripts/development/dev.py",
                "compile",
                "--product",
                runner.product,
            ]
        )
        output_path = Path(result.stdout.strip().splitlines()[-1])
        output_paths.append(output_path)
        digests.append(_sha256_tree(output_path))
    if digests[0] != digests[1]:
        raise RuntimeError("ASSEMBLY_DIGEST_MISMATCH")
    artifacts = {
        child.name: hashlib.sha256(child.read_bytes()).hexdigest()
        for child in sorted(output_paths[-1].iterdir())
        if child.is_file()
    }
    return digests, artifacts


def _run_stage(
    runner: Runner,
    name: str,
    commands: Sequence[Sequence[str]],
    *,
    resume: bool,
    baseline: dict[str, Any],
    extra: dict[str, Any],
) -> None:
    if resume and runner.completed(name):
        print(f"RESUME {name}: already PASS", flush=True)
        return
    runner.stage(name, commands)
    runner.checkpoint(baseline=baseline, extra=extra)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--product", default="groundwater")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--resume",
        action="store_true",
        help="resume completed stages from an IN_PROGRESS report at --output",
    )
    parser.add_argument(
        "--rerun-stage",
        action="append",
        default=[],
        help="force a named checkpoint stage to run again; may be repeated",
    )
    parser.add_argument(
        "--network-subnet",
        help="confirmed-free private subnet forwarded to isolated Compose drivers",
    )
    args = parser.parse_args()
    output = args.output if args.output.is_absolute() else ROOT / args.output
    existing: dict[str, Any] | None = None
    if args.resume:
        if not output.is_file():
            parser.error(f"--resume requires an existing report: {output}")
        existing = json.loads(output.read_text(encoding="utf-8"))
        if existing.get("schemaVersion") != "full-project-test-report.v1":
            parser.error("--resume requires full-project-test-report.v1")
        if existing.get("product") != args.product:
            parser.error("--resume product does not match the checkpoint")
        run_id = str(existing["runId"])
        baseline = dict(existing["baseline"])
    else:
        run_id = uuid.uuid4().hex[:12]
        baseline = {
            "startedAt": _now(),
            "head": _version(["git", "rev-parse", "HEAD"]),
            "worktree": _git_status(),
            "versions": {
                "python": _version([PYTHON, "--version"]),
                "node": _version(["node", "--version"]),
                "docker": _version(
                    [docker_executable(), "info", "--format", "{{.ServerVersion}}"]
                ),
                "compose": _version([docker_executable(), "compose", "version"]),
                "ruff": _version(["ruff", "--version"]),
                "mypy": _version([PYTHON, "-m", "mypy", "--version"]),
            },
        }
    forced_stages = set(args.rerun_stage)
    runner = Runner(
        product=args.product,
        run_id=run_id,
        output=output,
        force_stages=forced_stages,
    )
    if existing is not None:
        runner.load_report(existing)
    stage_status = "PASS"
    extra: dict[str, Any] = {
        "isolation": {
            "policy": "RANDOM_PROJECT_DYNAMIC_PORTS_PRIVATE_NETWORK_FRESH_VOLUMES",
            "existingDeploymentMutation": False,
            "runId": run_id,
            "networkSubnet": args.network_subnet or "driver-default",
        },
        "assembly": {},
        "simulation": {
            "status": "PASS",
            "agentProvider": "tests/e2e/compose-stub",
            "agentModel": "test-model",
            "intentRouterProvider": "tests/e2e/compose-stub",
            "intentRouterModel": "test-router-model",
            "embedding": "deterministic-compose-stub",
        },
    }
    validation_root = validation_cache_root(ROOT)
    evidence_sources: dict[str, Path] = {
        "data-knowledge-e2e-current.json": validation_root
        / "data-knowledge-e2e-current.json",
        "v12-six-model-lifecycle.json": validation_root
        / "transfer-evidence/v12-six-model-lifecycle.json",
        "webui-results.json": validation_root / "webui-evidence/results.json",
    }
    if existing is not None:
        for key in (
            "isolation",
            "assembly",
            "simulation",
            "evidence",
            "reportPolicy",
            "failureHandling",
        ):
            if key in existing:
                extra[key] = existing[key]
        if args.network_subnet:
            extra.setdefault("isolation", {})["networkSubnet"] = args.network_subnet
    stages: list[tuple[str, Sequence[Sequence[str]]]] = [
        (
            "preflight_static",
            [
                [PYTHON, "--version"],
                ["node", "--version"],
                ["npm", "--version"],
                [docker_executable(), "info", "--format", "{{.ServerVersion}}"],
                [docker_executable(), "compose", "version"],
                _build_check_command(),
                [PYTHON, "-m", "scripts.checks.check_core_imports"],
                ["bash", "scripts/checks/check-boundaries.sh"],
                [PYTHON, "-m", "scripts.checks.check_documentation"],
                ["git", "diff", "--check"],
                ["ruff", "check", "."],
                [
                    PYTHON,
                    "-m",
                    "mypy",
                    "--follow-imports=silent",
                    "kernel/src/platform_kernel/application/ports.py",
                    "kernel/src/platform_kernel/application/agent_projection.py",
                    "kernel/src/platform_kernel/interaction/owner.py",
                    "kernel/src/platform_kernel/agent_runtime/run_dependencies.py",
                    "kernel/src/platform_kernel/agent_runtime/run_start.py",
                    "kernel/src/platform_kernel/agent_runtime/run_resume.py",
                    "kernel/src/platform_kernel/agent_runtime/run_finalization.py",
                    "kernel/src/platform_kernel/agent_runtime/owner.py",
                    "tests/architecture/kernel_port_types.py",
                ],
                [PYTHON, "-m", "scripts.development.check_runtime_openapi"],
                ["npm", "--prefix", "web", "run", "contracts:check"],
                [
                    PYTHON,
                    "-m",
                    "scripts.development.generate_python_sdk_contracts",
                    "--check",
                ],
            ],
        ),
        *(
            (
                f"python_{test_slice.key}",
                [[PYTHON, *pytest_command(test_slice, report_skips=True)]],
            )
            for test_slice in V6_PYTEST_SLICES
        ),
        (
            "native_operator_suite",
            [[PYTHON, "-m", "scripts.checks.check_native_tests"]],
        ),
        (
            "web_suite",
            [
                *(
                    ["npm", "--prefix", name, "run", action]
                    for name in V6_UI_WORKSPACES
                    for action in ("test", "typecheck", "build")
                ),
                ["npm", "--prefix", "web", "run", "test:e2e:build"],
                ["npm", "--prefix", "services/data/web", "run", "test:ui"],
                ["npm", "--prefix", "services/knowledge-next/ui", "run", "test:ui"],
            ],
        ),
    ]
    try:
        if args.resume and runner.completed("assembly"):
            print("RESUME assembly: already PASS", flush=True)
        else:
            runner.replace_stage("assembly")
            runner._stage = StageResult(
                name="assembly", status="RUNNING", started_at=_now()
            )
            runner.stages.append(runner._stage)
            try:
                digests, artifacts = _compile_digests(runner)
                runner._stage.status = "PASS"
                extra["assembly"] = {
                    "status": "PASS",
                    "digest": digests[0],
                    "artifactChecksums": artifacts,
                }
            finally:
                runner._stage.finished_at = _now()
                runner._stage = None
            runner.checkpoint(baseline=baseline, extra=extra)

        for name, commands in stages:
            _run_stage(
                runner,
                name,
                commands,
                resume=args.resume,
                baseline=baseline,
                extra=extra,
            )

        joint_command = [
            PYTHON,
            "scripts/checks/check_data_knowledge_e2e.py",
            "--output",
            str(validation_root / "data-knowledge-e2e-current.json"),
        ]
        compose_command = [
            PYTHON,
            "scripts/checks/check_compose_e2e.py",
            "--product",
            args.product,
        ]
        if args.network_subnet:
            joint_command.extend(["--network-subnet", args.network_subnet])
            compose_command.extend(["--network-subnet", args.network_subnet])
        _run_stage(
            runner,
            "data_knowledge_joint",
            [joint_command],
            resume=args.resume,
            baseline=baseline,
            extra=extra,
        )

        if args.resume and runner.completed("wheels"):
            print("RESUME wheels: already PASS", flush=True)
        else:
            runner.replace_stage("wheels")
            runner._stage = StageResult(
                name="wheels", status="RUNNING", started_at=_now()
            )
            runner.stages.append(runner._stage)
            try:
                _build_and_check_wheels(runner)
                runner._stage.status = "PASS"
            finally:
                runner._stage.finished_at = _now()
                runner._stage = None
            runner.checkpoint(baseline=baseline, extra=extra)

        _run_stage(
            runner,
            "final_compose",
            [compose_command],
            resume=args.resume,
            baseline=baseline,
            extra=extra,
        )
        final_compose_log = (
            validation_root / "full-project-e2e" / run_id / "final-compose.log"
        )
        if final_compose_log.is_file():
            evidence_sources["final-compose.log"] = final_compose_log
        joint_report = validation_root / "data-knowledge-e2e-current.json"
        extra["evidence"] = {
            "dataKnowledge": str(joint_report),
            "sixModelLifecycle": str(
                validation_root / "transfer-evidence/v12-six-model-lifecycle.json"
            ),
            "webUi": str(validation_root / "webui-evidence/results.json"),
            "dataKnowledgeStatus": (
                json.loads(joint_report.read_text(encoding="utf-8")).get("status")
                if joint_report.is_file()
                else "MISSING"
            ),
        }
        # The joint runner persists its service logs under the run-specific
        # directory. Include them in the immutable snapshot when available.
        joint_run_id = (
            json.loads(joint_report.read_text(encoding="utf-8"))
            .get("isolation", {})
            .get("runId")
            if joint_report.is_file()
            else None
        )
        if isinstance(joint_run_id, str):
            joint_log_dir = validation_root / "data-knowledge-e2e" / joint_run_id
            for name in (
                "data.log",
                "knowledge.log",
                "knowledge-restored.log",
                "data-ui-e2e.log",
                "knowledge-ui-e2e.log",
            ):
                evidence_sources[f"data-knowledge-{name}"] = joint_log_dir / name
            joint_details = json.loads(joint_report.read_text(encoding="utf-8"))
            for item in joint_details.get("commandLogs", []):
                command_log = Path(item)
                if command_log.is_file():
                    evidence_sources[f"data-knowledge-{command_log.name}"] = command_log
        joint_isolation = (
            json.loads(joint_report.read_text(encoding="utf-8")).get("isolation", {})
            if joint_report.is_file()
            else {}
        )
        extra["runtimeIdentities"] = {
            "finalComposeProject": None,
            "finalComposeNetworkSubnet": args.network_subnet or "driver-default",
            "jointRunId": joint_isolation.get("runId"),
            "jointProject": joint_isolation.get("projectId"),
            "dataComposeProject": joint_isolation.get("dataComposeProject"),
            "knowledgeComposeProject": joint_isolation.get("knowledgeComposeProject"),
            "restoredKnowledgeComposeProject": joint_isolation.get(
                "restoredKnowledgeComposeProject"
            ),
            "jointNetwork": joint_isolation.get("network"),
        }
    except KeyboardInterrupt as error:
        current = runner.stages[-1] if runner.stages else None
        if current is not None and current.status == "RUNNING":
            current.status = "INTERRUPTED"
            current.failure = {
                "failureClass": "INTERRUPTED[operator]",
                "error": str(error) or "KeyboardInterrupt",
                "minimalReproduction": "resume with the same --output and --resume",
            }
        stage_status = "INTERRUPTED"
        for name in (
            "data_knowledge_joint",
            "wheels",
            "final_compose",
        ):
            if not any(stage.name == name for stage in runner.stages):
                runner.record_not_run(name, "UPSTREAM_STAGE_INTERRUPTED")
    except (subprocess.CalledProcessError, RuntimeError) as error:
        current = runner.stages[-1] if runner.stages else None
        if current is not None and current.status == "RUNNING":
            current.status = "FAIL"
            current.failure = {
                "failureClass": "SOURCE_OR_TEST_FAILURE",
                "error": str(error),
                "minimalReproduction": "see preceding command in this stage",
            }
        stage_status = (
            "BLOCKED"
            if isinstance(error, subprocess.CalledProcessError)
            and runner.stages[-1].status == "BLOCKED"
            else "FAIL"
        )
        for name in (
            "data_knowledge_joint",
            "wheels",
            "final_compose",
        ):
            if not any(stage.name == name for stage in runner.stages):
                runner.record_not_run(name, "UPSTREAM_STAGE_FAILED")
    finally:
        try:
            # The joint gate can fail before the success-path evidence wiring
            # runs. Discover its run-scoped service/UI logs here as well so a
            # failed fresh run remains diagnosable and immutable.
            joint_report = validation_root / "data-knowledge-e2e-current.json"
            if joint_report.is_file():
                joint_details = json.loads(joint_report.read_text(encoding="utf-8"))
                joint_run_id = joint_details.get("isolation", {}).get("runId")
                if isinstance(joint_run_id, str):
                    joint_log_dir = (
                        validation_root / "data-knowledge-e2e" / joint_run_id
                    )
                    for name in (
                        "data.log",
                        "knowledge.log",
                        "knowledge-restored.log",
                        "data-ui-e2e.log",
                        "knowledge-ui-e2e.log",
                    ):
                        evidence_sources[f"data-knowledge-{name}"] = (
                            joint_log_dir / name
                        )
                    for item in joint_details.get("commandLogs", []):
                        command_log = Path(item)
                        if command_log.is_file():
                            evidence_sources[f"data-knowledge-{command_log.name}"] = (
                                command_log
                            )
            final_compose_log = (
                validation_root / "full-project-e2e" / run_id / "final-compose.log"
            )
            if final_compose_log.is_file():
                evidence_sources["final-compose.log"] = final_compose_log
            snapshot = _snapshot_evidence(run_id, evidence_sources)
            extra["evidenceSnapshot"] = snapshot
            if "evidence" in extra:
                evidence = extra["evidence"]
                if isinstance(evidence, dict):
                    snapshot_paths = {
                        name: item.get("snapshot", item.get("source"))
                        for name, item in snapshot["files"].items()
                    }
                    evidence["dataKnowledge"] = snapshot_paths.get(
                        "data-knowledge-e2e-current.json", evidence.get("dataKnowledge")
                    )
                    evidence["sixModelLifecycle"] = snapshot_paths.get(
                        "v12-six-model-lifecycle.json",
                        evidence.get("sixModelLifecycle"),
                    )
                    evidence["webUi"] = snapshot_paths.get(
                        "webui-results.json", evidence.get("webUi")
                    )
                    evidence["snapshot"] = snapshot_paths
        except Exception as error:
            # Evidence copy failure must make the run non-passing. The test
            # stages may have passed, but the requested audit artifact is not
            # complete without an immutable snapshot.
            stage_status = "FAIL"
            extra["evidenceSnapshot"] = {
                "status": "FAIL",
                "error": f"{type(error).__name__}: {error}",
            }
        runner.write_report(
            status=stage_status,
            baseline=baseline,
            extra={
                **extra,
                "reportPolicy": "single_canonical_report",
                "failureHandling": "stable_error_request_identity_log_phase_minimal_reproduction",
            },
        )
        try:
            markdown_output = write_markdown_report(output)
        except (OSError, ValueError) as error:
            stage_status = "FAIL"
            extra["markdownReport"] = {
                "status": "FAIL",
                "error": f"{type(error).__name__}:{error}",
            }
            runner.write_report(
                status=stage_status,
                baseline=baseline,
                extra={
                    **extra,
                    "reportPolicy": "single_canonical_report",
                    "failureHandling": "stable_error_request_identity_log_phase_minimal_reproduction",
                },
            )
        else:
            print(f"Full project Markdown report: {markdown_output}", flush=True)
    print(f"Full project report: {output}", flush=True)
    return 0 if stage_status == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
