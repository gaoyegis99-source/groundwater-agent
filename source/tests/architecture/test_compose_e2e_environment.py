from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from scripts.checks.compose_e2e import (
    assertions,
    environment,
    live_reply_quality,
    scenarios,
)
from scripts.release import create_deployment_secrets
from scripts.lib.e2e_http import TOKEN_ENVIRONMENT, public_api_headers


def test_e2e_http_adds_ephemeral_token_without_overriding_headers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(TOKEN_ENVIRONMENT, "gwr_fixture")

    assert public_api_headers({"X-Project-ID": "project"}) == {
        "X-Project-ID": "project",
        "Authorization": "Bearer gwr_fixture",
    }
    assert public_api_headers({"Authorization": "Bearer explicit"}) == {
        "Authorization": "Bearer explicit"
    }


def test_compose_bootstraps_short_lived_local_admin_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def execute(command, service, source, **_kwargs):
        captured.update(command=command, service=service, source=source)
        return "gwr_isolated_token\n"

    monkeypatch.setattr(environment, "_exec_python", execute)

    token = environment._bootstrap_e2e_api_token(
        ["docker", "compose"], "groundwater-demo"
    )

    assert token == "gwr_isolated_token"
    assert captured["service"] == "kernel-service"
    source = str(captured["source"])
    assert "bootstrap_admin" in source
    assert "groundwater-demo" in source
    assert "expires_days=1" in source


def test_lineage_audit_passes_api_token_via_stdin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, object]] = []

    def execute(_command, service, source, **kwargs):
        calls.append(
            {
                "service": service,
                "source": source,
                "input_text": kwargs.get("input_text"),
            }
        )
        if "SELECT COUNT(*) FROM workflow_runs" in source:
            return "1\n"
        if "SELECT * FROM executor_jobs" in source:
            return json.dumps(
                {
                    "jobs": 1,
                    "worker_execution": "native",
                    "job_request": "{}",
                    "job_id": "job-1",
                    "request_id": "run-1",
                    "node_count": 1,
                    "attempt_count": 1,
                }
            )
        if "authorization = sys.stdin.read().strip()" in source:
            return json.dumps(
                [{"version_id": "result-version", "inputs": ["input-version"]}]
            )
        raise AssertionError(source)

    monkeypatch.setenv(TOKEN_ENVIRONMENT, "gwr_lineage_fixture")
    monkeypatch.setattr(assertions, "_exec_python", execute)

    result = assertions._audit_case(
        ["docker", "compose"],
        "groundwater-demo",
        "session-1",
        {
            "run_id": "run-1",
            "outputs": [{"version_id": "result-version"}],
        },
        {"version_id": "input-version"},
        "selection",
    )

    lineage_call = next(
        call
        for call in calls
        if "authorization = sys.stdin.read().strip()" in str(call["source"])
    )
    assert lineage_call["service"] == "kernel-service"
    assert lineage_call["input_text"] == "Bearer gwr_lineage_fixture"
    assert "gwr_lineage_fixture" not in str(lineage_call["source"])
    assert "http://gateway:8080" in str(lineage_call["source"])
    assert "/api/v12/data/versions/" in str(lineage_call["source"])
    assert "http://data-service:8000" not in str(lineage_call["source"])
    assert result["lineage"] == [
        {"version_id": "result-version", "inputs": ["input-version"]}
    ]


