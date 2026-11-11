"""Product behavior and recovery assertions for Compose acceptance."""
from __future__ import annotations
import json
from pathlib import Path
import subprocess
import time
import httpx

from scripts.maintenance.project_environment import docker_executable
from scripts.lib.e2e_http import public_api_headers

from .environment import ROOT, _exec_python, _run, _run_bootstrap_python

def _verify_task_collaboration(command, base_url):
    from scripts.evaluation.evaluate_task_collaboration import (
        run_suite as run_task_suite,
    )

    task_report = run_task_suite(
        base_url,
        mode="fixed",
        restart=lambda: _run(
            [
                *command,
                "up",
                "-d",
                "--no-deps",
                "--no-build",
                "--force-recreate",
                "--wait",
                "--wait-timeout",
                "120",
                "kernel-service",
            ]
        ),
    )
    print(
        "Task collaboration " + json.dumps(task_report, ensure_ascii=False), flush=True
    )
    assert task_report["status"] == "PASS", "TASK_ACCEPTANCE_FAILED"


def _verify_bootstrap_failure_isolated(
    command: list[str], compose: dict, config: Path
) -> None:
    lock = json.loads((config / "assembly-lock.json").read_text())
    domain = next(
        Path(volume.removesuffix(":/domain:ro"))
        for volume in compose["services"]["knowledge-bootstrap"]["volumes"]
        if volume.endswith(":/domain:ro")
    )
    seed = domain / lock["knowledgeBootstrap"]["files"][0]
    original = seed.read_bytes()
    try:
        seed.write_bytes(original + b"\n")
        result = _run(
            [
                *command,
                "up",
                "-d",
                "--build",
                "--force-recreate",
                "--wait",
                "--wait-timeout",
                "180",
                # Start the failed one-shot beside Kernel. Its failure must be
                # visible without taking the diagnostic/control API down.
                "gateway",
                "kernel-service",
                "knowledge-bootstrap",
            ],
            check=False,
        )
        assert result.returncode != 0, "tampered bootstrap unexpectedly succeeded"
        logs = _run(
            [*command, "logs", "--no-color", "knowledge-bootstrap"], capture_output=True
        ).stdout
        if "DOMAIN_FILE_CHECKSUM_MISMATCH" not in logs:
            status = _run(
                [*command, "ps", "--all"], check=False, capture_output=True
            )
            diagnostics = _run(
                [*command, "logs", "--no-color", "--tail", "200"],
                check=False,
                capture_output=True,
            )
            raise RuntimeError(
                "BOOTSTRAP_NEGATIVE_STARTUP_FAILED_BEFORE_CHECKSUM_PROBE\n"
                "compose status:\n"
                + status.stdout
                + status.stderr
                + "\ncompose logs:\n"
                + diagnostics.stdout
                + diagnostics.stderr
            )
        running = _run(
            [*command, "ps", "--status", "running", "--services"], capture_output=True
        ).stdout.splitlines()
        assert "kernel-service" in running and "gateway" in running, running
        product_ui = _run(
            [
                *command,
                "exec",
                "-T",
                "gateway",
                "wget",
                "-qO-",
                "http://127.0.0.1:8080/product-ui.json",
            ],
            capture_output=True,
        )
        assert json.loads(product_ui.stdout)["productId"]
        health = _run(
            [
                *command,
                "exec",
                "-T",
                "gateway",
                "wget",
                "-S",
                "-O-",
                "http://127.0.0.1:8080/api/v12/health",
            ],
            check=False,
            capture_output=True,
        )
        assert health.returncode == 0, health.stderr
        print(
            "Knowledge bootstrap checksum failure stays isolated while Kernel "
            "and Gateway diagnostics remain available PASS",
            flush=True,
        )
    finally:
        seed.write_bytes(original)
    # The checksum-negative startup intentionally creates migration and service
    # containers. Start the positive candidate from new volumes so the negative
    # case cannot influence its one-shot migration state.
    _run(
        [*command, "rm", "--force", "--stop", "knowledge-bootstrap"],
        check=False,
    )
    _run([*command, "down", "--volumes", "--remove-orphans"])


