from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from threading import Barrier
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from platform_embedding_client import TransientUpstreamError, UpstreamServiceError
from platform_knowledge_client import KnowledgeClient, models as knowledge_client_models
from platform_protocol_contracts import build_problem_details

from platform_knowledge_next.api import create_app
from platform_knowledge_next.data_adapter import DataAdapter, DataTransientError, retry_after
from platform_knowledge_next.database import Database
from platform_knowledge_next.errors import KnowledgeProblemError
from platform_knowledge_next.ingestion import (
    Importer,
    claim,
    request_manual_retry,
    submit,
)
from platform_knowledge_next.models import (
    DataBinding,
    EvidenceRead,
    Lookup,
    Retrieval,
    Unit,
)
from platform_knowledge_next.retrieval import EvidenceService

from conftest import Data
from test_service import job


@pytest.fixture
def binding():
    return DataBinding(
        project_id="local-data",
        dataset_id="d1",
        version_id="v1",
        semantic_id="s1",
        field_id="f0",
    )


class Clock:
    def __init__(self):
        self.now = datetime.now(timezone.utc)

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += timedelta(seconds=seconds)


class Flaky(Data):
    def __init__(self, failures=1, after=None):
        self.calls = 0
        self.failures = failures
        self.after = after

    def validate(self, binding):
        self.calls += 1
        if self.calls <= self.failures:
            raise DataTransientError(self.after)


class FlakyEmbedding:
    def __init__(self, delegate, failures, *, retry_after=None, terminal=False):
        self.delegate = delegate
        self.failures = failures
        self.retry_after = retry_after
        self.terminal = terminal
        self.calls = 0

    def embed(self, texts):
        self.calls += 1
        if self.calls <= self.failures:
            if self.terminal:
                raise UpstreamServiceError("EMBEDDING_PROVIDER_RESPONSE_INVALID")
            raise TransientUpstreamError(
                "EMBEDDING_PROVIDER_UNAVAILABLE", retry_after=self.retry_after
            )
        return self.delegate.embed(texts)


def capabilities(project="local-data"):
    return {
        "contracts": {}, "formatMediaTypes": {}, "formats": [],
        "identityMode": "local", "limits": {}, "openapi": "/openapi.json",
        "preview": {}, "productId": "platform", "project": project,
        "currentUser": {
            "subject": "local-operator", "displayName": "Local Operator",
            "roles": ["researcher", "admin"],
        },
        "permissions": {
            "catalogRead": True, "queryCreate": True, "previewCreate": True,
            "objectDownload": True, "userUploadCreate": False,
            "managedPublicationWrite": False, "semanticWrite": False,
            "accessWrite": False, "governanceRead": False,
            "qualityWrite": False, "mlopsWrite": False,
        },
        "query": {}, "version": "3.0.0",
        "writeEnabled": False,
    }


def problem(status):
    return httpx.Response(
        status,
        headers={"Content-Type": "application/problem+json", "Retry-After": "30"},
        json=build_problem_details("DATA_TEST_ERROR", status, "Test failure").model_dump(
            mode="json", by_alias=True
        ),
    )


def bound_job(db, package, binding):
    package.documents[0].bindings = [binding]
    return submit(db, package)["import_id"]