def test_executor_restart_acceptance_compares_the_persisted_output_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = {
        "status": "SUCCEEDED",
        "outputs": [
            {"name": "summary", "versionId": "version-1"},
            {"name": "details", "versionId": "version-2"},
        ],
    }
    responses = iter(
        (
            "job-1\nlease-1\n",
            json.dumps(snapshot),
            json.dumps(
                {
                    "job": snapshot,
                    "stale": {"status": 409, "code": "LEASE_STALE"},
                }
            ),
        )
    )
    restart_commands: list[list[str]] = []
    monkeypatch.setattr(
        assertions, "_exec_python", lambda *_args, **_kwargs: next(responses)
    )
    monkeypatch.setattr(
        assertions, "_run", lambda command, **_kwargs: restart_commands.append(command)
    )

    assertions._verify_executor_restart(["docker", "compose"], "job-1")

    assert restart_commands == [
        [
            "docker",
            "compose",
            "up",
            "-d",
            "--no-deps",
            "--no-build",
            "--force-recreate",
            "-V",
            "--wait",
            "--wait-timeout",
            "120",
            "executor-control",
        ]
    ]


def test_executor_restart_acceptance_rejects_changed_outputs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    before = {
        "status": "SUCCEEDED",
        "outputs": [{"name": "summary", "versionId": "version-1"}],
    }
    after = {
        "job": {
            "status": "SUCCEEDED",
            "outputs": [{"name": "summary", "versionId": "changed"}],
        },
        "stale": {"status": 409, "code": "LEASE_STALE"},
    }
    responses = iter(("job-1\nlease-1\n", json.dumps(before), json.dumps(after)))
    monkeypatch.setattr(
        assertions, "_exec_python", lambda *_args, **_kwargs: next(responses)
    )
    monkeypatch.setattr(assertions, "_run", lambda *_args, **_kwargs: None)

    with pytest.raises(RuntimeError, match="EXECUTOR_RESTART_CHECK_FAILED"):
        assertions._verify_executor_restart(["docker", "compose"], "job-1")


def test_knowledge_data_probes_use_current_data_v3_wire_identity() -> None:
    root = Path(__file__).resolve().parents[2]
    scripts = (
        "check_data_integration.py",
        "check_retry_recovery.py",
    )

    for name in scripts:
        source = (root / "services/knowledge-next/scripts" / name).read_text(
            encoding="utf-8"
        )
        assert 'dataset["datasetId"]' in source
        assert 'version["versionId"]' in source
        assert 'dataset["id"]' not in source
        assert 'version["id"]' not in source


def test_compose_e2e_does_not_force_a_dedicated_builder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = {}

    def run(command, **kwargs):
        captured.update(kwargs)
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(environment.subprocess, "run", run)
    monkeypatch.setenv("COMPOSE_BAKE", "false")
    monkeypatch.setenv("BUILDX_BUILDER", "default")

    environment._run(["docker", "compose", "build"])

    assert captured["env"]["COMPOSE_BAKE"] == "false"
    assert captured["env"]["BUILDX_BUILDER"] == "default"


def test_deployment_secret_set_matches_compose_contract(tmp_path: Path) -> None:
    agent_key = tmp_path / "agent-key"
    router_key = tmp_path / "router-key"
    agent_key.write_text("agent-fixture-key\n")
    router_key.write_text("router-fixture-key\n")
    destination = tmp_path / "secrets"

    create_deployment_secrets.create(destination, agent_key, router_key)

    expected = {
        "agent-api-key",
        "intent-router-api-key",
        "data-db-password",
        "knowledge-db-password",
    }
    assert {path.name for path in destination.iterdir()} == expected
    assert destination.stat().st_mode & 0o777 == 0o700
    assert (destination / "agent-api-key").read_bytes() == b"agent-fixture-key"
    assert (destination / "intent-router-api-key").read_bytes() == b"router-fixture-key"
    database_passwords = [
        (destination / name).read_bytes()
        for name in ("data-db-password", "knowledge-db-password")
    ]
    assert len(set(database_passwords)) == 2
    assert all(len(value) >= 48 and b"\n" not in value for value in database_passwords)
    assert all(path.stat().st_mode & 0o777 == 0o444 for path in destination.iterdir())


def test_compose_secret_set_accepts_ephemeral_live_provider_key(
    tmp_path: Path,
) -> None:
    environment._compose_secrets(
        tmp_path,
        {"services": {}},
        provider_key="live-provider-fixture-key",
    )

    assert (tmp_path / "secrets/agent-api-key").read_bytes() == (
        b"live-provider-fixture-key"
    )
    assert (tmp_path / "secrets/intent-router-api-key").read_bytes() == (
        b"live-provider-fixture-key"
    )


