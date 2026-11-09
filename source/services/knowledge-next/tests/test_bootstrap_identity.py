import hashlib
import json

import pytest
import psycopg

from platform_knowledge_next import cli
from platform_knowledge_next.cli import (
    _canonical_checksum,
    _prepare_bootstrap_import,
    _run_bootstrap,
    _verified_bootstrap_manifest,
)
from platform_knowledge_next.models import KnowledgePackageV1


def _digest(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _package() -> KnowledgePackageV1:
    return KnowledgePackageV1.model_validate(
        {
            "projectId": "groundwater-demo",
            "idempotencyKey": "domain-3-0-0-0123456789abcdef",
            "documents": [
                {
                    "knowledgeId": knowledge_id,
                    "title": f"Document {knowledge_id}",
                    "contentVersion": "3.0.0",
                    "sources": [
                        {
                            "sourceId": "manual",
                            "kind": "DOMAIN_DEFINITION",
                            "title": "Manual",
                            "publisher": "Test",
                            "reviewAfter": "2099-01-01",
                        }
                    ],
                    "units": [
                        {
                            "unitId": "text",
                            "content": "content",
                            "sourceRefs": ["manual"],
                        }
                    ],
                }
                for knowledge_id in ("one", "two")
            ],
        }
    )


def test_migrate_retries_only_database_operational_failures(monkeypatch):
    events = []

    class StartingDatabase:
        def migrate(self):
            events.append("migrate")
            if events.count("migrate") < 3:
                raise psycopg.OperationalError("the database system is starting up")

    monkeypatch.setattr(
        cli.time, "sleep", lambda seconds: events.append(("sleep", seconds))
    )

    cli._migrate_when_database_ready(StartingDatabase())

    assert events == ["migrate", ("sleep", 1), "migrate", ("sleep", 2), "migrate"]


def test_migrate_does_not_retry_knowledge_contract_failure(monkeypatch):
    class InvalidDatabase:
        def migrate(self):
            raise RuntimeError("DATABASE_CONTRACT_MISMATCH")

    monkeypatch.setattr(
        cli.time,
        "sleep",
        lambda seconds: pytest.fail(f"unexpected retry after {seconds}s"),
    )

    with pytest.raises(RuntimeError, match="DATABASE_CONTRACT_MISMATCH"):
        cli._migrate_when_database_ready(InvalidDatabase())


def test_bootstrap_verifies_every_locked_domain_file(tmp_path):
    manifest = {
        "domainId": "groundwater",
        "version": "3.0.0",
        "knowledgeDocuments": ["knowledge.json"],
    }
    (tmp_path / "domain-manifest.json").write_text(json.dumps(manifest))
    knowledge = tmp_path / "knowledge.json"
    knowledge.write_text('{"documents": []}')
    files = {
        name: _digest((tmp_path / name).read_bytes())
        for name in ("domain-manifest.json", "knowledge.json")
    }
    lock = {
        "domainPackage": {
            "domainId": "groundwater",
            "version": "3.0.0",
            "checksum": _canonical_checksum(files),
            "files": files,
        },
        "knowledgeBootstrap": {
            "projects": ["groundwater-demo"],
            "files": ["knowledge.json"],
            "documentCount": 0,
            "chunkCount": 0,
        },
    }
    lock_path = tmp_path / "assembly-lock.json"
    lock_path.write_text(json.dumps(lock))

    loaded, expected, checksum = _verified_bootstrap_manifest(
        tmp_path, lock_path, "groundwater-demo"
    )
    assert loaded == manifest
    assert expected == lock["knowledgeBootstrap"]
    assert checksum == lock["domainPackage"]["checksum"]

    knowledge.write_text('{"documents": ["tampered"]}')
    with pytest.raises(SystemExit, match="DOMAIN_FILE_CHECKSUM_MISMATCH"):
        _verified_bootstrap_manifest(tmp_path, lock_path, "groundwater-demo")


def test_bootstrap_preflight_skips_exact_catalog_snapshot():
    package = _package()
    prepared = _prepare_bootstrap_import(
        package,
        {
            "documents": [
                {
                    "knowledgeId": document.knowledge_id,
                    "currentVersion": f"version-{index}",
                    "contentChanged": False,
                }
                for index, document in enumerate(package.documents)
            ]
        },
    )
    assert prepared is None


def test_bootstrap_preflight_imports_only_changed_documents_with_catalog_cas():
    package = _package()
    prepared = _prepare_bootstrap_import(
        package,
        {
            "documents": [
                {
                    "knowledgeId": "one",
                    "currentVersion": "version-current",
                    "contentChanged": True,
                },
                {
                    "knowledgeId": "two",
                    "currentVersion": "version-unchanged",
                    "contentChanged": False,
                },
            ]
        },
    )
    assert prepared is not None
    assert [document.knowledge_id for document in prepared.documents] == ["one"]
    assert prepared.documents[0].expected_version == "version-current"
    assert prepared.idempotency_key.startswith(package.idempotency_key + "-")
    assert len(prepared.idempotency_key.rsplit("-", 1)[1]) == 20


def test_bootstrap_surfaces_import_failure_code():
    package = _package()

    class Response:
        def __init__(self, body):
            self.body = body

        def raise_for_status(self):
            return None

        def json(self):
            return self.body

    class Client:
        def __init__(self):
            self.posts = 0

        def post(self, *_args, **_kwargs):
            self.posts += 1
            if self.posts == 1:
                return Response(
                    {
                        "documents": [
                            {
                                "knowledgeId": document.knowledge_id,
                                "currentVersion": None,
                                "contentChanged": True,
                            }
                            for document in package.documents
                        ]
                    }
                )
            if self.posts == 2:
                return Response({"canSubmit": True})
            return Response({"importId": "import-test"})

        def get(self, *_args, **_kwargs):
            return Response(
                {"state": "FAILED", "error": {"code": "VERSION_CONFLICT"}}
            )

    with pytest.raises(
        SystemExit, match="KNOWLEDGE_BOOTSTRAP_FAILED:VERSION_CONFLICT"
    ):
        _run_bootstrap(Client(), "http://knowledge", {}, package)
