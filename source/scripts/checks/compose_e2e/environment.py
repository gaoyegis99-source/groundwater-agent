"""Validated inputs, isolated subprocesses, and generated fixture helpers."""
from __future__ import annotations
import argparse
import ipaddress
import os
import secrets
import shutil
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit
from scripts.maintenance.project_environment import paths as project_paths
ROOT = Path(__file__).resolve().parents[3]
for value in (
    str(ROOT), str(ROOT / "packages/workflow-compiler/src"),
    str(ROOT / "packages/operator-pack/src"), str(ROOT / "platform/assembler"),
):
    if value not in sys.path:
        sys.path.insert(0, value)
from scripts.release.create_deployment_secrets import create as create_deployment_secrets  # noqa: E402

def _http_base_url(value: str) -> str:
    parsed = urlsplit(value)
    if (
        parsed.scheme not in {"http", "https"}
        or parsed.hostname is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise argparse.ArgumentTypeError(
            "expected an HTTP(S) base URL without credentials, query or fragment"
        )
    return value.rstrip("/")


def _private_ipv4_subnet(value: str) -> str:
    try:
        network = ipaddress.ip_network(value, strict=True)
    except ValueError as error:
        raise argparse.ArgumentTypeError(str(error)) from error
    if network.version != 4 or not network.is_private or network.prefixlen > 28:
        raise argparse.ArgumentTypeError(
            "expected a private IPv4 network with at least 16 usable addresses"
        )
    return str(network)


def _compose_secrets(
    directory: Path,
    compose: dict,
    *,
    provider_key: str | None = None,
    remote_services: dict[str, dict] | None = None,
) -> Path:
    agent_key = directory / "agent-provider-key"
    key = provider_key or "p0-1-agent-sentinel-" + secrets.token_urlsafe(32)
    if not key.strip() or "\n" in key or "\r" in key:
        raise ValueError("COMPOSE_PROVIDER_KEY_INVALID")
    agent_key.write_text(key + "\n", encoding="ascii")
    agent_key.chmod(0o600)
    secret_dir = directory / "secrets"
    create_deployment_secrets(secret_dir, agent_key, agent_key)
    remote_environment = _remote_http_fixture_environment(
        compose, secret_dir, remote_services or {}
    )
    instance_data = directory / "instance-data"
    instance_data.mkdir()
    prefix = "${PLATFORM_INSTANCE_DATA_ROOT:?required}/"
    for service in compose["services"].values():
        for volume in service.get("volumes", []):
            source = volume.get("source") if isinstance(volume, dict) else volume.partition(":/")[0]
            if isinstance(source, str) and source.startswith(prefix):
                relative = Path(source.removeprefix(prefix))
                if relative.is_absolute() or ".." in relative.parts:
                    raise ValueError("COMPOSE_INSTANCE_VOLUME_INVALID")
                target = instance_data / relative
                target.mkdir(parents=True, exist_ok=True)
                # The acceptance daemon is rootless, so an image UID such as
                # 10001 is mapped to a subordinate host UID.  These paths are
                # isolated below a 0700 temporary parent; make only the bind
                # roots writable so services can initialize them just as
                # ``gw instance prepare-storage`` does for a real instance.
                target.chmod(0o777)
    environment_file = directory / "compose.env"
    environment_file.write_text(
        (
            f"PLATFORM_SECRETS_DIR={secret_dir}\n"
            f"PLATFORM_INSTANCE_DATA_ROOT={instance_data}\n"
            f"PLATFORM_DEPENDENCY_ROOT={project_paths()['PLATFORM_DEPENDENCY_ROOT']}\n"
            + "".join(
                f"{name}={value}\n"
                for name, value in remote_environment.items()
            )
        ),
        encoding="utf-8",
    )
    return environment_file


def _remote_http_fixture_environment(
    compose: dict, secret_dir: Path, remote_services: dict[str, dict],
) -> dict[str, str]:
    """Provide inert deployment bindings for an assembled remote worker.

    Compose must resolve required endpoint variables and mount every declared
    secret even when an acceptance scenario never schedules a remote node. The
    reserved ``.invalid`` endpoint keeps any accidental execution fail-closed.
    """
    worker = compose.get("services", {}).get("executor-remote_http-worker")
    if worker is None:
        return {}
    expected_secrets = {
        binding[key]
        for binding in remote_services.values()
        for key in ("credentialSecret", "caBundleSecret")
    }
    if not remote_services or expected_secrets != set(worker.get("secrets", [])):
        raise ValueError("COMPOSE_REMOTE_HTTP_SECRETS_INVALID")
    values = {}
    for service_id, binding in remote_services.items():
        host = f"{service_id}.invalid"
        values[binding["baseUrlEnvironment"]] = f"https://{host}"
        values[binding["allowedHostEnvironment"]] = host
        for name, value in (
            (binding["credentialSecret"], secrets.token_urlsafe(48)),
            (binding["caBundleSecret"], "unused-e2e-ca-bundle"),
        ):
            path = secret_dir / name
            path.write_text(value, encoding="ascii")
            path.chmod(0o444)
    return values


def _test_upstream_build(directory: Path) -> Path:
    context = directory / "test-upstream"
    context.mkdir()
    for name in ("Dockerfile", "stub_server.py"):
        shutil.copy2(ROOT / "tests/e2e/compose-stub" / name, context / name)
    return context


def _configure_embedding_provider(
    compose: dict, embedding_base_url: str | None
) -> None:
    selected_url = embedding_base_url or "http://test-upstream:8000"
    configured_services = []
    for service_name, service in compose["services"].items():
        if service_name.startswith(
            "knowledge-next"
        ) and "PLATFORM_EMBEDDING_BASE_URL" in service.get("environment", {}):
            configured_services.append(service_name)
            service["environment"]["PLATFORM_EMBEDDING_BASE_URL"] = selected_url
            if embedding_base_url is None:
                service.setdefault("depends_on", {})["test-upstream"] = {
                    "condition": "service_healthy"
                }
    if not configured_services:
        raise AssertionError("KNOWLEDGE_EMBEDDING_CONFIGURATION_MISSING")


def _exec_python(
    command: list[str],
    service: str,
    source: str,
    *,
    input_text: str | None = None,
) -> str:
    result = _run(
        [*command, "exec", "-T", service, "python", "-c", source],
        capture_output=True,
        input_text=input_text,
    )
    return result.stdout


def _bootstrap_e2e_api_token(
    command: list[str], project_id: str, *, browser_credentials: Path | None = None,
) -> str:
    """Create a short-lived admin identity inside the isolated Kernel store."""

    password = "e2e-" + secrets.token_urlsafe(24)
    token = _exec_python(
        command,
        "kernel-service",
        f"""
from pathlib import Path
import sys
from platform_kernel.access.research_auth import bootstrap_admin, create_token
database = Path('/data/kernel.sqlite3')
user_id = bootstrap_admin(
    database,
    {project_id!r},
    'acceptance-admin',
    'Acceptance Admin',
    sys.stdin.read(),
)
_metadata, token = create_token(database, user_id, 'compose-e2e', expires_days=1)
print(token)
""",
        input_text=password,
    ).strip()
    if not token.startswith("gwr_"):
        raise RuntimeError("E2E_API_TOKEN_INVALID")
    if browser_credentials is not None:
        with os.fdopen(
            os.open(browser_credentials, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600),
            "w",
        ) as stream:
            stream.write(f"username=acceptance-admin\npassword={password}\n")
    return token


def _run_bootstrap_python(command: list[str], source: str) -> str:
    result = _run(
        [
            *command,
            "run",
            "--rm",
            "--no-deps",
            "-T",
            "--entrypoint",
            "python",
            "knowledge-bootstrap",
            "-c",
            source,
        ],
        check=False,
        capture_output=True,
    )
    if result.returncode != 0:
        raise AssertionError(
            "KNOWLEDGE_BOOTSTRAP_PROBE_FAILED:\n" + result.stdout + "\n" + result.stderr
        )
    return result.stdout


def _run(
    command: list[str],
    *,
    check: bool = True,
    capture_output: bool = False,
    env: dict[str, str] | None = None,
    input_text: str | None = None,
) -> subprocess.CompletedProcess[str]:
    environment = dict(os.environ if env is None else env)
    return subprocess.run(
        command,
        check=check,
        text=True,
        capture_output=capture_output,
        env=environment,
        input=input_text,
    )
