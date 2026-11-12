"""Isolated assembled-product scenario orchestration."""

from __future__ import annotations
import json
import os
from pathlib import Path
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import httpx
from assembler import compile_product
from scripts.evaluation.evaluate_agent import FIXED_SCENARIOS, run_suite
from scripts.maintenance.project_environment import (
    docker_executable,
    lifecycle,
    validation_cache_root,
)
from scripts.lib.e2e_http import TOKEN_ENVIRONMENT
from tests.e2e.groundwater import porous_live
from .assertions import (
    _audit_case,
    _check_retired_contracts,
    _observation_assertions,
    _run_interaction_options,
    _verify_bootstrap_failure_isolated,
    _verify_bootstrap_publications,
    _verify_executor_restart,
    _verify_task_collaboration,
)
from .environment import (
    ROOT,
    _bootstrap_e2e_api_token,
    _compose_secrets,
    _configure_embedding_provider,
    _run,
    _test_upstream_build,
)
from .live_reply_quality import (
    LiveReplyQualityConfig,
    configure_kernel,
    run_reply_quality,
)
from .report import _assert_no_secret_leaks
from .topology import (
    _check_gateway_request_ids,
    _check_local_topology,
    _check_project_boundary,
    _check_public_readiness,
    _published_service_port,
)


def _relocate_generated_volume(volume: str | dict, generated: Path, config: Path) -> str | dict:
    if isinstance(volume, str):
        return volume.replace(str(generated), str(config))
    if isinstance(volume, dict) and isinstance(volume.get("source"), str):
        return {
            **volume,
            "source": volume["source"].replace(str(generated), str(config)),
        }
    raise ValueError("COMPOSE_VOLUME_INVALID")


def _copy_compose_source(directory: Path) -> Path:
    source_root = directory / "source"
    for name in ("kernel", "services", "packages", "products", "domains", "web"):
        shutil.copytree(
            ROOT / name,
            source_root / name,
            ignore=shutil.ignore_patterns(
                "*.egg-info",
                ".pytest_cache",
                ".ruff_cache",
                ".venv",
                "__pycache__",
                "build",
                "node_modules",
                "dist",
                "test-results",
                "playwright-report",
            ),
        )
    for runtime in ("build", "native", "qgis"):
        lock_directory = source_root / "environments" / runtime
        lock_directory.mkdir(parents=True)
        shutil.copy2(
            ROOT / "environments" / runtime / "requirements.lock",
            lock_directory / "requirements.lock",
        )
    for filename in (
        "package.json", "package-lock.json", ".npmrc", "system-deps.lock.json",
    ):
        shutil.copy2(ROOT / filename, source_root / filename)
    shutil.copy2(ROOT / ".dockerignore", source_root / ".dockerignore")
    return source_root


def _available_loopback_ports(count: int) -> tuple[int, ...]:
    """Reserve distinct ephemeral ports long enough to discover their numbers."""

    sockets: list[socket.socket] = []
    try:
        for _ in range(count):
            selected = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            selected.bind(("127.0.0.1", 0))
            sockets.append(selected)
        return tuple(int(selected.getsockname()[1]) for selected in sockets)
    finally:
        for selected in sockets:
            selected.close()


def _configure_local_sustainability(environment_file: Path) -> None:
    state = lifecycle().state / "third-party" / "groundwater-sustainability"
    names = {
        "groundwater-sustainability-api-key": "api-key",
        "groundwater-sustainability-ca-bundle": "tls.crt",
    }
    for name, source in names.items():
        target = environment_file.parent / "secrets" / name
        target.chmod(0o600)
        target.write_bytes((state / source).read_bytes())
        target.chmod(0o444)
    updates = {
        "GROUNDWATER_SUSTAINABILITY_BASE_URL": "https://groundwater-sustainability.local:8443",
        "GROUNDWATER_SUSTAINABILITY_ALLOWED_HOST": "groundwater-sustainability.local",
    }
    lines = [
        line for line in environment_file.read_text().splitlines()
        if line.split("=", 1)[0] not in updates
    ]
    lines.extend(f"{key}={value}" for key, value in updates.items())
    environment_file.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _reclaim_rootless_instance_data(instance_data: Path, compose: dict) -> None:
    """Map container-owned acceptance files back to the invoking host user."""

    image = compose.get("services", {}).get("data-db", {}).get("image")
    if not isinstance(image, str) or not image:
        raise RuntimeError("COMPOSE_CLEANUP_IMAGE_MISSING")
    result = _run(
        [
            docker_executable(),
            "run",
            "--rm",
            "--network",
            "none",
            "--user",
            "0",
            "--mount",
            f"type=bind,src={instance_data},dst=/target",
            image,
            "/bin/sh",
            "-c",
            "set -eu; chown -R 0:0 /target; chmod -R u+rwX /target",
        ],
        check=False,
        capture_output=True,
    )
    if result.returncode != 0:
        raise RuntimeError(
            "COMPOSE_INSTANCE_DATA_RECLAIM_FAILED:\n"
            + result.stdout
            + result.stderr
        )


