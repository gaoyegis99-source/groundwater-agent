import hashlib
import io
import json
import os
import subprocess
import sys

# Pin direct checks to the owning project's Docker launcher.
import sys as _maintenance_sys  # noqa: E402
import sysconfig
import threading
import time
from dataclasses import replace
from pathlib import Path
from pathlib import Path as _MaintenancePath  # noqa: E402

import psycopg
import pytest
from fastapi.testclient import TestClient
from platform_data_client import DataClient
from platform_data_service.adapters.database import Database, identity
from platform_data_service.adapters.locking import runtime_lock
from platform_data_service.adapters.storage import LocalStore
from platform_data_service.api.app import create_app
from platform_data_service.api.routers import publication as publication_router
from platform_data_service.operations import worker as worker_module
from platform_data_service.operations.worker import Worker

from .test_service import publish, query, ready, start, upload, workbook_bytes

_maintenance_source = next(
    parent
    for parent in _MaintenancePath(__file__).resolve().parents
    if (parent / "scripts/maintenance/project_environment.py").is_file()
)
_maintenance_sys.path.insert(0, str(_maintenance_source))
from scripts.maintenance.project_environment import docker_executable  # noqa: E402


def test_kernel_verified_gateway_identity_and_roles(settings):
    gateway = replace(settings, identity_mode="gateway")
    user = {
        "X-Project-ID": settings.project,
        "X-Platform-Subject": "user@example.test",
        "X-Platform-Display-Name": "Project User",
        "X-Platform-Roles": "researcher",
    }
    auditor = {
        **user,
        "X-Platform-Subject": "auditor@example.test",
        "X-Platform-Display-Name": "Project Viewer",
        "X-Platform-Roles": "viewer",
    }
    operator = {
        **user,
        "X-Platform-Subject": "operator@example.test",
        "X-Platform-Display-Name": "Project Admin",
        "X-Platform-Roles": "admin",
    }
    with TestClient(create_app(gateway)) as client:
        assert client.get("/data/v3/capabilities").status_code == 401
        assert (
            client.get(
                "/data/v3/capabilities", headers={"X-Project-ID": "wrong"}
            ).status_code
            == 401
        )
        assert (
            client.get(
                "/data/v3/capabilities",
                headers={
                    key: value
                    for key, value in user.items()
                    if key != "X-Platform-Display-Name"
                },
            ).status_code
            == 401
        )
        assert (
            client.get(
                "/data/v3/capabilities",
                headers={
                    **user,
                    "X-Platform-Display-Name": ("名" * 81).encode("utf-8"),
                },
            ).status_code
            == 401
        )
        capabilities = client.get("/data/v3/capabilities", headers=user)
        assert capabilities.status_code == 200
        body = capabilities.json()
        assert body["identityMode"] == "gateway"
        assert body["productId"] == settings.product
        assert body["currentUser"] == {
            "subject": "user@example.test",
            "displayName": "Project User",
            "roles": ["researcher"],
        }
        assert body["permissions"] == {
            "catalogRead": True,
            "queryCreate": True,
            "previewCreate": True,
            "objectDownload": True,
            "userUploadCreate": True,
            "managedPublicationWrite": False,
            "semanticWrite": False,
            "accessWrite": True,
            "governanceRead": False,
            "qualityWrite": False,
            "mlopsWrite": False,
        }
        operator_body = client.get("/data/v3/capabilities", headers=operator).json()
        assert operator_body["permissions"]["managedPublicationWrite"] is True
        assert operator_body["permissions"]["semanticWrite"] is True
        assert operator_body["permissions"]["accessWrite"] is True
        assert operator_body["permissions"]["governanceRead"] is True
        assert operator_body["permissions"]["qualityWrite"] is True
        assert operator_body["permissions"]["mlopsWrite"] is True
        assert client.get("/data/v3/activity", headers=user).status_code == 403
        assert client.get("/data/v3/activity", headers=operator).status_code == 200
        assert (
            client.get(
                "/data/v3/datasets/missing/governance", headers=user
            ).status_code
            == 403
        )
        assert (
            client.get(
                "/data/v3/datasets/missing/governance", headers=operator
            ).status_code
            == 404
        )
        auditor_capabilities = client.get(
            "/data/v3/capabilities", headers=auditor
        )
        assert auditor_capabilities.status_code == 200
        assert auditor_capabilities.json()["permissions"] == {
            "catalogRead": True,
            "queryCreate": False,
            "previewCreate": False,
            "objectDownload": True,
            "userUploadCreate": False,
            "managedPublicationWrite": False,
            "semanticWrite": False,
            "accessWrite": False,
            "governanceRead": False,
            "qualityWrite": False,
            "mlopsWrite": False,
        }
        assert (
            client.get(
                "/data/v3/capabilities",
                headers={**user, "X-Project-ID": "wrong"},
            ).status_code
            == 403
        )
        forbidden = client.post(
            "/data/v3/publications",
            headers=auditor,
            json={"idempotencyKey": identity(), "outputs": []},
        )
        assert forbidden.status_code == 403
        assert forbidden.json()["code"] == "ROLE_FORBIDDEN"


