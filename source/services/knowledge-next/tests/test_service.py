from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import date
import json
from importlib.resources import files

from fastapi.testclient import TestClient
from platform_embedding_client import UpstreamServiceError
from platform_protocol_contracts import validate_camel_json
import pytest

from platform_knowledge_next import catalog
from platform_knowledge_next.api import create_app
from platform_knowledge_next.config import Settings
from platform_knowledge_next.database import Database
from platform_knowledge_next.errors import KnowledgeProblemError
from platform_knowledge_next.ingestion import Importer, claim, renew, submit
from platform_knowledge_next.models import (
    EvidenceRead,
    EvidenceRef,
    LifecycleUpdate,
    Lookup,
    Retrieval,
    Unit,
)
from platform_knowledge_next.retrieval import EvidenceService

from conftest import Data


def job(db, jid):
    with db.transaction() as c:
        return c.execute("SELECT * FROM imports WHERE id=%s", (jid,)).fetchone()


def test_idempotency_and_conflicts(db, package, embeddings, imported):
    assert submit(db, package)["state"] == "SUCCEEDED"
    package.documents[0].title = "changed"
    with pytest.raises(KnowledgeProblemError, match="IDEMPOTENCY_CONFLICT"):
        submit(db, package)
    package.idempotency_key = "second"
    jid = submit(db, package)["import_id"]
    Importer(db, embeddings, Data()).run_once()
    assert job(db, jid)["error"]["code"] == "VERSION_CONFLICT"


def test_import_preflight_distinguishes_catalog_cas_from_content(
    db, package, embeddings, imported
):
    with TestClient(create_app(db.settings, embeddings)) as client:
        unchanged = client.post(
            "/knowledge/v3/imports/preflight",
            json=package.model_dump(mode="json", by_alias=True),
        )
        assert unchanged.status_code == 200, unchanged.text
        difference = unchanged.json()["documents"][0]
        assert difference["currentVersion"] == imported
        assert difference["expectedVersionMatches"] is False
        assert difference["contentChanged"] is False

        package.documents[0].title = "changed"
        changed = client.post(
            "/knowledge/v3/imports/preflight",
            json=package.model_dump(mode="json", by_alias=True),
        )
        assert changed.status_code == 200, changed.text
        assert changed.json()["documents"][0]["contentChanged"] is True


def test_import_cas_precondition_is_not_persisted_as_version_content(
    db, package, embeddings, imported
):
    package.idempotency_key = "replacement"
    package.documents[0].expected_version = imported
    package.documents[0].title = "replacement"
    import_id = submit(db, package)["import_id"]
    Importer(db, embeddings, Data()).run_once()
    assert job(db, import_id)["state"] == "SUCCEEDED"
    with db.transaction() as connection:
        body = connection.execute(
            """SELECT v.body FROM documents d JOIN versions v
               ON v.id=d.current_version WHERE d.id=%s""",
            (package.documents[0].knowledge_id,),
        ).fetchone()["body"]
    assert "expected_version" not in body


def test_version_history_and_embedding_reuse(db, package, embeddings, imported):
    old_calls = len(embeddings.calls)
    package.idempotency_key = "second"
    package.documents[0].expected_version = imported
    package.documents[0].content_version = "1.0.1"
    jid = submit(db, package)["import_id"]
    Importer(db, embeddings, Data()).run_once()
    assert job(db, jid)["state"] == "SUCCEEDED"
    assert len(embeddings.calls) == old_calls
    with db.transaction() as c:
        assert c.execute("SELECT count(*) AS n FROM versions").fetchone()["n"] == 2
        assert (
            c.execute("SELECT DISTINCT version_id FROM search_entries").fetchone()[
                "version_id"
            ]
            != imported
        )
        assert catalog.version(c, imported)["body"]["content_version"] == "1.0.0"


def test_failed_package_is_atomic_and_does_not_block_read(
    db, package, embeddings, imported
):
    service = EvidenceService(db, embeddings)
    package.idempotency_key = "failing"
    package.documents[0].expected_version = imported
    package.documents[0].units[0].content = "new"
    extra = package.documents[0].model_copy(deep=True)
    extra.knowledge_id, extra.expected_version = "extra", None
    package.documents.append(extra)

    def fail():
        # A separate thread can read while the provider call is in progress.
        with ThreadPoolExecutor(1) as pool:
            result = pool.submit(service.lookup, Lookup(key="head")).result(timeout=2)
        assert result.items[0].version_id == imported
        raise UpstreamServiceError("EMBEDDING_PROVIDER_UNAVAILABLE")

    embeddings.hook = fail
    jid = submit(db, package)["import_id"]
    Importer(db, embeddings, Data()).run_once()
    assert job(db, jid)["state"] == "FAILED"
    with db.transaction() as c:
        assert c.execute("SELECT count(*) AS n FROM documents").fetchone()["n"] == 1