def _run_groundwater_knowledge(
    transfer_case=None,
    transfer_evidence=None,
    native_image=None,
    qgis_image=None,
    *,
    webui_only=False,
    interaction_only=False,
    webui_report=None,
    tasks_only=False,
    six_model_only=False,
    native_real_cases: Path | None = None,
    sustainability_only=False,
    sustainability_case: Path | None = None,
    porous: porous_live.PorousAcceptance | None = None,
    embedding_base_url=None,
    embedding_report=None,
    network_subnet=None,
    reply_quality: LiveReplyQualityConfig | None = None,
    product_id="groundwater",
) -> None:
    groundwater = product_id == "groundwater"
    if not groundwater and (
        transfer_case
        or webui_only
        or interaction_only
        or tasks_only
        or six_model_only
        or native_real_cases is not None
        or sustainability_only
        or porous is not None
        or reply_quality
    ):
        raise ValueError("PRODUCT_SPECIFIC_GATE_UNAVAILABLE")
    with tempfile.TemporaryDirectory(
        prefix=f"platform-{product_id}-e2e-source-"
    ) as directory:
        source_root = _copy_compose_source(Path(directory))
        if porous is not None:
            from products.groundwater.scripts.porous_candidate import stage_candidate

            stage_candidate(source_root, full=porous.fixtures is not None)
        generated = compile_product(
            source_root,
            source_root / "products" / product_id / "product-manifest.json",
        )
        _run_groundwater_product(
            generated,
            transfer_case,
            transfer_evidence,
            native_image,
            qgis_image,
            product_id=product_id,
            webui_only=webui_only,
            interaction_only=interaction_only,
            webui_report=webui_report,
            tasks_only=tasks_only,
            six_model_only=six_model_only,
            native_real_cases=native_real_cases,
            sustainability_only=sustainability_only,
            sustainability_case=sustainability_case,
            porous=porous,
            embedding_base_url=embedding_base_url,
            embedding_report=embedding_report,
            network_subnet=network_subnet,
            reply_quality=reply_quality,
        )