@pytest.mark.parametrize("mode", ["exact", "hnsw"])
def test_scope_identity_history_budget_and_receipt(
    db, package, embeddings, binding, mode
):
    base = package.documents[0]
    for name, changes in [
        ("match", {}),
        ("semantic", {"semantic_id": "s2"}),
        ("version", {"version_id": "v2"}),
        ("field", {"field_id": "f1"}),
        ("dataset", {"dataset_id": "d2"}),
        ("dataset_only", {"field_id": None}),
    ]:
        doc = base.model_copy(deep=True)
        doc.knowledge_id = name
        doc.bindings = [binding.model_copy(update=changes)]
        doc.units[0].required_units = ["context"]
        doc.units.append(
            Unit(unit_id="context", content="水头单位：米。", source_refs=["manual"])
        )
        package.documents.append(doc)
    submit(db, package)
    Importer(db, embeddings, Data()).run_once()
    service = EvidenceService(
        Database(replace(db.settings, vector_search=mode)), embeddings
    )
    general = service.retrieve(Retrieval(query="地下水"))
    assert {i.knowledge_id for i in general.items} == {"head"}
    before = len(embeddings.calls)
    combined = service.retrieve(Retrieval(query="地下水", binding=binding))
    assert len(embeddings.calls) == before + 1
    assert {i.knowledge_id for i in combined.items} == {
        "head",
        "match",
        "dataset_only",
    }
    assert {i.binding_match for i in combined.items} == {"GENERAL", "EXACT"}
    assert combined.coverage["scope"] == "combined"
    for cohort in ("general", "bound"):
        assert combined.coverage["cohorts"][cohort]["returned_units"] >= 1
    with db.transaction() as c:
        receipt = c.execute(
            "SELECT body FROM receipts WHERE id=%s", (combined.receipt_id,)
        ).fetchone()["body"]
    assert receipt["coverage"]["request_binding"] == binding.model_dump()
    assert receipt["items"][0]["binding_match"] in ("GENERAL", "EXACT")
    assert "content" not in receipt["items"][0]
    strict = service.retrieve(Retrieval(query="地下水", scope="bound", binding=binding))
    assert {i.knowledge_id for i in strict.items} == {"match", "dataset_only"}
    field_request = service.retrieve(
        Retrieval(
            query="地下水",
            scope="bound",
            binding=binding.model_copy(update={"field_id": "unseen-field"}),
        )
    )
    assert {i.knowledge_id for i in field_request.items} == {"dataset_only"}
    assert {i.binding_match for i in field_request.items} == {"EXACT"}
    assert {i.knowledge_id for i in service.lookup(Lookup(binding=binding)).items} == {
        "match",
        "dataset_only",
    }
    assert service.lookup(Lookup(key="match")).items[0].binding_match == "NOT_CHECKED"
    old = strict.items[0]
    historical = service.read(
        EvidenceRead(
            refs=[
                dict(
                    version_id=old.version_id,
                    unit_id=old.unit_id,
                    checksum=old.checksum,
                )
            ]
        )
    )
    assert historical.items[0].binding_match == "NOT_CHECKED"
    assert historical.items[0].content == old.content
    s2 = service.retrieve(
        Retrieval(
            query="地下水", binding=binding.model_copy(update={"semantic_id": "s2"})
        )
    )
    assert "match" not in {i.knowledge_id for i in s2.items}
    small = service.retrieve(
        Retrieval(
            query="地下水", scope="bound", binding=binding, budget={"max_units": 1}
        )
    )
    assert any(i.status == "NEEDS_EXPANSION" for i in small.items)
    assert not any(i.content for i in small.items if i.unit_id == "text")
    assert len({(i.version_id, i.unit_id) for i in combined.items}) == len(
        combined.items
    )


@pytest.mark.parametrize(
    "scope,bound", [("general", True), ("bound", False), ("combined", False)]
)
def test_scope_contract_rejects_ambiguous_context(binding, scope, bound):
    with pytest.raises(ValidationError):
        Retrieval(query="x", scope=scope, binding=binding if bound else None)


def test_project_rejected_before_embedding(db, embeddings, binding):
    wrong = binding.model_copy(update={"project_id": "wrong"})
    service = EvidenceService(db, embeddings)
    with pytest.raises(KnowledgeProblemError, match="BINDING_PROJECT_MISMATCH"):
        service.retrieve(Retrieval(query="x", binding=wrong))
    with pytest.raises(KnowledgeProblemError, match="BINDING_PROJECT_MISMATCH"):
        service.lookup(Lookup(binding=wrong))
    assert not embeddings.calls


