from __future__ import annotations

import json
from pathlib import Path
import socket
from types import SimpleNamespace

import httpx
import pytest

from scripts.checks.data_knowledge_e2e.context import Recorder
from scripts.checks.data_knowledge_e2e.diagnostics import _scan_log
from scripts.checks.data_knowledge_e2e.environment import (
    _dependency_build_arguments,
    _frozen_image_build_command,
    _free_ports,
    _prepare_instance_data,
    _wait_http_ready,
    _write_overrides,
)
from scripts.checks.data_knowledge_e2e.identity import (
    _bootstrap_identity_token,
    _check_gateway_pages,
)
from scripts.checks.data_knowledge_e2e.report import (
    _playwright_passed,
    _pytest_junit_summary,
    _pytest_report,
)
from scripts.checks.data_knowledge_e2e.scenarios import _pytest_command
from base_images import locked_base_image_contexts


def test_dependency_build_arguments_use_frozen_named_contexts(
    tmp_path: Path,
) -> None:
    arguments = _dependency_build_arguments(tmp_path)
    contexts = arguments[1::2]

    assert arguments[::2] == ["--build-context"] * 8
    assert contexts[0] == f"platform-deps={tmp_path}"
    root = Path(__file__).resolve().parents[2]
    assert contexts[1:] == list(locked_base_image_contexts(root))


def test_frozen_image_build_uses_digest_contexts_and_loads_result(
    tmp_path: Path,
) -> None:
    command = _frozen_image_build_command(
        "service:test",
        "services/data/Dockerfile",
        ".",
        dependency_root=tmp_path,
    )

    assert command[:7] == [
        command[0],
        "buildx",
        "build",
        "--load",
        "--platform",
        "linux/amd64",
        "--network=none",
    ]
    assert "--build-context" in command
    assert command[-5:] == ["-t", "service:test", "-f", "services/data/Dockerfile", "."]


def test_instance_data_roots_are_prepared_and_disjoint(tmp_path: Path) -> None:
    source, restored = _prepare_instance_data(tmp_path)

    assert source != restored
    assert {
        path.relative_to(source).as_posix()
        for path in source.rglob("*")
        if path.stat().st_mode & 0o777 == 0o777
    } == {"data/database", "data/objects", "knowledge/database"}
    assert (restored / "knowledge/database").stat().st_mode & 0o777 == 0o777
    assert not (restored / "data").exists()


def test_pytest_junit_summary_aggregates_suite_counts(tmp_path: Path) -> None:
    report = tmp_path / "pytest.xml"
    report.write_text(
        '<testsuites><testsuite tests="3" failures="0" errors="0" skipped="1"/>'
        '<testsuite tests="2" failures="1" errors="0" skipped="0"/></testsuites>',
        encoding="utf-8",
    )

    assert _pytest_junit_summary(report) == {
        "tests": 5,
        "failures": 1,
        "errors": 0,
        "skipped": 1,
        "passed": 3,
    }


def test_pytest_report_rejects_skipped_tests(tmp_path: Path) -> None:
    report = tmp_path / "pytest.xml"
    report.write_text(
        '<testsuite tests="2" failures="0" errors="0" skipped="1"/>',
        encoding="utf-8",
    )

    result = _pytest_report(report, return_code=0)

    assert result["status"] == "FAIL"
    assert result["tests"] == 2
    assert result["passed"] == 1
    assert result["skipped"] == 1
    assert result["junitSha256"]


def test_joint_backend_suites_use_run_owned_pytest_temp_roots() -> None:
    run = Path("/cache/validation/data-knowledge-e2e/run-1")
    command = _pytest_command(
        ("services/data/tests",),
        junit=run / "data.xml",
        basetemp=run / "data-pytest-temp",
    )

    assert command[command.index("--basetemp") + 1] == str(run / "data-pytest-temp")
    assert "/tmp/pytest-of-" not in " ".join(command)


def test_startup_retry_is_classified_without_being_treated_as_failure() -> None:
    recorder = Recorder()
    recorder.run(
        ["python", "-c", "raise SystemExit(2)"],
        capture=True,
        check=False,
        phase="startup_wait",
        expected_return_codes={1, 2},
    )
    assert recorder.commands[-1]["classification"] == "EXPECTED_STARTUP_RETRY"
    assert recorder.commands[-1]["phase"] == "startup_wait"


def test_service_log_fatal_is_a_stable_failure_finding() -> None:
    findings = _scan_log(
        'postgres-1 | FATAL: password authentication failed for user \\"data\\"\n'
    )
    assert findings == [{"code": "UNEXPECTED_FATAL", "line": 1}]


def test_service_log_startup_warning_is_not_a_finding() -> None:
    assert (
        _scan_log(
            'initdb: warning: enabling "trust" authentication for local connections\n'
        )
        == []
    )


def test_service_log_database_init_fatal_is_expected_noise() -> None:
    assert (
        _scan_log(
            "database system is ready to accept connections\n"
            'FATAL:  database "knowledge" does not exist\n'
            "CREATE DATABASE\n"
        )
        == []
    )


