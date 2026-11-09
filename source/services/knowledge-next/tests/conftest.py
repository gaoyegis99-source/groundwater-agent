import hashlib
import os

import pytest

from platform_knowledge_next.config import Settings
from platform_knowledge_next.database import Database
from platform_knowledge_next.ingestion import Importer, submit
from platform_knowledge_next.models import KnowledgePackageV1


class Embeddings:
    def __init__(self):
        self.calls = []
        self.hook = None

    def embed(self, texts):
        self.calls.append(texts)
        if self.hook:
            self.hook()
        vectors = []
        for text in texts:
            values = [0.0] * 1024
            for word in text:
                values[
                    int(hashlib.sha256(word.encode()).hexdigest()[:8], 16) % 1024
                ] += 1
            norm = sum(v * v for v in values) ** 0.5
            vectors.append(tuple(v / norm for v in values))
        return tuple(vectors)

    def ready(self):
        pass

    def close(self):
        pass


class Data:
    def begin_attempt(self):
        pass

    def validate(self, binding):
        pass


@pytest.fixture
def db():
    url = os.getenv("TEST_KNOWLEDGE_DATABASE_URL")
    if not url or not url.rsplit("/", 1)[-1].endswith("_test"):
        pytest.skip(
            "Set TEST_KNOWLEDGE_DATABASE_URL to an isolated database ending in _test"
        )
    db = Database(Settings(url))
    db.migrate()
    with db.transaction() as c:
        # TRUNCATE requires ACCESS EXCLUSIVE on every table.  That made the
        # shared test database intermittently wait behind a short-lived reader
        # or autovacuum even though the preceding test had completed.  Delete
        # in foreign-key order instead: the fixture still starts from an empty
        # database, while ordinary row locks remain compatible with readers.
        for statement in (
            "DELETE FROM receipts",
            "DELETE FROM search_entries",
            "DELETE FROM lifecycle",
            "DELETE FROM units",
        ):
            c.execute(statement)
        c.execute("UPDATE documents SET current_version=NULL")
        for statement in (
            "DELETE FROM versions",
            "DELETE FROM documents",
            "DELETE FROM import_manual_retries",
            "DELETE FROM imports",
            "DELETE FROM embeddings",
        ):
            c.execute(statement)
        c.execute("UPDATE meta SET revision=0")
    return db


@pytest.fixture
def embeddings():
    return Embeddings()


@pytest.fixture
def package():
    return KnowledgePackageV1.model_validate(
        {
            "project_id": "local-data",
            "idempotency_key": "first",
            "documents": [
                {
                    "knowledge_id": "head",
                    "title": "地下水水头",
                    "content_version": "1.0.0",
                    "aliases": ["水头", "head"],
                    "sources": [
                        {
                            "source_id": "manual",
                            "kind": "DOMAIN_DEFINITION",
                            "title": "说明",
                            "publisher": "测试",
                            "review_after": "2099-01-01",
                        }
                    ],
                    "units": [
                        {
                            "unit_id": "text",
                            "content": "地下水水头是水位的解释。",
                            "source_refs": ["manual"],
                        }
                    ],
                }
            ],
        }
    )


@pytest.fixture
def imported(db, package, embeddings):
    job = submit(db, package)
    assert Importer(db, embeddings, Data()).run_once()
    with db.transaction() as c:
        row = c.execute(
            "SELECT * FROM imports WHERE id=%s", (job["import_id"],)
        ).fetchone()
    assert row["state"] == "SUCCEEDED", row
    return row["result"]["versions"][0]["version_id"]