def _verify_bootstrap_publications(command: list[str], config: Path) -> None:
    lock = json.loads((config / "assembly-lock.json").read_text())
    projects = lock["knowledgeBootstrap"]["projects"]
    expected_documents = lock["knowledgeBootstrap"]["documents"]
    count = len(expected_documents)
    script = f"""
import json
import httpx
result = {{}}
with httpx.Client(base_url='http://knowledge-next-service:8000', timeout=120, trust_env=False) as client:
    for project in {projects!r}:
        headers = {{
            'X-Project-ID': project,
            'X-Platform-Subject': 'platform-knowledge-bootstrap-probe',
            'X-Platform-Display-Name': 'Platform Knowledge Bootstrap Probe',
            'X-Platform-Roles': 'admin',
        }}
        items = []
        cursor = None
        while True:
            query = {{'limit': 100}}
            if cursor is not None:
                query['cursor'] = cursor
            response = client.get(
                '/knowledge/v3/documents', params=query, headers=headers
            )
            response.raise_for_status()
            page = response.json()
            items.extend(page['items'])
            cursor = page['nextCursor']
            if cursor is None:
                break
        assert len(items) == {count}, len(items)
        assert [item['documentId'] for item in items] == {expected_documents!r}
        result[project] = [
            (item['documentId'], item['currentVersion']) for item in items
        ]
print(json.dumps(result, sort_keys=True))
"""
    _run([*command, "run", "--rm", "--no-deps", "knowledge-bootstrap"])
    before = json.loads(_run_bootstrap_python(command, script))
    _run([*command, "run", "--rm", "--no-deps", "knowledge-bootstrap"])
    assert json.loads(_run_bootstrap_python(command, script)) == before
    print(
        f"Knowledge bootstrap {count} documents and idempotent redeployment PASS",
        flush=True,
    )


def _runtime_observation(command: list[str], selected_services: set[str]) -> dict:
    targets = {"kernel": "kernel-service", "executor": "executor-control"}
    if "data" in selected_services:
        targets["data"] = "data-service"
    if "knowledge-next" in selected_services:
        targets["knowledge"] = "knowledge-next-service"
    metrics = {}
    for label, service in targets.items():
        metrics[label] = _exec_python(
            command,
            service,
            """
import urllib.request
print(urllib.request.urlopen(
    'http://127.0.0.1:8000/internal/metrics', timeout=10
).read().decode())
""",
        )
    container_ids = _run(
        [*command, "ps", "-q"], capture_output=True
    ).stdout.splitlines()
    stats = []
    if container_ids:
        raw = _run(
            [
                docker_executable(),
                "stats",
                "--no-stream",
                "--format",
                "{{json .}}",
                *container_ids,
            ],
            capture_output=True,
        ).stdout
        stats = [json.loads(line) for line in raw.splitlines() if line.strip()]
    return {"metrics": metrics, "containerStats": stats}