def test_service_log_data_database_init_fatal_is_expected_noise() -> None:
    assert (
        _scan_log(
            "database system is ready to accept connections\n"
            'FATAL:  database "data" does not exist\n'
            "CREATE DATABASE\n"
        )
        == []
    )


def test_service_log_database_init_allows_bounded_compose_interleaving() -> None:
    assert (
        _scan_log(
            "knowledge-db-1 | database system is ready to accept connections\n"
            'knowledge-db-1 | FATAL:  database "knowledge" does not exist\n'
            'api-1          | GET /knowledge/v3/imports HTTP/1.1" 200 OK\n'
            "knowledge-db-1 | done\n"
            'api-1          | GET /knowledge/v3/imports HTTP/1.1" 200 OK\n'
            "knowledge-db-1 | server started\n"
            'api-1          | GET /knowledge/v3/imports HTTP/1.1" 200 OK\n'
            "knowledge-db-1 | CREATE DATABASE\n"
        )
        == []
    )


def test_service_log_database_fatal_is_not_hidden_by_later_same_stream_create() -> None:
    findings = _scan_log(
        'knowledge-db-1 | FATAL:  database "knowledge" does not exist\n'
        "knowledge-db-1 | unrelated one\n"
        "knowledge-db-1 | unrelated two\n"
        "knowledge-db-1 | unrelated three\n"
        "knowledge-db-1 | CREATE DATABASE\n"
    )
    assert findings == [{"code": "UNEXPECTED_FATAL", "line": 1}]


def test_service_log_database_fatal_is_not_hidden_by_distant_create() -> None:
    findings = _scan_log(
        'FATAL:  database "data" does not exist\n' + "\n" * 5 + "CREATE DATABASE\n"
    )
    assert findings == [{"code": "UNEXPECTED_FATAL", "line": 1}]


def test_service_log_controlled_shutdown_fatal_is_expected_noise() -> None:
    assert (
        _scan_log(
            "received fast shutdown request\n"
            "waiting for server to shut down\n"
            "aborting any active transactions\n"
            'background worker "logical replication launcher" exited\n'
            "shutting down\n"
            "checkpoint starting: shutdown immediate\n"
            "FATAL:  the database system is shutting down\n"
            "checkpoint complete\n"
            "database system is shut down\n"
        )
        == []
    )


def test_service_log_controlled_shutdown_allows_bounded_compose_interleaving() -> None:
    api_poll = 'api-1          | GET /knowledge/v3/imports HTTP/1.1" 200 OK\n'
    assert (
        _scan_log(
            "knowledge-db-1 | received fast shutdown request\n"
            "knowledge-db-1 | aborting any active transactions\n"
            "knowledge-db-1 | shutting down\n"
            "knowledge-db-1 | checkpoint starting: shutdown immediate\n"
            + api_poll * 10
            + "knowledge-db-1 | FATAL:  the database system is shutting down\n"
            + api_poll
            + "knowledge-db-1 | checkpoint complete\n"
            + "knowledge-db-1 | database system is shut down\n"
        )
        == []
    )


def test_service_log_shutdown_fatal_is_not_hidden_by_distant_lifecycle() -> None:
    findings = _scan_log(
        "received fast shutdown request\n"
        + "\n" * 9
        + "FATAL:  the database system is shutting down\n"
        + "database system is shut down\n"
    )
    assert findings == [{"code": "UNEXPECTED_FATAL", "line": 11}]


def test_free_ports_are_distinct_and_released() -> None:
    ports = _free_ports(3)
    assert len(set(ports)) == 3
    listeners = []
    try:
        for port in ports:
            listener = socket.socket()
            listener.bind(("127.0.0.1", port))
            listeners.append(listener)
    finally:
        for listener in listeners:
            listener.close()