def test_lease_recovery_fences_old_attempt(db, package, embeddings):
    jid = submit(db, package)["import_id"]
    old = claim(db)
    assert claim(db) is None
    with db.transaction() as c:
        c.execute(
            "UPDATE imports SET lease_until=now()-interval '1 second' WHERE id=%s",
            (jid,),
        )
    new = claim(db)
    assert new["attempt"] == old["attempt"] + 1
    with pytest.raises(KnowledgeProblemError, match="IMPORT_LEASE_LOST"):
        renew(db, old, "INDEXING")
    Importer(db, embeddings, Data()).process(new)
    assert job(db, jid)["state"] == "SUCCEEDED"


def test_cas_checked_after_embedding(db, package, embeddings, imported):
    package.idempotency_key = "race"
    package.documents[0].expected_version = imported
    package.documents[0].title = "new"
    first = claim_after_submit(db, package)
    package.idempotency_key = "winner"
    second = claim_after_submit(db, package)
    importer = Importer(db, embeddings, Data())
    importer.process(second)
    with pytest.raises(KnowledgeProblemError, match="VERSION_CONFLICT"):
        importer.process(first)


def claim_after_submit(db, package):
    submit(db, package)
    return claim(db)


def test_evidence_checksums_withdrawal_and_partial(db, embeddings, imported):
    service = EvidenceService(db, embeddings)
    bundle = service.lookup(Lookup(key="水头"))
    item = bundle.items[0]
    assert item.content and item.evidence_id and item.status == "OK"
    read = EvidenceRead(
        refs=[
            EvidenceRef(
                version_id=imported, unit_id=item.unit_id, checksum=item.checksum
            ),
            EvidenceRef(
                version_id=imported,
                unit_id=item.unit_id,
                checksum="sha256:" + "0" * 64,
            ),
        ]
    )
    result = service.read(read)
    assert [i.status for i in result.items] == ["OK", "STALE"]
    assert result.items[1].content is None
    catalog.update_lifecycle(
        db,
        imported,
        LifecycleUpdate(
            expected_revision=1,
            status="WITHDRAWN",
            review_after=date(2099, 1, 1),
            reason="incorrect",
        ),
    )
    assert service.lookup(Lookup(key="水头")).status == "NO_MATCH"
    result = service.read(read)
    assert result.items[0].status == "WITHDRAWN" and result.items[0].content is None
    with db.transaction() as c:
        receipt = c.execute(
            "SELECT body FROM receipts WHERE id=%s", (bundle.receipt_id,)
        ).fetchone()["body"]
    assert "content" not in receipt["items"][0]


@pytest.mark.parametrize("expiry,status", [("2000-01-01", "EXPIRED"), (None, "OK")])
def test_lifecycle_restore_and_expiry(db, embeddings, imported, expiry, status):
    catalog.update_lifecycle(
        db,
        imported,
        LifecycleUpdate(
            expected_revision=1,
            status="ACTIVE",
            valid_through=expiry,
            review_after=date(2000, 1, 1),
            reason="review",
        ),
    )
    service = EvidenceService(db, embeddings)
    with db.transaction() as c:
        checksum = c.execute(
            "SELECT checksum FROM units WHERE version_id=%s", (imported,)
        ).fetchone()["checksum"]
    result = service.read(
        EvidenceRead(
            refs=[EvidenceRef(version_id=imported, unit_id="text", checksum=checksum)]
        )
    )
    assert result.items[0].status == status
    if status == "OK":
        assert result.items[0].review_due


def test_required_context_is_never_truncated(db, package, embeddings):
    d = package.documents[0]
    d.units[0].required_units = ["units"]
    d.units.append(
        Unit(
            unit_id="units",
            content="单位：米；参考面固定。",
            source_refs=["manual"],
            required_units=["text"],
        )
    )
    submit(db, package)
    Importer(db, embeddings, Data()).run_once()
    service = EvidenceService(db, embeddings)
    small = service.lookup(Lookup(key="head", budget={"max_units": 1}))
    assert all(i.status == "NEEDS_EXPANSION" and i.content is None for i in small.items)
    full = service.lookup(Lookup(key="head"))
    assert len(full.items) == 2 and all(i.status == "OK" for i in full.items)