@pytest.mark.parametrize("mode", ["exact", "hnsw"])
def test_combined_uses_global_modality_ranks_no_quotas(
    db, package, embeddings, binding, mode
):
    base = package.documents[0]
    package.documents = []
    for n in range(90):
        doc = base.model_copy(deep=True)
        doc.knowledge_id = f"doc-{n:03}"
        doc.bindings = [binding] if n >= 45 else []
        doc.units[0].content = "水头" * (1 + n % 5) + "无关字符" * (90 - n)
        package.documents.append(doc)
    submit(db, package)
    Importer(db, embeddings, Data()).run_once()
    service = EvidenceService(
        Database(replace(db.settings, vector_search=mode)), embeddings
    )
    request = Retrieval(query="水头", binding=binding)
    vector = embeddings.embed((request.query,))[0]
    dense, lexical = [], []
    with db.transaction(snapshot=True) as c:
        for cohort in ("general", "bound"):
            d, lex = service.candidates(c, request, cohort, vector)
            assert len(d) == len(lex) == 40
            dense.extend(d)
            lexical.extend(lex)
    scores = Counter()
    for rows in (
        sorted(dense, key=lambda r: (r["distance"], r["document_id"], r["ordinal"])),
        sorted(lexical, key=lambda r: (-r["score"], r["document_id"], r["ordinal"])),
    ):
        for rank, row in enumerate(rows, 1):
            scores[row["document_id"]] += 1 / (60 + rank)
    expected = sorted(scores, key=lambda k: (-scores[k], k))[:5]
    result = service.retrieve(request)
    assert [i.knowledge_id for i in result.items] == expected
    assert (
        result.coverage["dense_candidates"]
        == result.coverage["lexical_candidates"]
        == 80
    )


class CandidateResult:
    def __init__(self, rows=None, row=None):
        self.rows = rows or []
        self.row = row

    def fetchall(self):
        return self.rows

    def fetchone(self):
        return self.row


class CandidateCursor:
    def __init__(self, approximate, available, exact):
        self.approximate = approximate
        self.available = available
        self.exact = exact
        self.dense_calls = 0
        self.statements = []

    def execute(self, statement, parameters=()):
        self.statements.append((statement, parameters))
        if "ts_rank" in statement:
            return CandidateResult()
        if "LEAST(count(*),40)" in statement:
            return CandidateResult(row={"count": self.available})
        if "embedding <=>" in statement:
            self.dense_calls += 1
            rows = self.approximate if self.dense_calls == 1 else self.exact
            return CandidateResult(rows=rows)
        return CandidateResult()


def hnsw_service():
    settings = SimpleNamespace(project="local-data", vector_search="hnsw")
    return EvidenceService(SimpleNamespace(settings=settings), embeddings=None)


def candidate_rows(count):
    return [
        {
            "version_id": f"version-{index}",
            "unit_id": "text",
            "document_id": f"document-{index}",
            "ordinal": 0,
            "distance": index / 100,
        }
        for index in range(count)
    ]


def test_hnsw_candidate_under_return_uses_exact_fallback():
    exact = candidate_rows(40)
    cursor = CandidateCursor(approximate=[], available=40, exact=exact)

    dense, lexical = hnsw_service().candidates(
        cursor, Retrieval(query="head"), "general", (0.0,)
    )

    assert dense == exact and lexical == []
    assert cursor.dense_calls == 2
    assert any("enable_indexscan=off" in sql for sql, _ in cursor.statements)


def test_hnsw_small_cohort_does_not_use_exact_fallback():
    approximate = candidate_rows(2)
    cursor = CandidateCursor(approximate=approximate, available=2, exact=[])

    dense, lexical = hnsw_service().candidates(
        cursor, Retrieval(query="head"), "general", (0.0,)
    )

    assert dense == approximate and lexical == []
    assert cursor.dense_calls == 1
    assert not any("enable_indexscan=off" in sql for sql, _ in cursor.statements)


def test_combined_candidates_share_snapshot(
    db, package, embeddings, binding, monkeypatch
):
    submit(db, package)
    Importer(db, embeddings, Data()).run_once()
    service = EvidenceService(db, embeddings)
    original = service.candidates

    def candidates(c, request, scope, vector, actor=None):
        result = original(c, request, scope, vector, actor)
        if scope == "general":
            package.idempotency_key = "concurrent"
            package.documents[0].knowledge_id = "new-bound"
            package.documents[0].bindings = [binding]
            submit(db, package)
            Importer(db, embeddings, Data()).run_once()
        return result

    monkeypatch.setattr(service, "candidates", candidates)
    result = service.retrieve(Retrieval(query="水头", binding=binding, mode="lexical"))
    assert {i.knowledge_id for i in result.items} == {"head"}