def test_overrides_use_only_the_requested_images_project_and_network(
    tmp_path: Path,
) -> None:
    data, knowledge = _write_overrides(
        tmp_path,
        data_image="data:test",
        knowledge_image="knowledge:test",
        network="joint-test-network",
        default_network="default-test-network",
        project_id="joint-test-project",
    )
    data_config = json.loads(data.read_text(encoding="utf-8"))
    knowledge_config = json.loads(knowledge.read_text(encoding="utf-8"))

    assert data_config["networks"]["joint"] == {
        "external": True,
        "name": "joint-test-network",
    }
    assert data_config["services"]["api"]["networks"]["joint"] == {
        "aliases": ["data-api", "data-service"]
    }
    assert "postgres" not in data_config["services"]
    assert "data_db" not in data_config["networks"]
    assert data_config["services"]["data-db"]["networks"] == {"joint": {}}
    assert knowledge_config["services"]["api"]["networks"]["joint"] == {
        "aliases": ["knowledge-next-service"]
    }
    assert "postgres" not in knowledge_config["services"]
    assert "knowledge_db" not in knowledge_config["networks"]
    assert knowledge_config["services"]["knowledge-db"]["networks"] == {
        "default": None,
        "joint": {"aliases": ["knowledge-db-joint-test-project"]},
    }
    assert knowledge_config["networks"]["default"] == {
        "external": True,
        "name": "default-test-network",
    }
    for service in ("migrate", "api", "worker"):
        assert data_config["services"][service]["image"] == "data:test"
        assert data_config["services"][service]["build"] is None
        assert data_config["services"][service]["environment"]["DATA_PROJECT"] == (
            "joint-test-project"
        )
        assert knowledge_config["services"][service]["image"] == "knowledge:test"
        environment = knowledge_config["services"][service]["environment"]
        assert environment["KNOWLEDGE_PROJECT"] == "joint-test-project"
        assert environment["KNOWLEDGE_DATA_URL"] == "http://data-api:8000"
        assert environment["PLATFORM_EMBEDDING_BASE_URL"] == (
            "http://embedding-stub:8000"
        )
        assert (
            data_config["services"][service]["environment"]["DATA_DATABASE_URL"]
            == "postgresql://data:data-local@data-db:5432/data"
        )


def test_readiness_waits_for_an_explicit_success_response() -> None:
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(503, json={"status": "NOT_READY"})
        return httpx.Response(200, json={"status": "READY"})

    with httpx.Client(
        base_url="http://test",
        transport=httpx.MockTransport(handler),
    ) as client:
        assert _wait_http_ready(client, timeout=2) == {"status": "READY"}
    assert attempts == 2


@pytest.mark.parametrize("path", ["/data/", "/knowledge/"])
@pytest.mark.parametrize("status", [401, 500])
def test_gateway_readiness_rejects_failed_pages(path: str, status: int) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v12/health":
            return httpx.Response(200, json={"status": "UP"})
        if request.url.path == "/":
            return httpx.Response(
                200, text="<html></html>", headers={"content-type": "text/html"}
            )
        return httpx.Response(
            status if request.url.path == path else 200,
            text="<html></html>", headers={"content-type": "text/html"},
        )

    with httpx.Client(base_url="http://test", transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(RuntimeError, match=f"GATEWAY_PAGE_INVALID:{path}:status={status}"):
            _check_gateway_pages(client)


def test_gateway_readiness_checks_root_and_both_pages() -> None:
    visited: list[tuple[str, str | None]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        visited.append((request.url.path, request.headers.get("authorization")))
        if request.url.path == "/api/v12/health":
            return httpx.Response(200, json={"status": "UP"})
        return httpx.Response(200, text="<html></html>", headers={"content-type": "text/html"})

    with httpx.Client(base_url="http://test", transport=httpx.MockTransport(handler)) as client:
        checks = _check_gateway_pages(client, api_token="private-token")
    assert checks["gatewayPages"]["status"] == "PASS"
    assert visited == [
        ("/api/v12/health", None),
        ("/", "Bearer private-token"),
        ("/data/", "Bearer private-token"),
        ("/knowledge/", "Bearer private-token"),
    ]


def test_identity_gate_bootstraps_a_real_token_without_recording_it() -> None:
    class FakeRecorder:
        def __init__(self) -> None:
            self.command: list[str] = []

        def run(self, command: list[str], *, capture: bool):
            assert capture is True
            self.command = command
            return SimpleNamespace(
                stdout=json.dumps(
                    {"subject": "user-1", "token": "gwr_private-test-token"}
                )
            )

    recorder = FakeRecorder()
    subject, token = _bootstrap_identity_token(
        recorder, "kernel-test", "joint-test-project"
    )

    assert subject == "user-1"
    assert token == "gwr_private-test-token"
    assert "gwr_private-test-token" not in " ".join(recorder.command)
    assert "PLATFORM_E2E_PROJECT_ID=joint-test-project" in recorder.command


def test_service_log_detects_missing_identity_upstream() -> None:
    findings = _scan_log(
        "2026/11/01 14:00:00 [notice] 1#1: start worker processes\n"
        "2026/11/01 14:00:01 [error] 20#20: *1 kernel-service could not be resolved (2: Server failure)\n"
        "2026/11/01 14:00:01 [error] 20#20: *1 auth request unexpected status: 502\n"
    )
    assert findings == [
        {"code": "NGINX_ERROR", "line": 2},
        {"code": "NGINX_ERROR", "line": 3},
    ]


def test_playwright_counts_come_from_this_run(tmp_path: Path) -> None:
    report = tmp_path / "playwright.json"
    report.write_text(json.dumps({"stats": {"expected": 9, "unexpected": 0, "flaky": 0, "skipped": 0}}))
    assert _playwright_passed(report) == 9
    report.write_text(json.dumps({"stats": {"expected": 8, "unexpected": 0, "flaky": 0, "skipped": 1}}))
    with pytest.raises(RuntimeError, match="PLAYWRIGHT_TESTS_INCOMPLETE"):
        _playwright_passed(report)
