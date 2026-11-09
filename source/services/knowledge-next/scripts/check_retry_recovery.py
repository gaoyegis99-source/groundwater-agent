"""Isolated Compose probe: real connection refusal, process restart, queue fairness.

Only the named test Knowledge deployment is written; Data is read via HTTP.
"""

import argparse
import json
import subprocess

# Pin direct checks to the owning project's Docker launcher.
import sys as _maintenance_sys  # noqa: E402
from datetime import datetime, timezone
from pathlib import Path
from pathlib import Path as _MaintenancePath  # noqa: E402

import httpx
from evaluate import wait
from platform_knowledge_client import KnowledgeClient, models
from platform_knowledge_next.models import KnowledgePackageV1

_maintenance_source = next(
    parent
    for parent in _MaintenancePath(__file__).resolve().parents
    if (parent / "scripts/maintenance/project_environment.py").is_file()
)
_maintenance_sys.path.insert(0, str(_maintenance_source))
from scripts.maintenance.project_environment import docker_executable  # noqa: E402


def as_dict(value):
    return value.model_dump(mode="json", by_alias=True)


def submit(client, payload):
    return as_dict(
        client.submit_import(models.KnowledgePackageRequest.model_validate(payload))
    )


def retrieve(client, payload):
    return as_dict(
        client.retrieve(
            models.RetrievalRequest.model_validate(
                {
                    "mode": "hybrid",
                    "purpose": "retry-recovery",
                    **payload,
                }
            )
        )
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", required=True)
    parser.add_argument("--url", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not args.project.startswith("knowledge-scope-"):
        raise ValueError("An isolated knowledge-scope-* deployment is required")
    with httpx.Client(
        base_url="http://127.0.0.1:8110/data/v3", trust_env=False
    ) as data:
        dataset = data.get("/datasets?limit=1").json()["items"][0]
        version = data.get("/versions/" + dataset["currentVersion"]).json()
    binding = dict(
        serviceBinding="data-primary",
        projectId=dataset["project"],
        datasetId=dataset["datasetId"],
        versionId=version["versionId"],
        semanticId=version["semanticId"],
        fieldId=None,
    )
    root = Path(__file__).resolve().parents[1]
    compose = [
        docker_executable(),
        "compose",
        "-p",
        args.project,
        "-f",
        str(root / "compose.yaml"),
        "-f",
        str(root / "compose.data.yaml"),
    ]
    client = KnowledgeClient(args.url, writes=True)
    prefix = "retry-probe-" + datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    source = json.loads((root / "examples/example.json").read_text())
    source["project_id"] = binding["projectId"]
    source["idempotency_key"] = prefix
    source["documents"][0]["knowledge_id"] = prefix
    source["documents"][0]["bindings"] = [binding]
    source = KnowledgePackageV1.model_validate(source).model_dump(
        mode="json", by_alias=True
    )
    general = json.loads(json.dumps(source))
    general["idempotencyKey"] = prefix + "-general"
    general["documents"][0]["knowledgeId"] = prefix + "-general"
    general["documents"][0]["bindings"] = []
    subprocess.run([*compose, "stop", "worker"], check=True, capture_output=True)
    states = []
    try:
        jid = submit(client, source)["importId"]
        code = """from dataclasses import replace
from platform_knowledge_next.config import Settings
from platform_knowledge_next.database import Database
from platform_knowledge_next.data_adapter import DataAdapter
from platform_knowledge_next.ingestion import Importer
from platform_embedding_client import EmbeddingClient
s=replace(Settings.from_env(),data_url='http://knowledge-db:1')
assert Importer(Database(s),EmbeddingClient(s.embedding_url),DataAdapter(s)).run_once()
"""

        def once():
            subprocess.run(
                [
                    docker_executable(),
                    "exec",
                    args.project + "-api-1",
                    "python",
                    "-c",
                    code,
                ],
                check=True,
                capture_output=True,
            )

        once()
        waiting = as_dict(client.get_import(jid))
        assert waiting["state"] == "RETRY_WAIT" and waiting["dataAttempts"] == 1
        states.append(waiting)
        assert submit(client, source)["importId"] == jid
        assert retrieve(client, {"query": "地下水", "mode": "lexical"})["items"]
        other = submit(client, general)["importId"]
        once()
        assert client.get_import(other).state == "SUCCEEDED"
        assert client.get_import(jid).state == "RETRY_WAIT"
    finally:
        # Existing container retains its original Data URL and network membership.
        subprocess.run([*compose, "restart", "worker"], check=True, capture_output=True)
    result = wait(client, jid)
    assert result["dataAttempts"] == 2 and result["attempt"] == 2
    states.append(result)
    for iid in (jid, other):
        row = as_dict(client.get_import(iid))
        for item in row["result"]["versions"]:
            client.update_lifecycle(
                item["versionId"],
                models.LifecycleUpdateRequest.model_validate(
                    {
                        "expectedRevision": 1,
                        "status": "WITHDRAWN",
                        "reviewAfter": "2099-01-01",
                        "reason": "retry process probe complete",
                    }
                ),
            )
    client.close()
    args.output.write_text(
        json.dumps(
            {
                "status": "PASS",
                "data_writes": 0,
                "checks": [
                    "real connection refusal",
                    "worker released during delay",
                    "general import succeeds before retry",
                    "existing knowledge readable",
                    "idempotent replay",
                    "new process resumes persisted retry and succeeds",
                ],
                "states": states,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