def test_retry_releases_worker_replays_and_recovers_after_restart(
    db, package, embeddings, binding, imported
):
    clock = Clock()
    data = Flaky()
    package.idempotency_key = "bound"
    package.documents[0].knowledge_id = "bound"
    jid = bound_job(db, package, binding)
    assert Importer(db, embeddings, data, clock).run_once()
    waiting = job(db, jid)
    assert waiting["state"] == "RETRY_WAIT" and waiting["data_attempts"] == 1
    assert waiting["next_retry_at"] == clock() + timedelta(seconds=5)
    assert waiting["retry_deadline"] == clock() + timedelta(minutes=10)
    replay = submit(db, package)
    assert replay["import_id"] == jid and replay["data_attempts"] == 1
    assert not Importer(db, embeddings, data, clock).run_once()
    assert (
        EvidenceService(db, embeddings).lookup(Lookup(key="head")).items[0].version_id
        == imported
    )
    # Waiting Data work must not block a later general import.
    general = package.model_copy(deep=True)
    general.idempotency_key = "general"
    general.documents[0].knowledge_id = "general"
    general.documents[0].bindings = []
    other = submit(db, general)["import_id"]
    assert Importer(db, embeddings, data, clock).run_once()
    assert job(db, other)["state"] == "SUCCEEDED"
    clock.advance(5)
    # New Importer instance consumes persisted retry state and uses existing vectors.
    before = len(embeddings.calls)
    assert Importer(db, embeddings, data, clock).run_once()
    result = job(db, jid)
    assert result["state"] == "SUCCEEDED" and result["data_attempts"] == 2
    assert result["error"] is None and result["next_retry_at"] is None
    assert len(embeddings.calls) == before
    with TestClient(create_app(db.settings, embeddings)) as client:
        assert client.get("/knowledge/v3/imports/" + jid).json()["dataAttempts"] == 2


def test_retry_budget_is_finite_across_restarts(db, package, embeddings, binding):
    clock, data = Clock(), Flaky(100)
    jid = bound_job(db, package, binding)
    for n, delay in enumerate((5, 15, 45, 120), 1):
        Importer(db, embeddings, data, clock).run_once()
        row = job(db, jid)
        assert row["state"] == "RETRY_WAIT" and row["data_attempts"] == n
        assert row["next_retry_at"] == clock() + timedelta(seconds=delay)
        clock.advance(delay)
    Importer(db, embeddings, data, clock).run_once()
    assert job(db, jid)["error"]["code"] == "DATA_RETRY_EXHAUSTED"
    assert job(db, jid)["data_attempts"] == 5 and data.calls == 5
    clock.advance(1000)
    assert not Importer(db, embeddings, data, clock).run_once()
    assert submit(db, package)["state"] == "FAILED"


def test_embedding_transient_retry_recovers_without_new_idempotency_key(
    db, package, embeddings
):
    clock = Clock()
    flaky = FlakyEmbedding(embeddings, 1, retry_after="30")
    jid = submit(db, package)["import_id"]
    assert Importer(db, flaky, Data(), clock).run_once()
    waiting = job(db, jid)
    assert waiting["state"] == "RETRY_WAIT"
    assert waiting["attempt"] == 1 and waiting["data_attempts"] == 0
    assert waiting["next_retry_at"] == clock() + timedelta(seconds=30)
    assert waiting["last_error"]["code"] == "EMBEDDING_PROVIDER_UNAVAILABLE"
    assert submit(db, package)["import_id"] == jid
    clock.advance(30)
    assert Importer(db, flaky, Data(), clock).run_once()
    assert job(db, jid)["state"] == "SUCCEEDED"
    assert flaky.calls == 2 and len(embeddings.calls) == 1


def test_embedding_transient_retry_is_finite_and_bad_response_is_terminal(
    db, package, embeddings
):
    clock = Clock()
    flaky = FlakyEmbedding(embeddings, 100)
    jid = submit(db, package)["import_id"]
    for expected_attempt, delay in enumerate((5, 15, 45, 120), 1):
        assert Importer(db, flaky, Data(), clock).run_once()
        row = job(db, jid)
        assert row["state"] == "RETRY_WAIT"
        assert row["attempt"] == expected_attempt
        clock.advance(delay)
    assert Importer(db, flaky, Data(), clock).run_once()
    assert job(db, jid)["error"]["code"] == "EMBEDDING_RETRY_EXHAUSTED"
    assert flaky.calls == 5
    assert not Importer(db, flaky, Data(), clock).run_once()

    other = package.model_copy(deep=True)
    other.idempotency_key = "bad-embedding-response"
    bad = submit(db, other)["import_id"]
    assert Importer(db, FlakyEmbedding(embeddings, 1, terminal=True), Data(), clock).run_once()
    assert job(db, bad)["state"] == "FAILED"
    assert job(db, bad)["error"]["code"] == "EMBEDDING_PROVIDER_RESPONSE_INVALID"