def test_lexical_works_without_provider_and_hybrid_fails(db, embeddings, imported):
    service = EvidenceService(db, embeddings)
    embeddings.hook = lambda: (_ for _ in ()).throw(
        UpstreamServiceError("EMBEDDING_PROVIDER_UNAVAILABLE")
    )
    assert service.retrieve(Retrieval(query="地下水水头", mode="lexical")).items
    with pytest.raises(UpstreamServiceError):
        service.retrieve(Retrieval(query="地下水水头"))


def test_api_boundaries_and_static_contract(db, embeddings, imported):
    with TestClient(create_app(db.settings, embeddings)) as client:
        assert (
            client.get(
                "/knowledge/v3/capabilities", headers={"X-Project-ID": "wrong"}
            ).status_code
            == 403
        )
        response = client.post(
            "/internal/tools/v5/lookup_knowledge",
            json={
                "callId": "a",
                "projectId": "local-data",
                "arguments": {"key": "head"},
            },
        )
        assert response.status_code == 200
        assert set(response.json()) == {"data", "outcome"}
        assert response.json()["data"]["items"][0]["content"]
        validate_camel_json(
            response.json(),
            opaque_keys=frozenset({"metadata", "provenance", "locator"}),
        )
        assert response.json()["data"]["coverage"]["retrievalExhaustive"] is False
        assert (
            client.post(
                "/knowledge/v3/retrievals",
                json={"query": "x", "budget": {"max_units": 100}},
            ).status_code
            == 422
        )
        assert client.post("/knowledge/v3/imports", json={}).status_code == 422
        assert (
            client.get("/knowledge/v3/tools").json()["contract"] == "knowledge-tools-v5"
        )
    contract = json.loads(
        files("platform_knowledge_next").joinpath("contracts/observation-tools.v5.json").read_text()
    )
    from jsonschema import Draft202012Validator

    Draft202012Validator(contract["tools"][2]["outputSchema"]).validate(
        response.json()["data"]
    )


def test_management_routes_require_operator_and_persist_actor(
    db, embeddings, imported, package
):
    trusted = replace(db.settings, identity_mode="gateway")
    user = {
        "X-Project-ID": db.settings.project,
        "X-Platform-Subject": "user@example.test",
        "X-Platform-Display-Name": "Project User",
        "X-Platform-Roles": "researcher",
    }
    operator = {
        **user,
        "X-Platform-Subject": "operator@example.test",
        "X-Platform-Display-Name": "Project Admin",
        "X-Platform-Roles": "admin",
        "X-Request-ID": "operator-request-1",
    }
    package.idempotency_key = "actor-audit"
    wire_package = package.model_dump(mode="json", by_alias=True)
    with TestClient(create_app(trusted, embeddings)) as client:
        assert client.post(
            "/knowledge/v3/imports", headers=user, json=wire_package
        ).status_code == 403
        accepted = client.post(
            "/knowledge/v3/imports", headers=operator, json=wire_package
        )
        assert accepted.status_code == 202, accepted.text
        assert accepted.json()["actor"] == {
            "subject": "operator@example.test",
            "displayName": "Project Admin",
            "roles": ["admin"],
            "traceId": accepted.headers["X-Trace-ID"],
            "requestId": "operator-request-1",
        }
        import_id = accepted.json()["importId"]
        assert client.get(
            f"/knowledge/v3/imports/{import_id}", headers=operator
        ).json()["actor"]["subject"] == "operator@example.test"

        lifecycle_body = {
            "expectedRevision": 1,
            "status": "WITHDRAWN",
            "reviewAfter": "2099-01-01",
            "reason": "operator review",
        }
        assert client.post(
            f"/knowledge/v3/versions/{imported}/lifecycle",
            headers=user,
            json=lifecycle_body,
        ).status_code == 403
        changed = client.post(
            f"/knowledge/v3/versions/{imported}/lifecycle",
            headers=operator,
            json=lifecycle_body,
        )
        assert changed.status_code == 200, changed.text
        assert changed.json()["lifecycleActor"]["subject"] == "operator@example.test"
        history = client.get(
            f"/knowledge/v3/versions/{imported}/lifecycle", headers=operator
        ).json()["items"]
        assert history[-1]["actor"]["requestId"] == "operator-request-1"

        batch_preflight_body = {
            "documentIds": ["head"],
            "status": "ACTIVE",
            "reviewAfter": "2099-06-01",
            "reason": "batch governance review",
        }
        assert client.post(
            "/knowledge/v3/lifecycle/batch/preflight",
            headers=user,
            json=batch_preflight_body,
        ).status_code == 403
        preflight = client.post(
            "/knowledge/v3/lifecycle/batch/preflight",
            headers=operator,
            json=batch_preflight_body,
        )
        assert preflight.status_code == 200, preflight.text
        batch_diff = preflight.json()["items"][0]
        assert batch_diff == {
            "documentId": "head",
            "title": "地下水水头",
            "versionId": imported,
            "currentRevision": 2,
            "currentStatus": "WITHDRAWN",
            "targetStatus": "ACTIVE",
            "currentValidThrough": None,
            "targetValidThrough": None,
            "currentReviewAfter": "2099-01-01",
            "targetReviewAfter": "2099-06-01",
            "changed": True,
        }
        batch_body = {
            "items": [
                {
                    "documentId": batch_diff["documentId"],
                    "versionId": batch_diff["versionId"],
                    "expectedRevision": batch_diff["currentRevision"],
                    "status": batch_diff["targetStatus"],
                    "reviewAfter": batch_diff["targetReviewAfter"],
                    "validThrough": batch_diff["targetValidThrough"],
                    "reason": batch_preflight_body["reason"],
                }
            ]
        }
        assert client.post(
            "/knowledge/v3/lifecycle/batch", headers=user, json=batch_body
        ).status_code == 403
        batch_changed = client.post(
            "/knowledge/v3/lifecycle/batch", headers=operator, json=batch_body
        )
        assert batch_changed.status_code == 200, batch_changed.text
        assert batch_changed.json()["items"][0]["lifecycleRevision"] == 3
        assert (
            batch_changed.json()["items"][0]["lifecycleActor"]["subject"]
            == "operator@example.test"
        )
        assert client.get("/knowledge/v3/activity", headers=user).status_code == 403
        activity = client.get("/knowledge/v3/activity", headers=operator)
        assert activity.status_code == 200, activity.text
        activity_items = activity.json()["items"]
        assert {item["service"] for item in activity_items} == {
            "knowledge"
        }
        assert {item["kind"] for item in activity_items} >= {
            "import",
            "lifecycle",
        }
        import_item = next(
            item for item in activity_items if item["operationId"] == import_id
        )
        assert import_item["href"] == (
            "/knowledge/?view=import&import=" + import_id
        )
        lifecycle_item = next(
            item
            for item in activity_items
            if item["kind"] == "lifecycle"
            and item["details"]["documentId"] == "head"
        )
        assert lifecycle_item["href"] == (
            f"/knowledge/?view=catalog&document=head&version={imported}"
        )