def _run_interaction_options(command, base_url):
    from scripts.evaluation.evaluate_agent import request, submit_message

    def job_count():
        return int(
            _exec_python(
                command,
                "executor-control",
                """
import sqlite3
with sqlite3.connect('/data/state/executor.sqlite3') as connection:
    print(connection.execute('SELECT COUNT(*) FROM executor_jobs').fetchone()[0])
""",
            )
        )

    with httpx.Client(
        base_url=base_url,
        timeout=300,
        trust_env=False,
        headers=public_api_headers(),
    ) as client:
        session = request(
            client, "POST", "/api/v12/sessions", json={"projectId": "groundwater-demo"}
        )
        session_id = session["session_id"]
        baseline = job_count()
        submit_message(
            client,
            session_id,
            "请比较当前资料可用的趋势分析方法，并说明各自限制，先不要执行。",
            requested_mode="AUTO",
        )
        deadline = time.monotonic() + 300
        while time.monotonic() < deadline:
            history = request(client, "GET", f"/api/v12/sessions/{session_id}/messages")
            turn = history["turns"][-1]
            if turn["status"] == "COMPLETE":
                break
            assert turn["status"] != "FAILED", turn
            time.sleep(0.25)
        else:
            raise AssertionError("INTERACTION_OPTIONS_TIMEOUT")
        assert turn["intent_decision"] == "DIALOGUE"
        assert turn["resolved_mode"] == "DIALOGUE"
        options = turn["interaction_options"]
        assert options == []
        current = request(client, "GET", f"/api/v12/sessions/{session_id}")
        assert current["confirmation"] is None and not current["workflow_run_ids"]
        assert job_count() == baseline
        assert (
            client.get(
                f"/api/v12/sessions/{session_id}/recommendations/missing"
            ).status_code
            == 404
        )
        assert (
            client.post(
                f"/api/v12/sessions/{session_id}/recommendations/missing/select",
                json={},
            ).status_code
            == 404
        )
        assert job_count() == baseline
    print(
        "Read-only reply options PASS: no unresolved items, zero options and zero Jobs",
        flush=True,
    )


def _observation_assertions(command, key, expected):
    source = f"""
import json
import urllib.error
import urllib.request
request = urllib.request.Request(
    'http://127.0.0.1:8000/assertions',
    data=json.dumps({{"key": {key!r}, "expected": {expected!r}}}).encode(),
    headers={{'Content-Type': 'application/json'}},
)
try:
    with urllib.request.urlopen(request) as response:
        print(response.read().decode())
except urllib.error.HTTPError as error:
    raise AssertionError(error.read().decode()) from error
"""
    try:
        return json.loads(_exec_python(command, "test-upstream", source))
    except subprocess.CalledProcessError as error:
        raise AssertionError(
            f"PROVIDER_ASSERTION_FAILED:{key}:{error.stderr}"
        ) from error


