"""Network, gateway, project-boundary, and readiness assertions."""
from __future__ import annotations
import json
import re
import time
import httpx

from scripts.maintenance.project_environment import docker_executable
from scripts.lib.e2e_http import public_api_headers

from .environment import _run

def _check_gateway_request_ids(
    base_url: str, project_id: str, selected_services: set[str]
) -> None:
    cases = [
        (
            "kernel-session",
            "/api/v12/sessions/00000000-0000-0000-0000-000000000000",
            {404},
        ),
    ]
    if "data" in selected_services:
        cases.append(
            (
                "data",
                "/api/v12/data/versions/00000000-0000-0000-0000-000000000000",
                {404},
            )
        )
    evidence = []
    with httpx.Client(
        base_url=base_url,
        timeout=15,
        trust_env=False,
        headers=public_api_headers(),
    ) as client:
        for label, path, expected in cases:
            response = client.get(path)
            values = response.headers.get_list("X-Request-ID")
            body_request_id = response.json().get("requestId")
            row = {
                "route": label,
                "status": response.status_code,
                "headerCount": len(values),
                "headerLengths": [len(value) for value in values],
                "headerClasses": [
                    "hex"
                    if re.fullmatch(r"[0-9a-f]+", value)
                    else "comma-list"
                    if "," in value
                    else "other"
                    for value in values
                ],
                "bodyMatchesHeader": (
                    len(values) == 1 and body_request_id == values[0]
                ),
            }
            evidence.append(row)
            if (
                response.status_code not in expected
                or len(values) != 1
                or re.fullmatch(r"[0-9a-f]{32}", values[0]) is None
                or body_request_id != values[0]
            ):
                raise AssertionError("GATEWAY_REQUEST_ID_INVALID:" + json.dumps(row))
    print("Gateway request IDs PASS " + json.dumps(evidence), flush=True)


def _check_local_topology(
    compose: dict, gateway_address: str, selected_services: set[str]
) -> None:
    assert gateway_address.startswith("127.0.0.1:"), gateway_address
    published_ports = {}
    for name, service in compose["services"].items():
        if name == "gateway":
            ports = service.get("ports", [])
            assert len(ports) == 1, (name, ports)
            match = re.fullmatch(r"127\.0\.0\.1:([1-9][0-9]*):8080", ports[0])
            assert match is not None and int(match.group(1)) <= 65535, (name, ports)
            published_ports[name] = int(match.group(1))
        else:
            assert "ports" not in service, name
    assert len(set(published_ports.values())) == 1, published_ports
    assert gateway_address == f"127.0.0.1:{published_ports['gateway']}"
    assert "auth-service" not in compose["services"]
    forbidden = {"resource-service", "knowledge-service", "qdrant"}
    assert forbidden.isdisjoint(compose["services"]), compose["services"].keys()
    required = {"kernel-service"} | {
        f"{name}-service"
        for name in selected_services
        if name in {"data", "knowledge-next"}
    }
    if "executor" in selected_services:
        required.add("executor-control")
    assert required <= set(compose["services"]), compose["services"].keys()
    print("local Gateway topology PASS", flush=True)


def _check_project_boundary(
    base_url: str, project_id: str, selected_services: set[str]
) -> None:
    def code(response):
        body = response.json()
        return body.get("code", body.get("error", {}).get("code"))

    other = project_id + "-other"
    with httpx.Client(
        base_url=base_url,
        timeout=15,
        trust_env=False,
        headers=public_api_headers(),
    ) as client:
        assert client.get("/api/v12/capabilities").status_code == 200
        mismatch = client.post("/api/v12/sessions", json={"projectId": other})
        # A caller outside the requested project must not learn whether that
        # project exists. This is distinct from a member lacking an operation
        # permission, which remains a debuggable 403.
        assert mismatch.status_code == 404, (mismatch.status_code, mismatch.text)
        assert code(mismatch) == "PROJECT_NOT_FOUND"
        valid = client.post("/api/v12/sessions", json={"projectId": project_id})
        valid.raise_for_status()
        session_id = valid.json()["sessionId"]
        mismatch = client.get(
            f"/api/v12/sessions/{session_id}/events",
            params={"projectId": other},
        )
        assert mismatch.status_code == 404, (mismatch.status_code, mismatch.text)
        assert code(mismatch) == "PROJECT_NOT_FOUND"
        if "data" in selected_services:
            # The edge must discard an untrusted project header and forward the
            # project authenticated by the identity assertion.  The request
            # therefore reaches Data in the deployment project and the random
            # version remains undiscoverable.
            mismatch = client.get(
                "/api/v12/data/versions/00000000-0000-0000-0000-000000000000",
                headers={"X-Project-ID": other, "X-Client-Type": "cli"},
            )
            assert mismatch.status_code == 404, (mismatch.status_code, mismatch.text)
            assert code(mismatch) == "VERSION_NOT_FOUND"
    print("fixed project boundary PASS", flush=True)


def _check_public_readiness(base_url: str, services: set[str]) -> dict[str, object]:
    with httpx.Client(
        base_url=base_url,
        timeout=30,
        trust_env=False,
        headers=public_api_headers(),
    ) as client:
        health = client.get("/api/v12/health")
        health.raise_for_status()
        assert health.json() == {"status": "UP"}, health.text
        deadline = time.monotonic() + 60
        while True:
            ready = client.get("/api/v12/ready")
            payload = ready.json()
            if ready.status_code == 200:
                break
            if time.monotonic() >= deadline:
                raise AssertionError(
                    "PUBLIC_READINESS_TIMEOUT:" + json.dumps(payload, sort_keys=True)
                )
            time.sleep(1)
    assert payload.get("status") == "UP", payload
    components = payload.get("components")
    assert isinstance(components, dict), payload
    required = {
        "kernel",
        *services,
        "agent-provider",
        "intent-router-provider",
        *(("embedding",) if "knowledge-next" in services else ()),
    }
    assert required <= set(components), components
    assert all(components[name].get("status") == "UP" for name in required), components
    print(
        "public health/readiness PASS " + json.dumps(payload, sort_keys=True),
        flush=True,
    )
    return payload


def _published_service_port(
    command: list[str], service: str, container_port: str
) -> str:
    """Resolve a published port even after a deliberately failed ``up --wait``.

    Compose 2.35 can return exit 1 from ``compose port`` after a one-shot
    dependency fails although an independent gateway is still running with its
    port published.  Inspecting that running service is authoritative and keeps
    the checksum-negative startup probe focused on the intended gateway
    availability assertion.
    """

    result = _run(
        [*command, "port", service, container_port],
        check=False,
        capture_output=True,
    )
    value = result.stdout.strip()
    if result.returncode == 0 and value:
        return value

    container_id = _run(
        [*command, "ps", "-q", service], capture_output=True
    ).stdout.strip()
    if not container_id:
        raise RuntimeError(f"SERVICE_NOT_RUNNING:{service}")
    inspected = json.loads(
        _run([docker_executable(), "inspect", container_id], capture_output=True).stdout
    )
    bindings = inspected[0]["NetworkSettings"]["Ports"].get(f"{container_port}/tcp")
    if not bindings:
        raise RuntimeError(f"SERVICE_PORT_NOT_PUBLISHED:{service}:{container_port}")
    binding = bindings[0]
    host = binding["HostIp"]
    if host == "0.0.0.0":
        host = "127.0.0.1"
    return f"{host}:{binding['HostPort']}"