def test_lifecycle_batch_is_atomic_and_increments_meta_once(
    db, embeddings, imported, package
):
    extra = package.documents[0].model_copy(deep=True)
    extra.knowledge_id = "extra"
    extra.title = "第二份知识"
    package.idempotency_key = "batch-extra"
    package.documents = [extra]
    import_id = submit(db, package)["import_id"]
    Importer(db, embeddings, Data()).run_once()
    extra_version = job(db, import_id)["result"]["versions"][0]["version_id"]

    with TestClient(create_app(db.settings, embeddings)) as client:
        preflight_body = {
            "documentIds": ["head", "extra"],
            "status": "WITHDRAWN",
            "reviewAfter": "2099-12-31",
            "reason": "batch atomicity test",
        }
        preflight = client.post(
            "/knowledge/v3/lifecycle/batch/preflight", json=preflight_body
        )
        assert preflight.status_code == 200, preflight.text
        assert [item["versionId"] for item in preflight.json()["items"]] == [
            imported,
            extra_version,
        ]

        def request_body():
            return {
                "items": [
                    {
                        "documentId": item["documentId"],
                        "versionId": item["versionId"],
                        "expectedRevision": item["currentRevision"],
                        "status": item["targetStatus"],
                        "reviewAfter": item["targetReviewAfter"],
                        "validThrough": item["targetValidThrough"],
                        "reason": preflight_body["reason"],
                    }
                    for item in preflight.json()["items"]
                ]
            }

        stale = request_body()
        stale["items"][1]["expectedRevision"] = 99
        assert client.post(
            "/knowledge/v3/lifecycle/batch", json=stale
        ).status_code == 409
        with db.transaction() as connection:
            assert connection.execute(
                "SELECT count(*) AS count FROM lifecycle"
            ).fetchone()["count"] == 2
            revision_before = connection.execute(
                "SELECT revision FROM meta"
            ).fetchone()["revision"]

        changed = client.post(
            "/knowledge/v3/lifecycle/batch", json=request_body()
        )
        assert changed.status_code == 200, changed.text
        assert [item["lifecycleRevision"] for item in changed.json()["items"]] == [
            2,
            2,
        ]
        with db.transaction() as connection:
            assert connection.execute(
                "SELECT revision FROM meta"
            ).fetchone()["revision"] == revision_before + 1