def _audit_case(command, project_id, session_id, workflow, resource, scenario):
    """Inspect the current Session and its exact Job in their owning stores."""
    expected = 0 if workflow is None else 1
    kernel_count = int(
        _exec_python(
            command,
            "kernel-service",
            f"""
import sqlite3
with sqlite3.connect('/data/kernel.sqlite3') as connection:
    session = connection.execute(
        'SELECT session_id FROM sessions WHERE project_id=? AND session_id=?',
        ({project_id!r}, {session_id!r}),
    ).fetchone()
    assert session is not None
    count = connection.execute(
        'SELECT COUNT(*) FROM workflow_runs WHERE session_id=?', session,
    ).fetchone()[0]
    assert count == {expected}, count
    print(count)
""",
        ).strip()
    )
    if workflow is None:
        if scenario == "idempotency":
            checkpoint = _exec_python(
                command,
                "kernel-service",
                f"""
import json, sqlite3
with sqlite3.connect('/data/kernel.sqlite3') as connection:
    rows = connection.execute(
        'SELECT state_json FROM agent_run_state JOIN agent_runs USING(agent_run_id) '
        'WHERE agent_runs.project_id=? AND agent_runs.session_id=?',
        ({project_id!r}, {session_id!r}),
    ).fetchall()
    print(json.dumps([row[0] for row in rows]))
""",
            )
            assert json.loads(checkpoint), "WAITING_CONFIRMATION_RUNSTATE_MISSING"
        return {"workflow_runs": kernel_count, "jobs": 0, "results": 0}

    run_id = workflow["run_id"]
    executor = json.loads(
        _exec_python(
            command,
            "executor-control",
            f"""
import json, sqlite3
with sqlite3.connect('/data/state/executor.sqlite3') as connection:
    connection.row_factory = sqlite3.Row
    jobs = connection.execute(
        'SELECT * FROM executor_jobs WHERE request_id=?', ({run_id!r},),
    ).fetchall()
    assert len(jobs) == 1
    job = jobs[0]
    assert job['status'] == 'SUCCEEDED'
    assert json.loads(job['request_json'])['project_id'] == {project_id!r}
    nodes = connection.execute(
        'SELECT * FROM job_nodes WHERE job_id=?', (job['job_id'],),
    ).fetchall()
    attempts = connection.execute(
        'SELECT * FROM node_attempts WHERE job_id=?', (job['job_id'],),
    ).fetchall()
    assert len(nodes) == len(attempts) == 1
    assert nodes[0]['status'] == attempts[0]['status'] == 'SUCCEEDED'
    runtime = connection.execute(
        'SELECT runtime FROM worker_heartbeats WHERE worker_id=?',
        (attempts[0]['worker_id'],),
    ).fetchone()[0]
    assert runtime == 'native'
    assert connection.execute(
        'SELECT COUNT(*) FROM job_object_pins WHERE job_id=?', (job['job_id'],),
    ).fetchone()[0] == 0
    print(json.dumps({{'jobs': 1, 'worker_execution': runtime,
        'job_request': job['request_json'], 'job_id': job['job_id'],
        'request_id': job['request_id'], 'node_count': len(nodes),
        'attempt_count': len(attempts)}}))
""",
        )
    )
    if scenario == "idempotency":
        job_request = json.loads(executor["job_request"])
        replay = json.loads(
            _exec_python(
                command,
                "kernel-service",
                f"""
import json, httpx
from platform_protocol_contracts import JobRequestV6
body = JobRequestV6.from_storage_dict(json.loads({json.dumps(job_request)!r})).as_dict()
with httpx.Client(base_url='http://executor-control:8000', timeout=30) as client:
    response = client.post('/internal/execution/v6/jobs', json=body)
    response.raise_for_status()
    print(json.dumps(response.json()))
""",
            )
        )
        assert replay["jobId"] == executor["job_id"]
        _exec_python(
            command,
            "executor-control",
            f"""
import sqlite3
with sqlite3.connect('/data/state/executor.sqlite3') as connection:
    assert connection.execute(
        'SELECT COUNT(*) FROM executor_jobs WHERE request_id=?',
        ({executor["request_id"]!r},),
    ).fetchone()[0] == 1
    assert connection.execute(
        'SELECT COUNT(*) FROM node_attempts WHERE job_id=?',
        ({executor["job_id"]!r},),
    ).fetchone()[0] == {executor["attempt_count"]}
""",
        )
        executor["idempotent_replay"] = True
    output_refs = workflow["outputs"]
    result_count = len(output_refs)
    assert result_count > 0
    authorization = public_api_headers().get("Authorization")
    if not authorization:
        raise AssertionError("E2E_API_TOKEN_MISSING_FOR_LINEAGE_AUDIT")
    lineage = json.loads(
        _exec_python(
            command,
            "kernel-service",
            f"""
import json, httpx, sys
refs = json.loads({json.dumps(output_refs)!r})
authorization = sys.stdin.read().strip()
assert authorization.startswith('Bearer ')
rows = []
with httpx.Client(base_url='http://gateway:8080', timeout=30) as client:
    for ref in refs:
        response = client.get('/api/v12/data/versions/' + ref['version_id'],
            headers={{'Authorization': authorization,
                'X-Project-ID': {project_id!r}, 'X-Client-Type': 'cli'}})
        response.raise_for_status()
        value = response.json()
        assert value['versionId'] == ref['version_id']
        assert value['inputVersions'] == [{resource["version_id"]!r}]
        rows.append({{'version_id': value['versionId'], 'inputs': value['inputVersions']}})
print(json.dumps(rows))
""",
            input_text=authorization,
        )
    )
    assert len(lineage) == result_count
    for key in ("job_request", "job_id", "request_id", "node_count", "attempt_count"):
        executor.pop(key)
    return {
        **executor,
        "workflow_runs": kernel_count,
        "data_versions": result_count,
        "lineage": lineage,
    }