def test_manual_retry_reuses_import_identity_with_a_new_embedding_budget(
    db, package, embeddings
):
    clock = Clock()
    flaky = FlakyEmbedding(embeddings, 5)
    jid = submit(db, package)["import_id"]
    for delay in (5, 15, 45, 120):
        assert Importer(db, flaky, Data(), clock).run_once()
        clock.advance(delay)
    assert Importer(db, flaky, Data(), clock).run_once()
    exhausted = job(db, jid)
    assert exhausted["state"] == "FAILED"
    assert exhausted["attempt"] == 5
    assert exhausted["error"]["code"] == "EMBEDDING_RETRY_EXHAUSTED"

    assert request_manual_retry(db, jid, "manual-embedding-1") == jid
    queued = job(db, jid)
    assert queued["state"] == "QUEUED"
    assert queued["retry_generation"] == 1
    assert queued["retry_base_attempt"] == 5
    assert queued["data_retry_base_attempt"] == 0
    assert queued["retry_deadline"] is None
    assert queued["error"] is None
    assert submit(db, package)["import_id"] == jid
    assert request_manual_retry(db, jid, "manual-embedding-1") == jid
    assert job(db, jid)["retry_generation"] == 1
    with db.transaction() as connection:
        receipt = connection.execute(
            "SELECT failure,from_attempt FROM import_manual_retries WHERE request_id=%s",
            ("manual-embedding-1",),
        ).fetchone()
    assert receipt == {
        "failure": exhausted["error"],
        "from_attempt": 5,
    }
    # A late worker holding the previous claim cannot fail the new cycle.
    Importer(db, flaky, Data(), clock).fail(exhausted, "LATE_FAILURE", None)
    assert job(db, jid)["state"] == "QUEUED"
    assert Importer(db, flaky, Data(), clock).run_once()
    completed = job(db, jid)
    assert completed["state"] == "SUCCEEDED"
    assert completed["attempt"] == 6
    assert completed["retry_generation"] == 1
    assert flaky.calls == 6
    assert request_manual_retry(db, jid, "manual-embedding-1") == jid
    assert job(db, jid)["retry_generation"] == 1


def test_manual_retry_preserves_lifetime_data_attempts_and_resets_cycle(
    db, package, embeddings, binding
):
    clock, data = Clock(), Flaky(5)
    jid = bound_job(db, package, binding)
    for delay in (5, 15, 45, 120):
        assert Importer(db, embeddings, data, clock).run_once()
        clock.advance(delay)
    assert Importer(db, embeddings, data, clock).run_once()
    exhausted = job(db, jid)
    assert exhausted["error"]["code"] == "DATA_RETRY_EXHAUSTED"
    assert exhausted["data_attempts"] == 5

    request_manual_retry(db, jid, "manual-data-1")
    queued = job(db, jid)
    assert queued["data_attempts"] == 5
    assert queued["data_retry_base_attempt"] == 5
    assert queued["retry_base_attempt"] == exhausted["attempt"]
    assert Importer(db, embeddings, data, clock).run_once()
    completed = job(db, jid)
    assert completed["state"] == "SUCCEEDED"
    assert completed["data_attempts"] == 6
    assert completed["retry_generation"] == 1
    assert data.calls == 6


def test_manual_retry_grants_exactly_one_more_bounded_data_cycle(
    db, package, embeddings, binding
):
    clock, data = Clock(), Flaky(100)
    jid = bound_job(db, package, binding)
    for delay in (5, 15, 45, 120):
        assert Importer(db, embeddings, data, clock).run_once()
        clock.advance(delay)
    assert Importer(db, embeddings, data, clock).run_once()
    assert job(db, jid)["data_attempts"] == 5

    request_manual_retry(db, jid, "manual-second-cycle")
    for delay in (5, 15, 45, 120):
        assert Importer(db, embeddings, data, clock).run_once()
        waiting = job(db, jid)
        assert waiting["state"] == "RETRY_WAIT"
        clock.advance(delay)
    assert Importer(db, embeddings, data, clock).run_once()
    exhausted_again = job(db, jid)
    assert exhausted_again["state"] == "FAILED"
    assert exhausted_again["error"]["code"] == "DATA_RETRY_EXHAUSTED"
    assert exhausted_again["data_attempts"] == 10
    assert exhausted_again["data_retry_base_attempt"] == 5
    assert exhausted_again["retry_generation"] == 1
    assert data.calls == 10