def test_compose_secret_set_supplies_inert_remote_worker_bindings(
    tmp_path: Path,
) -> None:
    compose = {
        "services": {
            "executor-remote_http-worker": {
                "secrets": [
                    "groundwater-sustainability-api-key",
                    "groundwater-sustainability-ca-bundle",
                ],
            },
        },
    }

    environment_file = environment._compose_secrets(tmp_path, compose)

    values = dict(
        line.split("=", 1)
        for line in environment_file.read_text(encoding="utf-8").splitlines()
    )
    assert values["GROUNDWATER_SUSTAINABILITY_BASE_URL"] == (
        "https://groundwater-sustainability.invalid"
    )
    assert values["GROUNDWATER_SUSTAINABILITY_ALLOWED_HOST"] == (
        "groundwater-sustainability.invalid"
    )
    for name in compose["services"]["executor-remote_http-worker"]["secrets"]:
        path = tmp_path / "secrets" / name
        assert path.is_file() and path.stat().st_mode & 0o777 == 0o444


def test_live_reply_quality_configures_agent_and_router_without_key_leak(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LIVE_PROVIDER_TEST_KEY", "provider-secret-fixture")
    config = live_reply_quality.LiveReplyQualityConfig(
        provider="deepseek",
        protocol="responses",
        base_url="https://provider.invalid",
        model="deepseek-test",
        key_env="LIVE_PROVIDER_TEST_KEY",
        report=tmp_path / "report.json",
    )
    kernel = {
        "environment": {},
        "depends_on": {"test-upstream": {"condition": "service_healthy"}},
    }

    live_reply_quality.configure_kernel(kernel, config)

    assert config.provider_key() == "provider-secret-fixture"
    assert kernel["environment"]["PLATFORM_AGENT_PROVIDER"] == "deepseek"
    assert kernel["environment"]["PLATFORM_INTENT_ROUTER_PROVIDER"] == "deepseek"
    assert "test-upstream" not in kernel["depends_on"]
    assert "provider-secret-fixture" not in json.dumps(kernel)


def test_live_reply_quality_uses_gateway_for_knowledge_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict = {}
    config = live_reply_quality.LiveReplyQualityConfig(
        provider="deepseek",
        protocol="responses",
        base_url="https://provider.invalid",
        model="deepseek-test",
        key_env="LIVE_PROVIDER_TEST_KEY",
        report=tmp_path / "report.json",
    )
    config.report.write_text('{"status":"STALE"}')

    def run(command, **kwargs):
        captured.update(
            command=command,
            kwargs=kwargs,
            report_exists=config.report.exists(),
        )
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(
        live_reply_quality,
        "_run",
        run,
    )

    live_reply_quality.run_reply_quality(
        base_url="http://127.0.0.1:32101",
        kernel_config=tmp_path / "kernel-config.json",
        config=config,
    )

    command = captured["command"]
    assert command[command.index("--base-url") + 1] == (
        command[command.index("--knowledge-url") + 1]
    )
    assert captured["kwargs"]["check"] is False
    assert captured["kwargs"]["capture_output"] is True
    assert captured["report_exists"] is False


def test_reply_quality_entrypoint_imports_from_outside_repository(
    tmp_path: Path,
) -> None:
    root = Path(__file__).resolve().parents[2]
    completed = subprocess.run(
        [
            sys.executable,
            str(root / "scripts/evaluation/evaluate_reply_quality.py"),
            "--help",
        ],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert "structured-reply quality gate" in completed.stdout


def test_reply_quality_authenticates_both_gateway_clients() -> None:
    root = Path(__file__).resolve().parents[2]
    source = (
        root / "scripts/evaluation/evaluate_reply_quality.py"
    ).read_text(encoding="utf-8")

    assert source.count("headers=public_api_headers()") == 2


def test_compose_transfer_fixture_is_scoped_to_temporary_run() -> None:
    source = Path(scenarios.__file__).read_text(encoding="utf-8")

    assert 'prepare_fixture(Path(directory) / "transfer-case")' in source
    assert 'prepare_fixture(generated.parent / "transfer-case")' not in source


def test_compose_source_copies_the_image_build_inputs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    for name in ("kernel", "services", "packages", "products", "domains", "web"):
        (repository / name).mkdir()
    for runtime in ("build", "native", "qgis"):
        lock_directory = repository / "environments" / runtime
        lock_directory.mkdir(parents=True)
        (lock_directory / "requirements.lock").write_text(runtime)
    for filename in (
        "package.json",
        "package-lock.json",
        ".npmrc",
        ".dockerignore",
        "system-deps.lock.json",
    ):
        (repository / filename).write_text(filename)
    monkeypatch.setattr(scenarios, "ROOT", repository)

    copied = scenarios._copy_compose_source(tmp_path / "evaluation")

    for runtime in ("build", "native", "qgis"):
        assert (
            copied / "environments" / runtime / "requirements.lock"
        ).read_text() == runtime
    for filename in (
        "package.json",
        "package-lock.json",
        ".npmrc",
        ".dockerignore",
        "system-deps.lock.json",
    ):
        assert (copied / filename).read_text() == filename


def test_compose_e2e_owns_instance_mounts_and_dependency_binding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        environment,
        "create_deployment_secrets",
        lambda directory, *_args: directory.mkdir(),
    )
    dependency_root = tmp_path / "dependencies"
    monkeypatch.setattr(
        environment,
        "project_paths",
        lambda: {"PLATFORM_DEPENDENCY_ROOT": str(dependency_root)},
    )
    compose = {
        "services": {
            "data": {
                "volumes": [
                    {
                        "source": "${PLATFORM_INSTANCE_DATA_ROOT:?required}/data/database",
                        "target": "/data",
                    }
                ]
            },
            "knowledge": {
                "volumes": [
                    {
                        "source": "${PLATFORM_INSTANCE_DATA_ROOT:?required}/knowledge/database",
                        "target": "/var/lib/postgresql/data",
                    }
                ]
            },
            "api": {
                "volumes": [
                    "${PLATFORM_INSTANCE_DATA_ROOT:?required}/api:/data"
                ]
            },
        }
    }
    path = environment._compose_secrets(tmp_path, compose)
    assert path.read_text(encoding="utf-8").splitlines() == [
        f"PLATFORM_SECRETS_DIR={tmp_path / 'secrets'}",
        f"PLATFORM_INSTANCE_DATA_ROOT={tmp_path / 'instance-data'}",
        f"PLATFORM_DEPENDENCY_ROOT={dependency_root}",
    ]
    assert (tmp_path / "instance-data/data/database").is_dir()
    assert (tmp_path / "instance-data/knowledge/database").is_dir()
    assert (tmp_path / "instance-data/api").is_dir()
    assert all(
        path.stat().st_mode & 0o777 == 0o777
        for path in (
            tmp_path / "instance-data/data/database",
            tmp_path / "instance-data/knowledge/database",
            tmp_path / "instance-data/api",
        )
    )


def test_compose_e2e_rejects_instance_mount_traversal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        environment,
        "create_deployment_secrets",
        lambda directory, *_args: directory.mkdir(),
    )
    compose = {
        "services": {
            "unsafe": {
                "volumes": [
                    {
                        "source": "${PLATFORM_INSTANCE_DATA_ROOT:?required}/../outside",
                        "target": "/data",
                    }
                ]
            }
        }
    }
    with pytest.raises(ValueError, match="COMPOSE_INSTANCE_VOLUME_INVALID"):
        environment._compose_secrets(tmp_path, compose)