def _verify_executor_restart(command: list[str], job_id: str) -> None:
    job_id, lease_id = (
        _exec_python(
            command,
            "executor-control",
            f"""
import sqlite3
with sqlite3.connect('/data/state/executor.sqlite3') as connection:
    row = connection.execute(
        "SELECT j.job_id, a.lease_id FROM executor_jobs j "
        "JOIN node_attempts a ON a.job_id=j.job_id "
        "WHERE j.job_id=? AND j.status='SUCCEEDED' AND a.status='SUCCEEDED'",
        ({job_id!r},),
    ).fetchone()
    assert row is not None
    print(row[0]); print(row[1])
""",
        )
        .strip()
        .splitlines()
    )
    before = json.loads(
        _exec_python(
            command,
            "kernel-service",
            f"""
import httpx, json
path='/internal/execution/v6/jobs/{job_id}'
with httpx.Client(base_url='http://executor-control:8000',timeout=30) as client:
 response=client.get(path)
 response.raise_for_status()
 body=response.json()
print(json.dumps({{'status':body['status'],'outputs':body['outputs']}},sort_keys=True,separators=(',',':')))
""",
        )
        .strip()
        .splitlines()[-1]
    )
    if (
        before.get("status") != "SUCCEEDED"
        or not isinstance(before.get("outputs"), list)
        or not before["outputs"]
    ):
        raise RuntimeError(f"EXECUTOR_RESTART_PRECONDITION_FAILED:{before}")
    _run(
        [
            *command,
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
    )
    output = _exec_python(
        command,
        "kernel-service",
        f"""
import httpx, json
path='/internal/execution/v6/jobs/{job_id}'
with httpx.Client(base_url='http://executor-control:8000',timeout=30) as client:
 response=client.get(path)
 response.raise_for_status()
 body=response.json()
 stale=client.post('/internal/workers/v3/leases/{lease_id}:heartbeat',json={{'leaseToken':'invalid'}})
print(json.dumps({{
 'job':{{'status':body['status'],'outputs':body['outputs']}},
 'stale':{{'status':stale.status_code,'code':stale.json()['code']}},
}},sort_keys=True,separators=(',',':')))
""",
    )
    after = json.loads(output.strip().splitlines()[-1])
    expected = {
        "job": before,
        "stale": {"status": 409, "code": "LEASE_STALE"},
    }
    if after != expected:
        raise RuntimeError(f"EXECUTOR_RESTART_CHECK_FAILED:{output}")


def _check_retired_contracts(
    command: list[str], base_url: str, selected_services: set[str]
) -> None:
    retired = json.loads(
        (ROOT / "tests/contracts/fixtures/retired_paths.json").read_text(
            encoding="utf-8"
        )
    )
    with httpx.Client(
        base_url=base_url,
        timeout=30,
        trust_env=False,
        headers=public_api_headers(),
    ) as client:
        public_statuses = [client.get(path).status_code for path in retired["gateway"]]
    if public_statuses != [404] * len(retired["gateway"]):
        raise AssertionError(f"RETIRED_PUBLIC_CONTRACT_ACTIVE:{public_statuses}")

    all_probes = {
        "kernel-service": retired["kernel"],
        "data-service": retired["data"],
        "knowledge-next-service": retired["knowledge"],
        "executor-control": retired["executor"],
    }
    service_containers = {
        "data": "data-service",
        "knowledge-next": "knowledge-next-service",
        "executor": "executor-control",
    }
    probes = {"kernel-service": all_probes["kernel-service"]}
    probes.update(
        {
            container: all_probes[container]
            for service, container in service_containers.items()
            if service in selected_services
        }
    )
    for service, paths in probes.items():
        statuses = _exec_python(
            command,
            service,
            "import httpx; "
            f"paths={paths!r}; "
            "print(','.join(str(httpx.get('http://127.0.0.1:8000'+path,timeout=10).status_code) for path in paths))",
        ).strip()
        if statuses != ",".join("404" for _ in paths):
            raise AssertionError(
                f"RETIRED_INTERNAL_CONTRACT_ACTIVE:{service}:{statuses}"
            )