def test_user_upload_is_restricted_and_mutable_work_is_subject_owned(
    client, worker, settings
):
    shared = publish(client, worker)
    workbook = publish(
        client,
        worker,
        [{"name": "table", "title": "工作簿", "format": "xlsx"}],
        workbook_bytes(),
    )
    detail = client.get(f"/data/v3/versions/{workbook['versionId']}").json()
    raw = next(item for item in detail["objects"] if item["role"] == "raw")
    trusted = replace(settings, identity_mode="gateway")
    user_a = {
        "X-Project-ID": settings.project,
        "X-Platform-Subject": "alice@example.test",
        "X-Platform-Display-Name": "Alice",
        "X-Platform-Roles": "researcher",
        "X-Request-ID": "alice-request",
    }
    user_b = {
        **user_a,
        "X-Platform-Subject": "bob@example.test",
        "X-Platform-Display-Name": "Bob",
    }
    upload_body = {
        "idempotencyKey": "same-browser-upload",
        "outputs": [
            {
                "name": "file",
                "title": "upload.csv",
                "format": "csv",
                "policy": "FILE_QUERYABLE",
                "semantics": {"description": "project upload", "source": "web_upload"},
            }
        ],
        "inputVersions": [],
        "provenance": {"client": "platform-web-v12"},
    }
    with TestClient(create_app(trusted)) as trusted_client:
        first = trusted_client.post(
            "/data/v3/publications", headers=user_a, json=upload_body
        )
        second = trusted_client.post(
            "/data/v3/publications", headers=user_b, json=upload_body
        )
        assert first.status_code == second.status_code == 201
        first_id = first.json()["publicationId"]
        second_id = second.json()["publicationId"]
        assert first_id != second_id
        assert (
            trusted_client.get(
                f"/data/v3/publications/{first_id}", headers=user_b
            ).status_code
            == 404
        )
        assert (
            trusted_client.post(
                f"/data/v3/publications/{first_id}/abort", headers=user_b
            ).status_code
            == 404
        )
        content = b"well,value\n001,2\n"
        checksum = "sha256:" + hashlib.sha256(content).hexdigest()
        assert (
            trusted_client.put(
                f"/data/v3/publications/{first_id}/items/file",
                headers={**user_b, "X-Content-SHA256": checksum},
                content=content,
            ).status_code
            == 404
        )
        assert (
            trusted_client.post(
                f"/data/v3/publications/{first_id}/prepare", headers=user_b
            ).status_code
            == 404
        )
        assert (
            trusted_client.post(
                f"/data/v3/publications/{first_id}/commit", headers=user_b
            ).status_code
            == 404
        )

        forbidden_update = {
            **upload_body,
            "idempotencyKey": "forbidden-update",
            "outputs": [
                {
                    **upload_body["outputs"][0],
                    "datasetId": shared["datasetId"],
                    "expectedVersion": shared["versionId"],
                }
            ],
        }
        response = trusted_client.post(
            "/data/v3/publications", headers=user_a, json=forbidden_update
        )
        assert response.status_code == 403
        assert response.json()["code"] == "ROLE_FORBIDDEN"

        query_body = {
            "idempotencyKey": "same-query-key",
            "versionIds": [shared["versionId"]],
        }
        query_a = trusted_client.post(
            "/data/v3/queries", headers=user_a, json=query_body
        )
        query_b = trusted_client.post(
            "/data/v3/queries", headers=user_b, json=query_body
        )
        assert query_a.status_code == query_b.status_code == 202
        query_a_id = query_a.json()["queryId"]
        assert query_a_id != query_b.json()["queryId"]
        assert (
            trusted_client.get(
                f"/data/v3/queries/{query_a_id}", headers=user_b
            ).status_code
            == 404
        )
        assert (
            trusted_client.post(
                f"/data/v3/queries/{query_a_id}/cancel", headers=user_b
            ).status_code
            == 404
        )
        assert (
            trusted_client.get(
                f"/data/v3/operations/{query_a_id}", headers=user_b
            ).status_code
            == 404
        )
        operation = trusted_client.get(
            f"/data/v3/operations/{query_a_id}", headers=user_a
        )
        assert operation.status_code == 200
        assert operation.json()["actor"]["subject"] == "alice@example.test"
        assert operation.json()["actor"]["requestId"] == "alice-request"

        preview_body = {
            "idempotencyKey": "same-preview-key",
            "kind": "xlsx_table",
            "versionId": detail["versionId"],
            "semanticId": detail["semanticId"],
            "objectId": raw["objectId"],
        }
        preview_a = trusted_client.post(
            "/data/v3/previews", headers=user_a, json=preview_body
        )
        preview_b = trusted_client.post(
            "/data/v3/previews", headers=user_b, json=preview_body
        )
        assert preview_a.status_code == preview_b.status_code == 202
        preview_a_id = preview_a.json()["previewId"]
        assert preview_a_id != preview_b.json()["previewId"]
        assert (
            trusted_client.get(
                f"/data/v3/previews/{preview_a_id}", headers=user_b
            ).status_code
            == 404
        )
        assert (
            trusted_client.post(
                f"/data/v3/previews/{preview_a_id}/cancel", headers=user_b
            ).status_code
            == 404
        )


