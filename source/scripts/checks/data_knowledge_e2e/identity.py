"""Run the real Kernel identity endpoint for the isolated unified Gateway."""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import httpx

from scripts.maintenance.project_environment import docker_executable

from .context import Recorder
from .environment import _docker_port, _wait_http_ready


def _bootstrap_identity_token(
    recorder: Recorder, container: str, project_id: str
) -> tuple[str, str]:
    """Create a real local admin and return its short-lived test token.

    The password and token are generated inside the Kernel container.  Neither
    value is placed in the recorded command or persisted in the gate report.
    """

    source = """
import json
import os
from pathlib import Path
import secrets
from platform_kernel.access.research_auth import bootstrap_admin, create_token

database = Path('/data/kernel.sqlite3')
subject = bootstrap_admin(
    database,
    os.environ['PLATFORM_E2E_PROJECT_ID'],
    'acceptance-admin',
    'Acceptance Admin',
    'e2e-' + secrets.token_urlsafe(24),
)
_metadata, token = create_token(
    database, subject, 'data-knowledge-e2e', expires_days=1
)
print(json.dumps({'subject': subject, 'token': token}))
"""
    created = recorder.run(
        [
            docker_executable(),
            "exec",
            "-e",
            f"PLATFORM_E2E_PROJECT_ID={project_id}",
            container,
            "python",
            "-c",
            source,
        ],
        capture=True,
    )
    try:
        payload = json.loads(created.stdout)
    except (TypeError, json.JSONDecodeError) as error:
        raise RuntimeError("LOCAL_IDENTITY_BOOTSTRAP_INVALID") from error
    subject = payload.get("subject")
    token = payload.get("token")
    if not isinstance(subject, str) or not subject or not isinstance(token, str):
        raise RuntimeError("LOCAL_IDENTITY_BOOTSTRAP_INVALID")
    if not token.startswith("gwr_"):
        raise RuntimeError("LOCAL_IDENTITY_TOKEN_INVALID")
    return subject, token


def _start_identity_kernel(
    recorder: Recorder,
    work: Path,
    *,
    image: str,
    container: str,
    network: str,
    project_id: str,
) -> tuple[dict[str, object], Path, str]:
    compiled = recorder.run(
        [
            sys.executable,
            "scripts/development/dev.py",
            "compile",
            "--product",
            "groundwater",
        ],
        capture=True,
    )
    bundle = Path(compiled.stdout.strip().splitlines()[-1])
    config = work / "kernel-config.json"
    shutil.copyfile(bundle / "kernel-config.json", config)
    data = work / "kernel-data"
    data.mkdir()
    data.chmod(0o777)  # Writable by the image UID through the rootless daemon.
    key = work / "synthetic-model-key"
    key.write_text("joint-gate-synthetic-key\n", encoding="utf-8")
    key.chmod(0o444)
    environment = {
        "KERNEL_CONFIG_PATH": "/config/kernel-config.json",
        "KERNEL_DATA_ROOT": "/data",
        "PLATFORM_PROJECT_ID": project_id,
        "PLATFORM_IDENTITY_MODE": "local",
        "PLATFORM_LOCAL_IDENTITY_SUBJECT": "local-researcher",
        "PLATFORM_LOCAL_IDENTITY_DISPLAY_NAME": "Local Research Admin",
        "PLATFORM_LOCAL_IDENTITY_ROLES": "researcher,admin",
        "PLATFORM_AGENT_PREVIEW_ENABLED": "false",
    }
    # These endpoints satisfy Kernel configuration without using external
    # providers. This gate exercises identity, not Agent execution/readiness.
    for prefix in ("PLATFORM_AGENT", "PLATFORM_INTENT_ROUTER"):
        environment.update(
            {
                f"{prefix}_PROVIDER": "deepseek",
                f"{prefix}_PROTOCOL": "responses",
                f"{prefix}_BASE_URL": "http://embedding-stub:8000/v1",
                f"{prefix}_MODEL": "joint-test-model",
                f"{prefix}_CONTEXT_WINDOW_TOKENS": "131072",
                f"{prefix}_MAX_OUTPUT_TOKENS": "4096",
                f"{prefix}_REQUEST_TIMEOUT_SECONDS": "120",
                f"{prefix}_SEGMENT_TIMEOUT_SECONDS": "300",
                f"{prefix}_API_KEY_FILE": "/config/model-key",
            }
        )
    recorder.run(
        [
            docker_executable(),
            "run",
            "-d",
            "--name",
            container,
            "--network",
            network,
            "--network-alias",
            "kernel-service",
            "-p",
            "127.0.0.1::8000",
            "--health-cmd",
            'python -c "import urllib.request; '
            "urllib.request.urlopen('http://127.0.0.1:8000/internal/health/live', timeout=3)\"",
            "-v",
            f"{config}:/config/kernel-config.json:ro",
            "-v",
            f"{key}:/config/model-key:ro",
            "-v",
            f"{data}:/data",
            *(
                argument
                for name, value in environment.items()
                for argument in ("-e", f"{name}={value}")
            ),
            image,
        ]
    )
    url = f"http://127.0.0.1:{_docker_port(recorder, container, '8000/tcp')}"
    with httpx.Client(base_url=url, timeout=5, trust_env=False) as kernel:
        _wait_http_ready(kernel, "/internal/health/live")
        subject, token = _bootstrap_identity_token(recorder, container, project_id)
        identity = kernel.get(
            "/internal/identity/verify",
            headers={
                "Authorization": f"Bearer {token}",
                "X-Platform-Required-Role": "admin",
            },
        )
        if (
            identity.status_code != 204
            or identity.headers.get("x-project-id") != project_id
            or identity.headers.get("x-platform-subject") != subject
            or "admin"
            not in identity.headers.get("x-platform-roles", "").split(",")
        ):
            raise RuntimeError(
                f"OPERATOR_IDENTITY_INVALID:status={identity.status_code}"
            )
    return (
        {
            "status": "PASS",
            "mode": "local",
            "url": url,
            "projectId": project_id,
            "subject": identity.headers["x-platform-subject"],
            "roles": identity.headers["x-platform-roles"].split(","),
            "httpStatus": identity.status_code,
            "scope": (
                "Kernel identity endpoint; Agent and Executor readiness not assessed"
            ),
        },
        bundle,
        token,
    )


def _check_gateway_pages(
    gateway: httpx.Client, *, api_token: str | None = None
) -> dict[str, object]:
    _wait_http_ready(gateway, "/api/v12/health")
    headers = {"Authorization": f"Bearer {api_token}"} if api_token else {}
    root = gateway.get("/", follow_redirects=False, headers=headers)
    if root.status_code != 200 or not root.headers.get(
        "content-type", ""
    ).startswith("text/html"):
        raise RuntimeError(
            f"GATEWAY_ROOT_INVALID:status={root.status_code}"
        )
    pages = {}
    for path in ("/data/", "/knowledge/"):
        response = gateway.get(path, follow_redirects=False, headers=headers)
        if response.status_code != 200 or not response.headers.get(
            "content-type", ""
        ).startswith("text/html"):
            raise RuntimeError(
                f"GATEWAY_PAGE_INVALID:{path}:status={response.status_code}"
            )
        pages[path] = {"status": "PASS", "httpStatus": response.status_code}
    return {
        "gatewayRoot": {
            "status": "PASS",
            "httpStatus": root.status_code,
        },
        "gatewayPages": {"status": "PASS", "pages": pages},
    }