def test_manual_retry_concurrent_same_request_creates_one_cycle(
    db, package, embeddings
):
    clock = Clock()
    jid = submit(db, package)["import_id"]
    flaky = FlakyEmbedding(embeddings, 5)
    for delay in (5, 15, 45, 120):
        assert Importer(db, flaky, Data(), clock).run_once()
        clock.advance(delay)
    assert Importer(db, flaky, Data(), clock).run_once()

    barrier = Barrier(2)

    def retry_once():
        barrier.wait(timeout=5)
        return request_manual_retry(db, jid, "concurrent-manual-retry")

    with ThreadPoolExecutor(max_workers=2) as workers:
        results = [workers.submit(retry_once) for _ in range(2)]
        assert [result.result(timeout=10) for result in results] == [jid, jid]
    with db.transaction() as connection:
        receipts = connection.execute(
            "SELECT count(*) AS n FROM import_manual_retries WHERE import_id=%s",
            (jid,),
        ).fetchone()["n"]
    assert receipts == 1
    assert job(db, jid)["retry_generation"] == 1


def test_manual_retry_rejects_permanent_failure_and_reused_request_key(
    db, package, embeddings
):
    bad = submit(db, package)["import_id"]
    assert Importer(
        db, FlakyEmbedding(embeddings, 1, terminal=True), Data()
    ).run_once()
    with pytest.raises(KnowledgeProblemError, match="IMPORT_NOT_RETRYABLE"):
        request_manual_retry(db, bad, "manual-bad")
    with pytest.raises(KnowledgeProblemError, match="RETRY_REQUEST_ID_INVALID"):
        request_manual_retry(db, bad, "invalid request key")
    with pytest.raises(KnowledgeProblemError, match="IMPORT_NOT_FOUND"):
        request_manual_retry(db, "missing-import", "manual-missing")

    transient = package.model_copy(deep=True)
    transient.idempotency_key = "second-transient"
    second = submit(db, transient)["import_id"]
    clock = Clock()
    flaky = FlakyEmbedding(embeddings, 5)
    for delay in (5, 15, 45, 120):
        assert Importer(db, flaky, Data(), clock).run_once()
        clock.advance(delay)
    assert Importer(db, flaky, Data(), clock).run_once()
    assert job(db, second)["error"]["code"] == "EMBEDDING_RETRY_EXHAUSTED"
    request_manual_retry(db, second, "manual-owned-by-second")
    with pytest.raises(KnowledgeProblemError, match="RETRY_REQUEST_CONFLICT"):
        request_manual_retry(db, bad, "manual-owned-by-second")
    with db.transaction() as connection:
        count = connection.execute(
            "SELECT count(*) AS n FROM import_manual_retries"
        ).fetchone()["n"]
    assert count == 1


