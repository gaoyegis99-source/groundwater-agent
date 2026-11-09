"""Restore an isolated schema-v3 deployment including every durable import state."""

import argparse
import json
import os
import subprocess

# Pin direct checks to the owning project's Docker launcher.
import sys as _maintenance_sys  # noqa: E402
import time
from pathlib import Path
from pathlib import Path as _MaintenancePath  # noqa: E402

import httpx
from platform_knowledge_next.models import KnowledgePackageV1

_maintenance_source = next(
    parent
    for parent in _MaintenancePath(__file__).resolve().parents
    if (parent / "scripts/maintenance/project_environment.py").is_file()
)
_maintenance_sys.path.insert(0, str(_maintenance_source))
from scripts.maintenance.project_environment import docker_executable  # noqa: E402


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", required=True)
    parser.add_argument("--url", required=True)
    parser.add_argument("--restore-port", type=int, required=True)
    parser.add_argument("--backup", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--compose-override",
        type=Path,
        help="Optional isolated-network override applied to source and restored APIs",
    )
    parser.add_argument("--restore-instance-data-root", type=Path, required=True)
    args = parser.parse_args()
    if not args.project.startswith("knowledge-scope-"):
        raise ValueError("Only an isolated knowledge-scope-* deployment is allowed")
    root = Path(__file__).resolve().parents[1]
    restored = args.project + "-restore"
    source_instance_root = os.environ.get("PLATFORM_INSTANCE_DATA_ROOT")
    restore_instance_root = args.restore_instance_data_root.resolve()
    if (
        not source_instance_root
        or restore_instance_root.is_symlink()
        or not restore_instance_root.is_dir()
        or Path(source_instance_root).resolve() == restore_instance_root
    ):
        raise ValueError("Restore requires a distinct prepared instance data root")
    compose = [
        docker_executable(),
        "compose",
        "-p",
        args.project,
        "-f",
        str(root / "compose.yaml"),
    ]
    restore_override = args.compose_override
    if args.compose_override is not None:
        override = args.compose_override.resolve()
        if not override.is_file():
            raise ValueError(f"Compose override does not exist: {override}")
        compose.extend(["-f", str(override)])

        # Source and restored projects share the integration network so the
        # restored worker can still reach the deterministic embedding stub.
        # Compose otherwise publishes the same ``knowledge-db`` DNS alias for
        # both databases, allowing a worker to connect to the wrong volume.
        source_override = json.loads(override.read_text(encoding="utf-8"))
        restored_override = json.loads(json.dumps(source_override))
        restored_db_alias = "knowledge-db-" + restored
        restored_database_url = (
            f"postgresql://knowledge:knowledge-local@{restored_db_alias}:5432/knowledge"
        )
        for service in ("migrate", "api", "worker"):
            service_config = restored_override.setdefault("services", {}).setdefault(
                service, {}
            )
            service_config.setdefault("environment", {})["KNOWLEDGE_DATABASE_URL"] = (
                restored_database_url
            )
        restored_override.setdefault("services", {}).setdefault("knowledge-db", {})[
            "networks"
        ] = {
            "default": None,
            "joint": {"aliases": [restored_db_alias]},
        }
        restore_override = args.output.with_name(
            args.output.name + ".restore.override.json"
        )
        restore_override.write_text(
            json.dumps(restored_override, indent=2), encoding="utf-8"
        )

    def command(cmd, **kwargs):
        result = subprocess.run(
            cmd, check=False, capture_output=True, text=True, **kwargs
        )
        if result.returncode:
            raise RuntimeError(
                "COMMAND_FAILED "
                + " ".join(cmd)
                + "\n"
                + (result.stderr or result.stdout)[-4000:]
            )
        return result.stdout

    def execute(project, code):
        return command(
            [docker_executable(), "exec", project + "-api-1", "python", "-c", code]
        )

    client = httpx.Client(
        base_url=args.url + "/knowledge/v3/", trust_env=False, timeout=30
    )
    capabilities = client.get("capabilities")
    capabilities.raise_for_status()
    deployment_project = capabilities.json()["project"]

    def post(path, body):
        r = client.post(path, json=body)
        r.raise_for_status()
        return r.json()

    command([*compose, "stop", "worker"])
    ids = {}

    def submit_fixture(name, bound=False):
        package = json.loads((root / "examples/example.json").read_text())
        package["project_id"] = deployment_project
        package["idempotency_key"] = "restore-" + name
        package["documents"][0]["knowledge_id"] = "restore-" + name
        if bound:
            package["documents"][0]["bindings"] = [
                dict(
                    project_id=deployment_project,
                    dataset_id="fixture",
                    version_id="v1",
                    semantic_id="s1",
                )
            ]
        package = KnowledgePackageV1.model_validate(package).model_dump(
            mode="json", by_alias=True
        )
        return post("imports", package)["importId"]

    prefix = """from platform_knowledge_next.config import Settings
from platform_knowledge_next.database import Database
from platform_knowledge_next.ingestion import Importer,claim,renew
from platform_knowledge_next.data_adapter import DataAdapter
from platform_embedding_client import EmbeddingClient
from dataclasses import replace
import httpx
s=Settings.from_env();db=Database(s)
"""
    ids["FAILED"] = submit_fixture("failed", True)
    execute(
        args.project,
        prefix
        + "Importer(db,EmbeddingClient(s.embedding_url),DataAdapter(replace(s,data_url=''))).run_once()",
    )
    ids["RETRY_WAIT"] = submit_fixture("waiting", True)
    execute(
        args.project,
        prefix
        + """problem={'type':'urn:platform:problem:data-unavailable','title':'Data unavailable','status':429,'detail':'temporary fixture','code':'DATA_UNAVAILABLE','requestId':'restore-fixture','traceId':'restore-fixture','errors':[]}\nadapter=DataAdapter(replace(s,data_url='http://data.invalid'),httpx.Client(transport=httpx.MockTransport(lambda _:httpx.Response(429,headers={'Content-Type':'application/problem+json','Retry-After':'120'},json=problem))))\nImporter(db,EmbeddingClient(s.embedding_url),adapter).run_once()""",
    )
    ids["VALIDATING"] = submit_fixture("validating")
    execute(args.project, prefix + "assert claim(db) is not None")
    ids["INDEXING"] = submit_fixture("indexing")
    execute(args.project, prefix + "renew(db,claim(db),'INDEXING')")
    ids["QUEUED"] = submit_fixture("queued")
    for state, iid in ids.items():
        assert client.get("imports/" + iid).json()["state"] == state
    # Capture exact evidence and its receipt before freezing the database snapshot.
    ref = json.loads(
        execute(
            args.project,
            prefix
            + """import json
with db.transaction() as c:
 print(json.dumps(c.execute("SELECT s.version_id,s.unit_id,u.checksum FROM search_entries s JOIN units u USING(version_id,unit_id) ORDER BY s.document_id,s.ordinal LIMIT 1").fetchone()))
""",
        )
    )
    wire_ref = {
        "versionId": ref["version_id"],
        "unitId": ref["unit_id"],
        "checksum": ref["checksum"],
    }
    original = post("evidence/read", {"refs": [wire_ref]})
    receipt = client.get("retrievals/" + original["receiptId"]).json()
    snapshot_code = (
        prefix
        + """import json
with db.transaction(snapshot=True) as c:
 result={}
 for table in ['meta','imports','documents','versions','lifecycle','units','embeddings','search_entries','receipts']:
  result[table]=c.execute(f"SELECT count(*) AS rows,md5(coalesce(string_agg(to_jsonb(t)::text,'' ORDER BY to_jsonb(t)::text),'')) AS hash FROM {table} t").fetchone()
 print(json.dumps(result))
"""
    )
    before = json.loads(execute(args.project, snapshot_code))
    script_env = {**os.environ, "KNOWLEDGE_COMPOSE_PROJECT": args.project}
    if restore_override is not None:
        script_env["KNOWLEDGE_COMPOSE_OVERRIDE"] = str(Path(restore_override).resolve())
    command(
        ["bash", str(root / "scripts/backup.sh"), str(args.backup)],
        env=script_env,
    )
    restored_env = {
        **script_env,
        "KNOWLEDGE_PORT": str(args.restore_port),
        "PLATFORM_INSTANCE_DATA_ROOT": str(restore_instance_root),
    }
    command(
        ["bash", str(root / "scripts/restore.sh"), str(args.backup), restored],
        env=restored_env,
    )
    restored_compose = [
        docker_executable(),
        "compose",
        "-p",
        restored,
        "-f",
        str(root / "compose.yaml"),
    ]
    if restore_override is not None:
        restored_compose.extend(["-f", str(Path(restore_override).resolve())])
    command(
        [
            *restored_compose,
            "up",
            "-d",
            "--no-build",
            "--no-deps",
            "--wait",
            "api",
        ],
        env=restored_env,
    )
    after = json.loads(execute(restored, snapshot_code))
    assert before == after
    restored_client = httpx.Client(
        base_url=f"http://127.0.0.1:{args.restore_port}/knowledge/v3/",
        trust_env=False,
        timeout=30,
    )
    for state, iid in ids.items():
        old = client.get("imports/" + iid)
        new = restored_client.get("imports/" + iid)
        assert old.json() == new.json() and new.json()["state"] == state
    read = restored_client.post("evidence/read", json={"refs": [wire_ref]})
    read.raise_for_status()
    assert read.json()["items"] == original["items"]
    assert restored_client.get("retrievals/" + original["receiptId"]).json() == receipt
    # Replay must preserve both the retry budget and schedule after restore.
    replay = json.loads((root / "examples/example.json").read_text())
    replay["project_id"] = deployment_project
    replay["idempotency_key"] = "restore-waiting"
    replay["documents"][0]["knowledge_id"] = "restore-waiting"
    replay["documents"][0]["bindings"] = [
        dict(
            project_id=deployment_project,
            dataset_id="fixture",
            version_id="v1",
            semantic_id="s1",
        )
    ]
    replay = KnowledgePackageV1.model_validate(replay).model_dump(
        mode="json", by_alias=True
    )
    row = restored_client.post("imports", json=replay)
    row.raise_for_status()
    assert row.json()["state"] == "RETRY_WAIT" and row.json()["dataAttempts"] == 1
    # A database/hash comparison alone does not prove operational recovery.
    # Start exactly one restored worker only after the historical comparison
    # and retry fixture are frozen. It must consume a new unbound package from
    # QUEUED to SUCCEEDED without changing those restored facts.
    resume_package = json.loads((root / "examples/example.json").read_text())
    resume_package["project_id"] = deployment_project
    resume_package["idempotency_key"] = "restore-resume"
    resume_package["documents"][0]["knowledge_id"] = "restore-resume"
    resume_package["documents"][0].pop("bindings", None)
    resume_package = KnowledgePackageV1.model_validate(resume_package).model_dump(
        mode="json", by_alias=True
    )
    resumed = restored_client.post("imports", json=resume_package)
    resumed.raise_for_status()
    resumed_id = resumed.json()["importId"]
    assert resumed.json()["state"] == "QUEUED"
    command(
        [*restored_compose, "up", "-d", "--no-deps", "worker"],
        env=restored_env,
    )
    resumed_state = "QUEUED"
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        resumed_response = restored_client.get("imports/" + resumed_id)
        if resumed_response.status_code == 404:
            # A committed POST must be immediately readable from the same
            # database. Preserve a useful diagnostic instead of leaking a
            # KeyError when a restore topology points the worker/API at
            # different storage.
            listed = restored_client.get("imports", params={"limit": 100})
            raise AssertionError(
                {
                    "code": "RESTORED_IMPORT_NOT_VISIBLE",
                    "importId": resumed_id,
                    "listStatus": listed.status_code,
                    "listedImportCount": len(listed.json().get("items", [])),
                }
            )
        resumed_response.raise_for_status()
        resumed_state = resumed_response.json()["state"]
        if resumed_state in {"SUCCEEDED", "FAILED"}:
            break
        time.sleep(0.5)
    assert resumed_state == "SUCCEEDED", {
        "importId": resumed_id,
        "state": resumed_state,
    }
    report = {
        "status": "PASS",
        "before": before,
        "after": after,
        "states": list(ids) + ["SUCCEEDED"],
        "retry_replay_preserves_budget": True,
        "exact_evidence_preserved": True,
        "historical_receipt_preserved": True,
        "restored_worker_resume": {
            "status": "PASS",
            "importId": resumed_id,
            "finalState": resumed_state,
        },
        "fixture_note": "Temporary HTTP 429 fixture seeds RETRY_WAIT; restored worker resumed a new QUEUED import. Historical source/restore rows remain compared before replay.",
        "time": time.time(),
    }
    args.output.write_text(json.dumps(report, indent=2))
    client.close()
    restored_client.close()
    print("PASS: all durable states, evidence and receipts survived new-volume restore")


if __name__ == "__main__":
    main()