def test_upload_attach_failure_removes_unpublished_object(
    client, settings, monkeypatch
):
    publication_id = start(client)
    content = b"well,value\n001,2\n"

    def fail_attach(*_args, **_kwargs):
        raise RuntimeError("injected attach failure")

    monkeypatch.setattr(publication_router.publications, "attach", fail_attach)
    with pytest.raises(RuntimeError, match="injected attach failure"):
        client.put(
            f"/data/v3/publications/{publication_id}/items/table",
            content=content,
            headers={
                "X-Content-SHA256": "sha256:" + hashlib.sha256(content).hexdigest()
            },
        )
    assert list((settings.root / "objects").iterdir()) == []


def blocked_child(op, settings, work_path):
    Path(work_path, "started").write_text("started")
    time.sleep(30)
    Path(work_path, "should-not-exist").write_text("late")


def test_running_cancel_and_deadline(client, worker, settings, monkeypatch):
    out = publish(client, worker)
    monkeypatch.setattr(worker_module, "child", blocked_child)
    qid = client.post(
        "/data/v3/queries",
        json={"idempotencyKey": identity(), "versionIds": [out["versionId"]]},
    ).json()["queryId"]
    errors = []

    def work():
        try:
            worker.once()
        except Exception as error:
            errors.append(error)

    thread = threading.Thread(target=work)
    thread.start()
    for _ in range(100):
        if list((settings.root / "work").glob("*/started")):
            break
        time.sleep(0.05)
    else:
        pytest.fail("query child did not start")
    assert (
        client.post("/data/v3/queries/" + qid + "/cancel").json()["state"]
        == "CANCELLED"
    )
    thread.join(5)
    assert not thread.is_alive() and not errors
    assert not list((settings.root / "work").glob("*/should-not-exist"))
    qid = client.post(
        "/data/v3/queries",
        json={"idempotencyKey": identity(), "versionIds": [out["versionId"]]},
    ).json()["queryId"]
    Worker(replace(settings, query_seconds=1)).once()
    assert (
        client.get("/data/v3/queries/" + qid).json()["error"]["code"]
        == "OPERATION_DEADLINE_EXCEEDED"
    )