def test_manual_retry_http_contract_is_idempotent_and_camel_case(
    db, package, embeddings
):
    clock = Clock()
    jid = submit(db, package)["import_id"]
    flaky = FlakyEmbedding(embeddings, 5)
    for delay in (5, 15, 45, 120):
        assert Importer(db, flaky, Data(), clock).run_once()
        clock.advance(delay)
    assert Importer(db, flaky, Data(), clock).run_once()

    with TestClient(create_app(db.settings, embeddings)) as client:
        path = f"/knowledge/v3/imports/{jid}/retry"
        invalid = client.post(path, json={"request_id": "manual-http"})
        assert invalid.status_code == 422
        first = client.post(path, json={"requestId": "manual-http"})
        assert first.status_code == 202
        assert first.json()["importId"] == jid
        assert first.json()["state"] == "QUEUED"
        assert first.json()["retryGeneration"] == 1
        sdk_result = KnowledgeClient(
            "http://testserver", writes=True, client=client
        ).retry_import(
            jid, knowledge_client_models.RetryImportRequest(requestId="manual-http")
        )
        assert sdk_result.import_id == jid
        assert sdk_result.retry_generation == 1
        repeated = client.post(path, json={"requestId": "manual-http"})
        assert repeated.status_code == 202
        assert repeated.json()["retryGeneration"] == 1
        different = client.post(path, json={"requestId": "manual-http-2"})
        assert different.status_code == 409
    trusted = replace(db.settings, identity_mode="gateway")
    with TestClient(create_app(trusted, embeddings)) as client:
        denied = client.post(
            path,
            json={"requestId": "manual-user"},
            headers={
                "X-Project-ID": db.settings.project,
                "X-Platform-Subject": "user@example.test",
                "X-Platform-Display-Name": "Project User",
                "X-Platform-Roles": "researcher",
            },
        )
        assert denied.status_code == 403
    readonly = replace(db.settings, read_only=True)
    with TestClient(create_app(readonly, embeddings)) as client:
        assert client.post(path, json={"requestId": "manual-readonly"}).status_code == 403
    assert job(db, jid)["retry_generation"] == 1


def test_retry_after_cannot_extend_absolute_deadline(db, package, embeddings, binding):
    clock, data = Clock(), Flaky(100, after=3600)
    jid = bound_job(db, package, binding)
    Importer(db, embeddings, data, clock).run_once()
    row = job(db, jid)
    assert (
        row["next_retry_at"] == row["retry_deadline"] == clock() + timedelta(minutes=10)
    )
    clock.advance(600)
    Importer(db, embeddings, data, clock).run_once()
    assert job(db, jid)["error"]["code"] == "DATA_RETRY_DEADLINE_EXCEEDED"
    assert data.calls == 1


def test_stale_actor_cannot_fail_retry_or_commit(db, package, embeddings, binding):
    clock = Clock()
    jid = bound_job(db, package, binding)
    old = claim(db, clock)
    actor = Importer(db, embeddings, Data(), clock)
    actor.begin_data_attempt(old)
    clock.advance(120)
    # Expired lease is fenced even before another worker claims.
    actor.fail(old, "STALE_ERROR", None)
    actor.retry(old, DataTransientError())
    assert job(db, jid)["state"] == "VALIDATING"
    new = claim(db, clock)
    actor.fail(old, "STALE_ERROR", None)
    actor.retry(old, DataTransientError())
    with pytest.raises(KnowledgeProblemError, match="IMPORT_LEASE_LOST"):
        actor.commit(old, package, clock())
    assert job(db, jid)["attempt"] == 2 and job(db, jid)["data_attempts"] == 1
    actor.process(new)
    assert job(db, jid)["state"] == "SUCCEEDED" and job(db, jid)["data_attempts"] == 2


def test_retry_checks_concurrent_version_before_data(
    db, package, embeddings, binding, imported
):
    clock, data = Clock(), Flaky()
    package.idempotency_key = "waiting-update"
    package.documents[0].expected_version = imported
    jid = bound_job(db, package, binding)
    Importer(db, embeddings, data, clock).run_once()
    winner = package.model_copy(deep=True)
    winner.idempotency_key = "winner"
    winner.documents[0].bindings = []
    winner_id = submit(db, winner)["import_id"]
    Importer(db, embeddings, data, clock).run_once()
    assert job(db, winner_id)["state"] == "SUCCEEDED"
    clock.advance(5)
    Importer(db, embeddings, data, clock).run_once()
    assert job(db, jid)["error"]["code"] == "VERSION_CONFLICT" and data.calls == 1


@pytest.mark.parametrize(
    "status,code,retry",
    [
        (408, "DATA_UNAVAILABLE", True),
        (429, "DATA_UNAVAILABLE", True),
        (500, "DATA_UNAVAILABLE", True),
        (502, "DATA_UNAVAILABLE", True),
        (503, "DATA_UNAVAILABLE", True),
        (504, "DATA_UNAVAILABLE", True),
        (404, "DATA_BINDING_NOT_FOUND", False),
        (401, "DATA_ACCESS_DENIED", False),
        (403, "DATA_ACCESS_DENIED", False),
        (422, "DATA_BINDING_REJECTED", False),
        (501, "DATA_BINDING_REJECTED", False),
    ],
)
def test_http_failure_classification(
    db, package, embeddings, binding, status, code, retry
):
    adapter = DataAdapter(
        replace(db.settings, data_url="http://data"),
        httpx.Client(
            transport=httpx.MockTransport(lambda _: problem(status))
        ),
    )
    jid = bound_job(db, package, binding)
    Importer(db, embeddings, adapter).run_once()
    row = job(db, jid)
    assert row["state"] == ("RETRY_WAIT" if retry else "FAILED")
    assert row["error"]["code"] == code
    if retry:
        assert (row["next_retry_at"] - row["updated_at"]).total_seconds() == 30


