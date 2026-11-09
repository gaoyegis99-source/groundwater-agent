"""End-to-end Data/Knowledge lifecycle orchestration."""

from __future__ import annotations
import json
import os
from pathlib import Path
import re
import shutil
import sys
import traceback
import uuid
from typing import Any
import httpx
from scripts.maintenance.project_environment import (
    docker_executable,
    validation_cache_root,
)
from .context import Recorder
from .diagnostics import _capture_logs, _cleanup, _joint_http_checks
from .environment import (
    DATA_COMPOSE,
    KNOWLEDGE_COMPOSE,
    PGVECTOR_IMAGE,
    ROOT,
    _compose,
    _compose_port,
    _docker_port,
    _frozen_image_build_command,
    _free_ports,
    _now,
    _prepare_instance_data,
    _secondary_subnet,
    _wait_postgres,
    _wait_stub,
    _write_overrides,
)
from .identity import _check_gateway_pages, _start_identity_kernel
from .report import _playwright_passed, _pytest_report, _write_report

RUN_ID = re.compile(r"^[a-z0-9]{8,24}$")


def _pytest_command(
    tests: tuple[str, ...],
    *,
    junit: Path,
    basetemp: Path,
    clear_repository_addopts: bool = False,
) -> list[str]:
    command = [
        sys.executable,
        "-m",
        "pytest",
        "-q",
        "--basetemp",
        str(basetemp),
    ]
    if clear_repository_addopts:
        command.extend(("-o", "addopts="))
    return [*command, *tests, "--junitxml", str(junit)]


def _remove_pytest_basetemp(path: Path) -> None:
    """Fail explicitly if a test leaves a temp tree the runner cannot own."""

    if path.exists():
        shutil.rmtree(path)