def test_transaction_failure_cannot_publish_partial_group(client, worker, settings):
    outputs = [
        {"name": "table", "title": "data", "format": "csv", "policy": "FILE_QUERYABLE"},
        {
            "name": "metrics",
            "title": "metric",
            "format": "json",
            "policy": "FILE_AND_METRICS",
        },
    ]
    pid = start(client, outputs)
    upload(client, pid)
    upload(
        client,
        pid,
        "metrics",
        json.dumps(
            {
                "metrics": [
                    {
                        "name": "count",
                        "definition": "count",
                        "unit": "1",
                        "evaluationScope": "all",
                        "value": 2,
                    }
                ]
            }
        ).encode(),
    )
    ready(client, worker, pid)
    db = Database(settings.dsn)
    with db.transaction() as c:
        c.execute("""CREATE FUNCTION fail_metric() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN RAISE EXCEPTION 'injected storage failure'; END $$;
            CREATE TRIGGER fail_metric BEFORE INSERT ON metrics FOR EACH ROW EXECUTE FUNCTION fail_metric()""")
    with pytest.raises(psycopg.errors.RaiseException):
        client.post(f"/data/v3/publications/{pid}/commit")
    assert client.get("/data/v3/datasets").json()["items"] == []
    assert client.get(f"/data/v3/publications/{pid}").json()["state"] == "READY"
    with db.transaction() as c:
        c.execute("DROP TRIGGER fail_metric ON metrics")
    assert client.post(f"/data/v3/publications/{pid}/commit").status_code == 200


@pytest.mark.parametrize("virtualenv", [False, True])
def test_os_query_boundary(settings, tmp_path, virtualenv):
    store = LocalStore(settings.root)
    forbidden = tmp_path / "secret.txt"
    forbidden.write_text("not data")
    script = """
import socket
from pathlib import Path
import duckdb
from platform_data_service.adapters.sandbox import restrict_query
from platform_data_service.settings import Settings
root=Path({root!r})
db=duckdb.connect(config={{"threads":1}})
restrict_query(root/'objects',root/'work',Settings('',root))
assert 'x'.encode('cp437') == b'x'
try: Path({forbidden!r}).read_text()
except PermissionError: pass
else: raise AssertionError('outside file readable')
try: socket.socket()
except PermissionError: pass
else: raise AssertionError('network socket allowed')
assert db.execute('SELECT 1').fetchone()==(1,)
print('sandbox verified')
""".format(root=str(store.root), forbidden=str(forbidden))
    interpreter = sys.executable
    if virtualenv:
        environment = tmp_path / "venv"
        subprocess.run(
            [
                sys.executable,
                "-m",
                "venv",
                "--system-site-packages",
                "--without-pip",
                str(environment),
            ],
            check=True,
        )
        interpreter = str(environment / "bin" / "python")
    result = subprocess.run(
        [interpreter, "-c", script],
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "PYTHONPATH": os.pathsep.join(
                (
                    str(Path(__file__).resolve().parents[1] / "src"),
                    str(
                        Path(__file__).resolve().parents[3]
                        / "packages/protocol-contracts/src"
                    ),
                    sysconfig.get_path("purelib"),
                    os.environ.get("PYTHONPATH", ""),
                )
            ),
        },
    )
    assert result.returncode == 0, result.stderr
    assert "sandbox verified" in result.stdout


def test_offline_operations_fenced_by_runtime(settings):
    with runtime_lock(settings.root):
        with pytest.raises(RuntimeError, match="DATA_ROOT_IN_USE"):
            with runtime_lock(settings.root, exclusive=True):
                pass