def test_read_only_and_wrong_database_project(db, embeddings, package):
    readonly = Settings(db.settings.database_url, read_only=True)
    with TestClient(create_app(readonly, embeddings)) as client:
        assert (
            client.post(
                "/knowledge/v3/imports", json=package.model_dump(mode="json", by_alias=True)
            ).status_code
            == 403
        )
    with pytest.raises(KnowledgeProblemError, match="DATABASE_CONTRACT_MISMATCH"):
        Database(Settings(db.settings.database_url, project="wrong")).check()


def test_database_failure_rolls_back_whole_package(
    db, package, embeddings, monkeypatch
):
    second = package.documents[0].model_copy(deep=True)
    second.knowledge_id = "second"
    package.documents.append(second)
    original = catalog.project_search

    def fail_second(c, vid, document, lifecycle):
        if document.knowledge_id == "second":
            raise RuntimeError("injected transaction failure")
        return original(c, vid, document, lifecycle)

    monkeypatch.setattr(catalog, "project_search", fail_second)
    jid = submit(db, package)["import_id"]
    with pytest.raises(RuntimeError):
        Importer(db, embeddings, Data()).run_once()
    assert job(db, jid)["error"]["code"] == "INTERNAL_ERROR"
    with db.transaction() as c:
        assert c.execute("SELECT count(*) AS n FROM documents").fetchone()["n"] == 0
        assert (
            c.execute("SELECT count(*) AS n FROM search_entries").fetchone()["n"] == 0
        )


def test_withdrawal_during_embedding_is_observed(db, embeddings, imported):
    def withdraw():
        catalog.update_lifecycle(
            db,
            imported,
            LifecycleUpdate(
                expected_revision=1,
                status="WITHDRAWN",
                review_after="2099-01-01",
                reason="concurrent withdrawal",
            ),
        )

    embeddings.hook = withdraw
    result = EvidenceService(db, embeddings).retrieve(Retrieval(query="地下水"))
    assert result.status == "NO_MATCH" and result.items == []


def test_binding_filter_never_uses_latest_or_matching_field_name(
    db, package, embeddings
):
    from platform_knowledge_next.models import DataBinding

    binding = DataBinding(
        project_id="local-data",
        dataset_id="d",
        version_id="v1",
        semantic_id="s1",
        field_id="f0",
    )
    package.documents[0].bindings = [binding]
    submit(db, package)
    Importer(db, embeddings, Data()).run_once()
    service = EvidenceService(db, embeddings)
    assert service.lookup(Lookup(binding=binding)).items[0].bindings_verified_at
    assert (
        service.lookup(
            Lookup(binding=binding.model_copy(update={"semantic_id": "s2"}))
        ).status
        == "NO_MATCH"
    )
    assert (
        service.lookup(
            Lookup(binding=binding.model_copy(update={"version_id": "v2"}))
        ).status
        == "NO_MATCH"
    )


def test_binding_failure_does_not_publish(db, package, embeddings):
    from platform_knowledge_next.models import DataBinding

    package.documents[0].bindings = [
        DataBinding(
            project_id="local-data", dataset_id="d", version_id="v1", semantic_id="s1"
        )
    ]

    class MissingBinding(Data):
        def validate(self, binding):
            raise KnowledgeProblemError("DATA_BINDING_NOT_FOUND", 422)

    jid = submit(db, package)["import_id"]
    Importer(db, embeddings, MissingBinding()).run_once()
    assert job(db, jid)["error"]["details"]["knowledge_id"] == "head"
    assert embeddings.calls == []


def test_hnsw_and_exact_only_return_matching_binding(db, package, embeddings):
    from dataclasses import replace
    from platform_knowledge_next.models import DataBinding

    binding = DataBinding(
        project_id="local-data", dataset_id="d", version_id="v1", semantic_id="s1"
    )
    package.documents[0].bindings = [binding]
    submit(db, package)
    Importer(db, embeddings, Data()).run_once()
    for mode in ("exact", "hnsw"):
        service = EvidenceService(
            Database(replace(db.settings, vector_search=mode)), embeddings
        )
        assert service.retrieve(Retrieval(query="地下水", binding=binding)).items
        assert (
            service.retrieve(
                Retrieval(
                    query="地下水",
                    binding=binding.model_copy(update={"semantic_id": "s2"}),
                )
            ).status
            == "NO_MATCH"
        )