def run(args) -> int:
    run_id = args.run_id or uuid.uuid4().hex[:12]
    if not RUN_ID.fullmatch(run_id):
        raise ValueError("RUN_ID_INVALID")
    work = validation_cache_root(ROOT) / "data-knowledge-e2e" / run_id
    work.mkdir(parents=True, exist_ok=False)
    output = (args.output or work / "report.json").resolve()
    recorder = Recorder()
    started = _now()

    data_project = f"data-scope-{run_id}"
    knowledge_project = f"knowledge-scope-{run_id}"
    restored_project = f"{knowledge_project}-restore"
    project_id = f"joint-{run_id}"
    network = f"data-knowledge-scope-{run_id}"
    default_network = f"data-knowledge-default-{run_id}"
    data_image = f"platform-data-service:joint-{run_id}"
    knowledge_image = f"platform-knowledge-next:joint-{run_id}"
    gateway_image = f"platform-gateway:joint-{run_id}"
    kernel_image = f"platform-kernel:joint-{run_id}"
    stub_image = f"platform-test-upstream:joint-{run_id}"
    stub_container = f"embedding-stub-{run_id}"
    gateway_container = f"gateway-{run_id}"
    kernel_container = f"kernel-identity-{run_id}"
    test_db_container = f"knowledge-test-db-{run_id}"

    data_override, knowledge_override = _write_overrides(
        work,
        data_image=data_image,
        knowledge_image=knowledge_image,
        network=network,
        default_network=default_network,
        project_id=project_id,
    )
    data_compose = _compose(data_project, DATA_COMPOSE, data_override)
    knowledge_compose = _compose(
        knowledge_project, KNOWLEDGE_COMPOSE, knowledge_override
    )
    restored_compose = _compose(restored_project, KNOWLEDGE_COMPOSE, knowledge_override)
    data_host_port, knowledge_host_port, gateway_host_port, restore_port = _free_ports(
        4
    )
    source_instance_root, restored_instance_root = _prepare_instance_data(work)
    data_env = {
        **os.environ,
        "DATA_PORT": str(data_host_port),
        "PLATFORM_INSTANCE_DATA_ROOT": str(source_instance_root),
    }
    knowledge_env = {
        **os.environ,
        "KNOWLEDGE_PORT": str(knowledge_host_port),
        "KNOWLEDGE_PROJECT": project_id,
        "KNOWLEDGE_DATA_URL": "http://data-api:8000",
        "PLATFORM_EMBEDDING_BASE_URL": "http://embedding-stub:8000",
        "KNOWLEDGE_COMPOSE_OVERRIDE": str(knowledge_override),
        "PLATFORM_INSTANCE_DATA_ROOT": str(source_instance_root),
    }
    restored_knowledge_env = {
        **knowledge_env,
        "KNOWLEDGE_PORT": str(restore_port),
        "PLATFORM_INSTANCE_DATA_ROOT": str(restored_instance_root),
    }
    restore_override = work / "knowledge-restore.json.restore.override.json"
    report: dict[str, Any] = {
        "schemaVersion": "data-knowledge-e2e.v2",
        "status": "RUNNING",
        "startedAt": started,
        "baseline": {},
        "isolation": {
            "runId": run_id,
            "projectId": project_id,
            "dataComposeProject": data_project,
            "knowledgeComposeProject": knowledge_project,
            "restoredKnowledgeComposeProject": restored_project,
            "network": network,
            "defaultNetwork": default_network,
            "sourceInstanceRoot": str(source_instance_root),
            "restoredInstanceRoot": str(restored_instance_root),
            "identityKernelContainer": kernel_container,
        },
        "checks": {},
    }
    error: str | None = None
    network_created = False
    default_network_created = False
    images_built: list[str] = []
    try:
        recorder.run([docker_executable(), "info", "--format", "{{.ServerVersion}}"])
        recorder.run([docker_executable(), "compose", "version"])
        recorder.run([sys.executable, "--version"])
        recorder.run(["node", "--version"])
        recorder.run(["npm", "--version"])
        head = recorder.run(["git", "rev-parse", "HEAD"], capture=True).stdout.strip()
        status = recorder.run(
            ["git", "status", "--short"], capture=True
        ).stdout.splitlines()
        report["baseline"] = {"head": head, "worktree": status}

        for image, dockerfile, context in (
            (data_image, "services/data/Dockerfile", "."),
            (knowledge_image, "services/knowledge-next/Dockerfile", "."),
            (gateway_image, "services/gateway/Dockerfile", "."),
            (kernel_image, "kernel/Dockerfile", "."),
            (
                stub_image,
                "tests/e2e/compose-stub/Dockerfile",
                "tests/e2e/compose-stub",
            ),
        ):
            recorder.run(
                _frozen_image_build_command(image, dockerfile, context)
            )
            images_built.append(image)
        report["images"] = {
            image: recorder.run(
                [docker_executable(), "image", "inspect", "--format", "{{.Id}}", image],
                capture=True,
            ).stdout.strip()
            for image in images_built
        }

        recorder.run(
            [
                docker_executable(),
                "run",
                "-d",
                "--name",
                test_db_container,
                "-e",
                "POSTGRES_USER=knowledge",
                "-e",
                "POSTGRES_PASSWORD=knowledge-test",
                "-e",
                "POSTGRES_DB=knowledge_test",
                "-p",
                "127.0.0.1::5432",
                PGVECTOR_IMAGE,
            ]
        )
        _wait_postgres(recorder, test_db_container)
        test_db_port = _docker_port(recorder, test_db_container, "5432/tcp")

        data_test_env = {
            **os.environ,
            "DATA_TEST_BACKUP_IMAGE": "1",
            "DATA_TEST_IMAGE": data_image,
        }
        data_junit = work / "data-backend-junit.xml"
        data_pytest_temp = work / "data-pytest-temp"
        try:
            data_tests = recorder.run(
                _pytest_command(
                    ("services/data/tests",),
                    junit=data_junit,
                    basetemp=data_pytest_temp,
                ),
                env=data_test_env,
                check=False,
            )
        finally:
            _remove_pytest_basetemp(data_pytest_temp)
        report["checks"]["dataBackend"] = _pytest_report(
            data_junit, data_tests.returncode
        )
        if report["checks"]["dataBackend"]["status"] != "PASS":
            raise RuntimeError("DATA_BACKEND_TESTS_FAILED")

        knowledge_test_env = {
            **os.environ,
            "TEST_KNOWLEDGE_DATABASE_URL": (
                "postgresql://knowledge:knowledge-test@127.0.0.1:"
                f"{test_db_port}/knowledge_test"
            ),
        }
        knowledge_junit = work / "knowledge-backend-junit.xml"
        knowledge_pytest_temp = work / "knowledge-pytest-temp"
        try:
            knowledge_tests = recorder.run(
                _pytest_command(
                    ("tests",),
                    junit=knowledge_junit,
                    basetemp=knowledge_pytest_temp,
                    clear_repository_addopts=True,
                ),
                env=knowledge_test_env,
                cwd=ROOT / "services/knowledge-next",
                check=False,
            )
        finally:
            _remove_pytest_basetemp(knowledge_pytest_temp)
        report["checks"]["knowledgeBackend"] = _pytest_report(
            knowledge_junit, knowledge_tests.returncode
        )
        if report["checks"]["knowledgeBackend"]["status"] != "PASS":
            raise RuntimeError("KNOWLEDGE_BACKEND_TESTS_FAILED")

        ui_counts = {}
        for package, script in (
            ("services/data/web", "test"),
            ("services/data/web", "typecheck"),
            ("services/data/web", "build"),
            ("services/data/web", "test:ui"),
            ("services/knowledge-next/ui", "test"),
            ("services/knowledge-next/ui", "typecheck"),
            ("services/knowledge-next/ui", "build"),
            ("services/knowledge-next/ui", "test:ui"),
        ):
            command = ["npm", "--prefix", package, "run", script]
            if script == "test:ui":
                ui_report = work / f"{package.replace('/', '-')}-playwright.json"
                recorder.run(
                    [*command, "--", "--reporter=line,json"],
                    env={**os.environ, "PLAYWRIGHT_JSON_OUTPUT_NAME": str(ui_report)},
                )
                ui_counts[package] = _playwright_passed(ui_report)
            else:
                recorder.run(command)

        network_command = [docker_executable(), "network", "create"]
        if args.network_subnet:
            network_command.extend(["--subnet", args.network_subnet])
        network_command.append(network)
        recorder.run(network_command)
        network_created = True
        recorder.run(
            [
                docker_executable(),
                "network",
                "create",
                "--subnet",
                _secondary_subnet(args.network_subnet),
                default_network,
            ]
        )
        default_network_created = True
        recorder.run(
            [
                docker_executable(),
                "run",
                "-d",
                "--rm",
                "--name",
                stub_container,
                "--network",
                network,
                "--network-alias",
                "embedding-stub",
                stub_image,
            ]
        )
        _wait_stub(recorder, stub_container)
        identity_check, compiled_bundle, api_token = _start_identity_kernel(
            recorder, work, image=kernel_image, container=kernel_container,
            network=network, project_id=project_id,
        )
        report["checks"]["gatewayIdentity"] = identity_check

        recorder.run([*data_compose, "config", "--quiet"], env=data_env)
        recorder.run(
            [*data_compose, "up", "-d", "--no-build", "--wait"],
            env=data_env,
        )
        data_port = _compose_port(recorder, data_compose, data_env)
        assert data_port == data_host_port
        data_url = f"http://127.0.0.1:{data_port}"
        recorder.run([*knowledge_compose, "config", "--quiet"], env=knowledge_env)
        recorder.run(
            [*knowledge_compose, "up", "-d", "--no-build", "--wait"],
            env=knowledge_env,
        )
        knowledge_port = _compose_port(recorder, knowledge_compose, knowledge_env)
        assert knowledge_port == knowledge_host_port
        knowledge_url = f"http://127.0.0.1:{knowledge_port}"
        recorder.run(
            [
                docker_executable(),
                "run",
                "-d",
                "--name",
                gateway_container,
                "--network",
                network,
                "-p",
                f"127.0.0.1:{gateway_host_port}:8080",
                "-v",
                f"{compiled_bundle / 'nginx.conf'}:/etc/nginx/nginx.conf:ro",
                "-v",
                f"{compiled_bundle / 'product-ui.json'}:"
                "/usr/share/nginx/html/product-ui.json:ro",
                "-v",
                f"{compiled_bundle / 'product-icon.svg'}:"
                "/usr/share/nginx/html/product-icon.svg:ro",
                gateway_image,
            ]
        )
        gateway_url = f"http://127.0.0.1:{gateway_host_port}"
        with httpx.Client(
            base_url=gateway_url, timeout=5, trust_env=False
        ) as gateway:
            report["checks"].update(
                _check_gateway_pages(gateway, api_token=api_token)
            )
        report["isolation"].update(
            {
                "dataUrl": data_url,
                "knowledgeUrl": knowledge_url,
                "gatewayUrl": gateway_url,
            }
        )

        live_data_env = {
            **os.environ,
            "DATA_E2E_URL": gateway_url,
            "PLATFORM_E2E_API_TOKEN": api_token,
            "DATA_E2E_SCREENSHOT": str(work / "data-browser.png"),
            "PLAYWRIGHT_JSON_OUTPUT_NAME": str(work / "data-live-playwright.json"),
        }
        recorder.run(
            ["npm", "--prefix", "services/data/web", "run", "test:e2e", "--", "--reporter=line,json"],
            env=live_data_env,
            output_log=work / "data-ui-e2e.log",
        )
        report["checks"]["dataUi"] = {
            "status": "PASS",
            "mockedPlaywright": ui_counts["services/data/web"],
            "livePlaywright": _playwright_passed(work / "data-live-playwright.json"),
        }
        joint = _joint_http_checks(
            recorder,
            data_url=data_url,
            knowledge_url=knowledge_url,
            project_id=project_id,
            data_compose=data_compose,
            data_env=data_env,
            run_id=run_id,
        )
        report["checks"]["jointHttp"] = joint

        knowledge_ui_env = {
            **knowledge_env,
            "DATA_E2E_URL": gateway_url,
            "KNOWLEDGE_UI_URL": gateway_url,
            "OPERATOR_E2E_URL": gateway_url,
            "PLATFORM_E2E_API_TOKEN": api_token,
            "PLAYWRIGHT_JSON_OUTPUT_NAME": str(work / "knowledge-live-playwright.json"),
        }
        recorder.run(
            [
                "npm",
                "--prefix",
                "services/knowledge-next/ui",
                "run",
                "test:e2e",
                "--",
                "--reporter=line,json",
            ],
            env=knowledge_ui_env,
            output_log=work / "knowledge-ui-e2e.log",
        )
        report["checks"]["knowledgeUi"] = {
            "status": "PASS",
            "mockedPlaywright": ui_counts["services/knowledge-next/ui"],
            "livePlaywright": _playwright_passed(work / "knowledge-live-playwright.json"),
        }

        backup = work / "knowledge.dump"
        restore_report = work / "knowledge-restore.json"
        recorder.run(
            [
                sys.executable,
                "services/knowledge-next/scripts/check_restore_states.py",
                "--project",
                knowledge_project,
                "--url",
                knowledge_url,
                "--restore-port",
                str(restore_port),
                "--backup",
                str(backup),
                "--output",
                str(restore_report),
                "--compose-override",
                str(knowledge_override),
                "--restore-instance-data-root",
                str(restored_instance_root),
            ],
            env=knowledge_env,
        )
        if not restore_override.is_file():
            raise RuntimeError("KNOWLEDGE_RESTORE_OVERRIDE_MISSING")
        restored_compose = _compose(
            restored_project, KNOWLEDGE_COMPOSE, restore_override
        )
        restored_knowledge_env["KNOWLEDGE_COMPOSE_OVERRIDE"] = str(
            restore_override
        )
        report["checks"]["knowledgeRestore"] = json.loads(
            restore_report.read_text(encoding="utf-8")
        )
        report["status"] = "PASS"
    except Exception:
        error = traceback.format_exc()
        report["status"] = "FAIL"
        report["error"] = error
        if restore_override.is_file():
            restored_compose = _compose(
                restored_project, KNOWLEDGE_COMPOSE, restore_override
            )
            restored_knowledge_env["KNOWLEDGE_COMPOSE_OVERRIDE"] = str(
                restore_override
            )
        for name, compose, env in (
            ("data", data_compose, data_env),
            ("knowledge", knowledge_compose, knowledge_env),
            ("knowledge-restored", restored_compose, restored_knowledge_env),
        ):
            _capture_logs(recorder, work, name, [*compose, "logs", "--no-color"], env)
    finally:
        # Always retain bounded service logs, including successful runs. This
        # lets the report distinguish expected startup/teardown noise from an
        # application error instead of only preserving logs after failure.
        report["logs"] = {
            name: _capture_logs(recorder, work, name, [*compose, "logs", "--no-color"], env)
            for name, compose, env in (
                ("data", data_compose, data_env),
                ("knowledge", knowledge_compose, knowledge_env),
                ("knowledge-restored", restored_compose, restored_knowledge_env),
            )
        }
        report["logs"].update(
            {
                name: _capture_logs(
                    recorder, work, name, [docker_executable(), "logs", container]
                )
                for name, container in (("kernel", kernel_container), ("gateway", gateway_container))
            }
        )
        logs = report["logs"]
        log_findings = [
            {"service": name, **finding}
            for name, item in logs.items()  # type: ignore[union-attr]
            for finding in item.get("findings", [])  # type: ignore[union-attr]
        ]
        report.setdefault("diagnostics", {})["unexpectedLogFindings"] = log_findings
        if log_findings:
            report["status"] = "FAIL"
            report["error"] = "UNEXPECTED_SERVICE_LOG_FINDINGS"
        cleanup_commands: list[dict[str, object]] = []
        cleanup_commands.append(
            _cleanup(
                recorder,
                [*restored_compose, "down", "-v", "--remove-orphans"],
                env=restored_knowledge_env,
            )
        )
        cleanup_commands.append(
            _cleanup(
                recorder,
                [*knowledge_compose, "down", "-v", "--remove-orphans"],
                env=knowledge_env,
            )
        )
        cleanup_commands.append(
            _cleanup(
                recorder,
                [*data_compose, "down", "-v", "--remove-orphans"],
                env=data_env,
            )
        )
        cleanup_commands.extend(
            _cleanup(recorder, [docker_executable(), "rm", "-f", container])
            for container in (gateway_container, kernel_container, stub_container, test_db_container)
        )
        if network_created:
            cleanup_commands.append(
                _cleanup(recorder, [docker_executable(), "network", "rm", network])
            )
        if default_network_created:
            cleanup_commands.append(
                _cleanup(
                    recorder, [docker_executable(), "network", "rm", default_network]
                )
            )
        for image in reversed(images_built):
            cleanup_commands.append(
                _cleanup(recorder, [docker_executable(), "image", "rm", image])
            )
        remaining_containers: list[str] = []
        for project in (data_project, knowledge_project, restored_project):
            remaining_containers.extend(
                recorder.run(
                    [
                        docker_executable(),
                        "ps",
                        "-a",
                        "--filter",
                        f"label=com.docker.compose.project={project}",
                        "--format",
                        "{{.Names}}",
                    ],
                    capture=True,
                    check=False,
                ).stdout.splitlines()
            )
        residue = {
            "containers": remaining_containers,
            "network": sum(
                (
                    recorder.run(
                        [
                            docker_executable(),
                            "network",
                            "ls",
                            "--filter",
                            f"name=^{network_name}$",
                            "--format",
                            "{{.Name}}",
                        ],
                        capture=True,
                        check=False,
                    ).stdout.splitlines()
                    for network_name in (network, default_network)
                ),
                [],
            ),
            "volumes": [],
            "images": [],
        }
        for container in (gateway_container, kernel_container, stub_container, test_db_container):
            remaining_containers.extend(
                recorder.run(
                    [docker_executable(), "ps", "-a", "--filter", f"name=^/{container}$", "--format", "{{.Names}}"],
                    capture=True, check=False, phase="teardown",
                ).stdout.splitlines()
            )
        for project in (data_project, knowledge_project, restored_project):
            residue["volumes"].extend(
                recorder.run(
                    [
                        docker_executable(),
                        "volume",
                        "ls",
                        "--filter",
                        f"label=com.docker.compose.project={project}",
                        "--format",
                        "{{.Name}}",
                    ],
                    capture=True,
                    check=False,
                    phase="teardown",
                ).stdout.splitlines()
            )
        for image in images_built:
            inspected = recorder.run(
                [docker_executable(), "image", "inspect", image],
                capture=True,
                check=False,
                phase="teardown",
                expected_return_codes={1},
            )
            if inspected.returncode == 0:
                residue["images"].append(image)
        unexpected_cleanup_failures = [
            item for item in cleanup_commands if item["status"] != "PASS"
        ]
        unexpected_nonzero = [
            item
            for item in recorder.commands
            if item.get("returnCode")
            and item.get("classification") == "UNEXPECTED_NONZERO"
        ]
        report["teardown"] = {
            "status": "PASS"
            if (
                not residue["containers"]
                and not residue["network"]
                and not residue["volumes"]
                and not residue["images"]
                and not unexpected_cleanup_failures
            )
            else "FAIL",
            "residue": residue,
            "cleanupCommands": cleanup_commands,
        }
        report["diagnostics"] = {
            "unexpectedLogFindings": log_findings,
            "expectedStartupRetries": sum(
                1
                for item in recorder.commands
                if item.get("classification") == "EXPECTED_STARTUP_RETRY"
            ),
            "unexpectedNonzeroCommands": unexpected_nonzero,
        }
        report["commandLogs"] = [
            item["outputLog"] for item in recorder.commands if item.get("outputLog")
        ]
        if unexpected_nonzero:
            report["status"] = "FAIL"
        if report["teardown"]["status"] != "PASS":
            report["status"] = "FAIL"
        report["finishedAt"] = _now()
        report["commands"] = recorder.commands
        _write_report(output, report)
        print(f"Data/Knowledge report: {output}")
    if error:
        sys.stderr.write(error)
        return 1
    return 0 if report["status"] == "PASS" else 1