def test_adapter_uses_http_contract_and_preserves_receipt(client, worker):
    out = publish(client, worker)
    result = query(client, worker, out["versionId"])
    import httpx

    def handler(request):
        response = client.request(
            request.method,
            request.url.path,
            params=request.url.params,
            headers=dict(request.headers),
            content=request.content,
        )
        return httpx.Response(
            response.status_code, content=response.content, headers=response.headers
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        adapter = DataClient("http://testserver", client=http)
        from_tool = adapter.query(result["queryId"]).model_dump(by_alias=True)
        assert from_tool["receipt"] == result["receipt"]
        assert from_tool["result"] == result["result"]
        with pytest.raises(ValueError, match="WRITES_NOT_ENABLED"):
            adapter.upload("id", "name", io.BytesIO(b"data"))


@pytest.mark.service_image
def test_backup_restore_container_tooling(settings, tmp_path):
    # This belongs to the joint container slice.  Running it without the exact
    # image is a configuration failure, never an accepted unverified skip.
    assert os.environ.get("DATA_TEST_BACKUP_IMAGE") == "1", (
        "DATA_SERVICE_IMAGE_REQUIRED: run check_data_knowledge_e2e"
    )
    from fastapi.testclient import TestClient
    from platform_data_service.api.app import create_app

    with TestClient(create_app(settings)) as client:
        output = publish(client, Worker(settings))
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()

    def run(command, root, dsn):
        return subprocess.run(
            [
                docker_executable(),
                "run",
                "--rm",
                "--network",
                "host",
                "--user",
                # The project-owned daemon is rootless: container UID 0 maps
                # to the invoking host user. Passing the host UID here would
                # map it a second time into the subordinate-ID range and make
                # restored bind-mount contents inaccessible to the host-side
                # verification below.
                "0",
                "-e",
                "DATA_DATABASE_URL=" + dsn,
                "-e",
                "DATA_ROOT=/data",
                "-e",
                "DATA_PROJECT=" + settings.project,
                "-v",
                str(root) + ":/data",
                "-v",
                str(backup_dir) + ":/backups",
                os.environ.get("DATA_TEST_IMAGE", "platform-data-service:3.0.0"),
                "platform-data",
                command,
                "--path",
                "/backups/snapshot",
            ],
            capture_output=True,
            text=True,
        )

    result = run("backup", settings.root, settings.dsn)
    assert result.returncode == 0, result.stderr
    from psycopg.conninfo import make_conninfo

    target = "restore_" + identity()
    with psycopg.connect(settings.dsn, autocommit=True) as c:
        c.execute(
            psycopg.sql.SQL("CREATE DATABASE {}").format(psycopg.sql.Identifier(target))
        )
    restored = tmp_path / "restored"
    restored.mkdir()
    dsn = make_conninfo(settings.dsn, dbname=target)
    try:
        result = run("restore", restored, dsn)
        assert result.returncode == 0, result.stderr
        with TestClient(
            create_app(replace(settings, dsn=dsn, root=restored))
        ) as client:
            assert (
                client.get("/data/v3/versions/" + output["versionId"]).status_code
                == 200
            )
            result = query(
                client,
                Worker(replace(settings, dsn=dsn, root=restored)),
                output["versionId"],
            )
            assert result["result"]["rows"][0][0] == "001"
    finally:
        with psycopg.connect(settings.dsn, autocommit=True) as c:
            c.execute(
                psycopg.sql.SQL("DROP DATABASE {} WITH (FORCE)").format(
                    psycopg.sql.Identifier(target)
                )
            )


@pytest.mark.service_image
def test_container_profile_keeps_landlock_and_denies_process_inspection():
    assert os.environ.get("DATA_TEST_BACKUP_IMAGE") == "1", (
        "DATA_SERVICE_IMAGE_REQUIRED: run check_data_knowledge_e2e"
    )
    script = """
import ctypes
from platform_data_service.adapters.sandbox import install_container_profile
install_container_profile('/app/seccomp.json')
libc=ctypes.CDLL(None,use_errno=True)
assert libc.syscall(444,0,0,1)>=1
assert libc.process_vm_readv(-1,None,0,None,0,0)==-1
assert ctypes.get_errno()==1
print('default-deny filter installed')
"""
    result = subprocess.run(
        [
            docker_executable(),
            "run",
            "--rm",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges:true",
            "--security-opt",
            "seccomp=unconfined",
            os.environ.get("DATA_TEST_IMAGE", "platform-data-service:3.0.0"),
            "python",
            "-c",
            script,
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_local_client_ignores_environment_proxy(monkeypatch):
    monkeypatch.setenv("ALL_PROXY", "socks5://127.0.0.1:1")
    client = DataClient("http://127.0.0.1:8110")
    client.close()