@pytest.mark.parametrize(
    "url,body,code",
    [
        (None, {}, "DATA_NOT_CONFIGURED"),
        ("ftp://data", {}, "DATA_ADDRESS_INVALID"),
        ("http://", {}, "DATA_ADDRESS_INVALID"),
        ("http://data", [], "DATA_RESPONSE_INVALID"),
        ("http://data", {}, "DATA_RESPONSE_INVALID"),
        ("http://data", capabilities("wrong"), "DATA_PROJECT_MISMATCH"),
    ],
)
def test_config_and_invalid_contract_are_permanent(db, binding, url, body, code):
    with pytest.raises(KnowledgeProblemError, match=code) as error:
        adapter = DataAdapter(
            replace(db.settings, data_url=url),
            httpx.Client(
                transport=httpx.MockTransport(lambda _: httpx.Response(200, json=body))
            ),
        )
        adapter.validate(binding)
    assert not isinstance(error.value, DataTransientError)


def test_malformed_json_and_transport_failures(db, binding):
    responses = [
        httpx.Response(200, content=b"{", headers={"Content-Type": "application/json"}),
        httpx.ReadTimeout("timeout"),
        httpx.ConnectError("offline"),
        httpx.RemoteProtocolError("bad"),
    ]
    for response, code, transient in zip(
        responses,
        [
            "DATA_RESPONSE_INVALID",
            "DATA_UNAVAILABLE",
            "DATA_UNAVAILABLE",
            "DATA_PROTOCOL_ERROR",
        ],
        [False, True, True, False],
        strict=True,
    ):

        def respond(_):
            if isinstance(response, Exception):
                raise response
            return response

        adapter = DataAdapter(
            replace(db.settings, data_url="http://data"),
            httpx.Client(transport=httpx.MockTransport(respond)),
        )
        with pytest.raises(KnowledgeProblemError, match=code) as error:
            adapter.validate(binding)
        assert isinstance(error.value, DataTransientError) == transient


def test_attempt_resource_cache_is_not_reused_on_retry(db, binding):
    paths = []

    def respond(request):
        paths.append(request.url.path)
        if request.url.path.endswith("capabilities"):
            body = capabilities()
        elif "/semantics/" in request.url.path:
            body = {
                "semanticId": "s1",
                "versionId": "v1",
                "actor": {},
                "createdAt": "2026-10-15T00:00:00Z",
                "body": {"fields": {"f0": {}, "f1": {}}},
            }
        else:
            body = {
                "versionId": "v1", "datasetId": "d1", "semanticId": "s1",
                "createdAt": "2026-10-15T00:00:00Z", "currentVersion": "v1",
                "descriptor": {}, "inputVersions": [], "objects": [],
                "project": "local-data", "publicationId": "p1",
                "semantics": {}, "tags": [], "title": "Test version",
            }
        return httpx.Response(200, json=body)

    adapter = DataAdapter(
        replace(db.settings, data_url="http://data"),
        httpx.Client(transport=httpx.MockTransport(respond)),
    )
    for _ in range(2):
        adapter.begin_attempt()
        adapter.validate(binding)
        adapter.validate(binding.model_copy(update={"field_id": "f1"}))
    assert len(paths) == 6 and not any(p.endswith("/s2") for p in paths)


def test_retry_after_formats():
    now = datetime(2026, 10, 11, tzinfo=timezone.utc)
    assert retry_after("Sun, 11 Oct 2026 00:00:20 GMT", now) == 20
    assert retry_after("Sun, 10 Oct 2026 00:00:20 GMT", now) == 0
    assert retry_after("45", now) == 45
    for value in ["-1", "garbage", "1.5", None]:
        assert retry_after(value, now) is None