def _run_groundwater_product(
    generated: Path,
    transfer_case=None,
    transfer_evidence=None,
    native_image=None,
    qgis_image=None,
    *,
    webui_only=False,
    interaction_only=False,
    webui_report=None,
    tasks_only=False,
    six_model_only=False,
    native_real_cases: Path | None = None,
    sustainability_only=False,
    sustainability_case: Path | None = None,
    porous: porous_live.PorousAcceptance | None = None,
    embedding_base_url=None,
    embedding_report=None,
    network_subnet=None,
    reply_quality: LiveReplyQualityConfig | None = None,
    product_id="groundwater",
) -> None:
    compose = json.loads((generated / "compose.json").read_text(encoding="utf-8"))
    with_assessment = sustainability_only or porous is not None and porous.assessment_fixture is not None
    if with_assessment:
        compose["networks"]["sustainability-link"] = {
            "external": True,
            "name": "source-groundwater_v6-egress",
        }
        compose["services"]["executor-remote_http-worker"]["networks"]["sustainability-link"] = {}
    for service, selected in (
        ("executor-native-worker", native_image),
        ("executor-qgis-worker", qgis_image),
    ):
        if selected:
            compose["services"][service].pop("build")
            compose["services"][service]["image"] = selected
            compose["services"][service]["pull_policy"] = "never"
    python_context = [
        value
        for value in compose["services"]["kernel-service"]["build"]["additional_contexts"]
        if value.startswith("platform-base-python=")
    ]
    if len(python_context) != 1:
        raise RuntimeError("COMPOSE_PYTHON_BASE_CONTEXT_MISSING")
    compose["services"]["test-upstream"] = {
        "build": {
            "context": "",
            "dockerfile": "Dockerfile",
            "additional_contexts": python_context,
        },
        "healthcheck": {
            "test": [
                "CMD",
                "python",
                "-c",
                (
                    "import urllib.request; urllib.request.urlopen("
                    "'http://127.0.0.1:8000/health', timeout=2)"
                ),
            ],
            "interval": "2s",
            "timeout": "2s",
            "retries": 20,
        },
        # Kernel provider calls and Knowledge embedding calls are the only
        # intended test egress.  The generated services do not join Compose's
        # implicit default network, so the stub must join their declared
        # egress network explicitly.
        "networks": {"v6-egress": {}},
    }
    kernel = compose["services"]["kernel-service"]
    selected_services = set(
        json.loads((generated / "kernel-config.json").read_text())["services"]
    )
    project_id = kernel["environment"]["PLATFORM_PROJECT_ID"]
    if reply_quality is None:
        kernel["environment"].update(
            {
            "PLATFORM_AGENT_PROVIDER": "deepseek",
            "PLATFORM_AGENT_PROTOCOL": "responses",
            "PLATFORM_AGENT_BASE_URL": "http://test-upstream:8000/v1",
            "PLATFORM_AGENT_MODEL": "test-model",
            "PLATFORM_AGENT_CONTEXT_WINDOW_TOKENS": "131072",
            "PLATFORM_AGENT_MAX_OUTPUT_TOKENS": "4096",
            "PLATFORM_AGENT_REQUEST_TIMEOUT_SECONDS": "120",
            "PLATFORM_AGENT_SEGMENT_TIMEOUT_SECONDS": "300",
            "PLATFORM_INTENT_ROUTER_PROVIDER": "deepseek",
            "PLATFORM_INTENT_ROUTER_PROTOCOL": "responses",
            "PLATFORM_INTENT_ROUTER_BASE_URL": "http://test-upstream:8000/v1",
            "PLATFORM_INTENT_ROUTER_MODEL": "test-router-model",
            "PLATFORM_INTENT_ROUTER_CONTEXT_WINDOW_TOKENS": "131072",
            "PLATFORM_INTENT_ROUTER_MAX_OUTPUT_TOKENS": "256",
            "PLATFORM_INTENT_ROUTER_REQUEST_TIMEOUT_SECONDS": "120",
            "PLATFORM_INTENT_ROUTER_SEGMENT_TIMEOUT_SECONDS": "300",
            }
        )
        kernel.setdefault("depends_on", {})["test-upstream"] = {
            "condition": "service_healthy"
        }
    else:
        configure_kernel(kernel, reply_quality)
    if "knowledge-next" in selected_services:
        _configure_embedding_provider(compose, embedding_base_url)
    # Compose omits publication for an empty host port and the Engine treats
    # published port zero as no binding. Discover a loopback port
    # immediately before creating the isolated project.
    gateway_port, = _available_loopback_ports(1)
    compose["services"]["gateway"]["ports"] = [
        f"127.0.0.1:{gateway_port}:8080"
    ]
    if network_subnet is not None:
        compose.setdefault("networks", {})["default"] = {
            "ipam": {"config": [{"subnet": network_subnet}]}
        }

    project = f"platform-{product_id}-e2e-{secrets.token_hex(6)}"
    with tempfile.TemporaryDirectory(
        prefix="platform-knowledge-compose-e2e-"
    ) as directory:
        compose["services"]["test-upstream"]["build"]["context"] = str(
            _test_upstream_build(Path(directory))
        )
        environment_file = _compose_secrets(
            Path(directory),
            compose,
            remote_services=json.loads(
                (generated / "executor-config.json").read_text()
            )["remoteServices"],
            provider_key=(
                None if reply_quality is None else reply_quality.provider_key()
            ),
        )
        if with_assessment:
            _configure_local_sustainability(environment_file)
        if porous is not None:
            porous_live.configure_deployment(compose, environment_file, porous.image)
        instance_data = Path(directory) / "instance-data"
        config = Path(directory) / "config"
        shutil.copytree(generated, config)
        for service in compose["services"].values():
            if "volumes" in service:
                service["volumes"] = [
                    _relocate_generated_volume(volume, generated, config)
                    for volume in service["volumes"]
                ]
        compose_path = Path(directory) / "compose.json"
        compose_path.write_text(
            json.dumps(compose, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        command = [
            docker_executable(),
            "compose",
            "--env-file",
            str(environment_file),
            "-p",
            project,
            "-f",
            str(compose_path),
        ]
        previous_test_token = os.environ.get(TOKEN_ENVIRONMENT)
        try:
            try:
                if (
                    not (six_model_only or sustainability_only or interaction_only or native_real_cases is not None)
                    and porous is None
                    and reply_quality is None
                    and "knowledge-bootstrap" in compose["services"]
                ):
                    _verify_bootstrap_failure_isolated(command, compose, config)
                _run(
                    [
                        *command,
                        "up",
                        "-d",
                        "--build",
                        "--wait",
                        "--wait-timeout",
                        "180",
                    ]
                )
                browser_credentials = Path(directory) / "browser-credentials" if interaction_only else None
                os.environ[TOKEN_ENVIRONMENT] = _bootstrap_e2e_api_token(
                    command, project_id, browser_credentials=browser_credentials,
                )
                if "knowledge-bootstrap" in compose["services"]:
                    _verify_bootstrap_publications(command, config)
                gateway_address = _published_service_port(command, "gateway", "8080")
                base_url = f"http://{gateway_address}"
                _check_public_readiness(base_url, selected_services)
                _check_retired_contracts(command, base_url, selected_services)
                _check_local_topology(compose, gateway_address, selected_services)
                _check_project_boundary(base_url, project_id, selected_services)
                _check_gateway_request_ids(base_url, project_id, selected_services)
                if product_id != "groundwater":
                    return
                if porous is not None:
                    print(json.dumps(porous_live.gateway_probe(base_url, porous, command), ensure_ascii=False))
                    return
                if sustainability_only:
                    from tests.e2e.groundwater.sustainability_live import gateway_probe

                    token_file = Path(directory) / "sustainability-acceptance-token"
                    token_file.write_text(os.environ[TOKEN_ENVIRONMENT], encoding="ascii")
                    token_file.chmod(0o600)
                    fixture = sustainability_case or ROOT.parent / (
                        "third-party-services/groundwater-sustainability-service"
                        "/examples/sustainability_task_request.json"
                    )
                    evidence = transfer_evidence / "sustainability" if sustainability_case and transfer_evidence else None
                    result = gateway_probe(base_url, token_file, fixture, evidence)
                    if evidence is not None:
                        (evidence / "result.json").write_text(
                            json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
                        )
                    print(json.dumps(result, ensure_ascii=False))
                    return
                if reply_quality is not None:
                    run_reply_quality(
                        base_url=base_url,
                        kernel_config=config / "kernel-config.json",
                        config=reply_quality,
                    )
                    return
                sys.path.insert(0, str(ROOT / "tests/e2e/groundwater"))
                from webui import run_browser

                browser_report = (
                    webui_report or validation_cache_root(ROOT) / "webui-evidence"
                )
                if interaction_only:
                    from .interaction_gate import run_interaction_gate

                    assert browser_credentials is not None
                    run_interaction_gate(
                        base_url, browser_report, browser_credentials, command, project, project_id
                    )
                    return
                if tasks_only:
                    _verify_task_collaboration(command, base_url)
                    return
                if webui_only:
                    run_browser(base_url, ROOT, browser_report)
                    return
                if native_real_cases is not None:
                    from tests.e2e.groundwater.native_real import run_suite as run_native_cases

                    reports = run_native_cases(
                        base_url, ROOT, native_real_cases,
                        transfer_evidence or validation_cache_root(ROOT) / "native-real-evidence",
                        command,
                    )
                    assert all(report["status"] == "PASS" for report in reports), "NATIVE_REAL_CASES_FAILED"
                    return
                # The transfer fixture has optional scientific dependencies and
                # is not needed to compile the product or validate bootstrap
                # failure containment.  Load it only after those gates and only
                # for the model-lifecycle scenarios that consume it.
                if transfer_case is None:
                    fixture_modules = str(ROOT / "tests/e2e/groundwater")
                    sys.path.insert(0, fixture_modules)
                    try:
                        from transfer_fixture import prepare_fixture
                    finally:
                        sys.path.remove(fixture_modules)
                    transfer_case = prepare_fixture(Path(directory) / "transfer-case")
                if six_model_only:
                    from model_lifecycle import run_suite as run_model_lifecycles

                    model_reports = run_model_lifecycles(base_url, transfer_case)
                    if transfer_evidence is not None:
                        transfer_evidence.mkdir(parents=True, exist_ok=True)
                        (transfer_evidence / "v12-six-model-lifecycle.json").write_text(
                            json.dumps(model_reports, ensure_ascii=False, indent=2)
                            + "\n"
                        )
                    assert len(model_reports) == 6 and all(
                        report["status"] == "PASS" for report in model_reports
                    ), "SIX_MODEL_LIFECYCLE_FAILED"
                    return
                reports = run_suite(
                    base_url,
                    mode="fixed",
                    repetitions=1,
                    scenarios=FIXED_SCENARIOS,
                    audit=lambda project_id, session_id, workflow, resource, scenario: (
                        _audit_case(
                            command,
                            project_id,
                            session_id,
                            workflow,
                            resource,
                            scenario,
                        )
                    ),
                    observation_audit=lambda key, expected: _observation_assertions(
                        command, key, expected
                    ),
                )
                assert all(report["status"] == "PASS" for report in reports), (
                    "AGENT_EVALUATION_FAILED"
                )
                from model_lifecycle import run_suite as run_model_lifecycles

                model_reports = run_model_lifecycles(base_url, transfer_case)
                if transfer_evidence is not None:
                    transfer_evidence.mkdir(parents=True, exist_ok=True)
                    (transfer_evidence / "v12-six-model-lifecycle.json").write_text(
                        json.dumps(model_reports, ensure_ascii=False, indent=2) + "\n"
                    )
                assert len(model_reports) == 6 and all(
                    report["status"] == "PASS" for report in model_reports
                ), "SIX_MODEL_LIFECYCLE_FAILED"
                _verify_task_collaboration(command, base_url)
                _verify_executor_restart(
                    command,
                    next(
                        report["job_id"] for report in reports if report.get("job_id")
                    ),
                )
                _run_interaction_options(command, base_url)
                run_browser(base_url, ROOT, browser_report)
                _assert_no_secret_leaks(command, config, Path(directory))
            except (AssertionError, subprocess.CalledProcessError, httpx.HTTPError):
                _run(
                    [
                        *command,
                        "logs",
                        "--no-color",
                        "--tail",
                        "100",
                        "test-upstream",
                        "gateway",
                        "kernel-service",
                        "knowledge-bootstrap",
                        "knowledge-next-migrate",
                        "knowledge-next-service",
                        "data-migrate",
                        "data-service",
                        "executor-control",
                        "executor-native-worker",
                        "executor-qgis-worker",
                        "executor-remote_http-worker",
                        *(["porous-backend", "porous-tls"] if porous is not None else []),
                    ],
                    check=False,
                )
                raise
        finally:
            if previous_test_token is None:
                os.environ.pop(TOKEN_ENVIRONMENT, None)
            else:
                os.environ[TOKEN_ENVIRONMENT] = previous_test_token
            if transfer_evidence is not None:
                diagnostics = _run(
                    [
                        *command,
                        "exec",
                        "-T",
                        "executor-control",
                        "python",
                        "-c",
                        "import json,sqlite3; c=sqlite3.connect('/data/state/executor.sqlite3'); "
                        "c.row_factory=sqlite3.Row; print(json.dumps({"
                        "'nodes':[dict(r) for r in c.execute('SELECT job_id,node_id,status,error FROM job_nodes')],"
                        "'attempts':[dict(r) for r in c.execute('SELECT node_id,status,error_code,stderr_tail FROM node_attempts')]"
                        "},ensure_ascii=False))",
                    ],
                    check=False,
                    capture_output=True,
                )
                transfer_evidence.mkdir(parents=True, exist_ok=True)
                (transfer_evidence / "executor-diagnostics.json").write_text(
                    diagnostics.stdout
                    if diagnostics.returncode == 0
                    else diagnostics.stderr
                )
            _run(
                [*command, "down", "-v", "--remove-orphans", "--rmi", "local"],
                check=False,
            )
            _reclaim_rootless_instance_data(instance_data, compose)
