"""Cross-service assertions, log diagnostics, and bounded teardown."""

from __future__ import annotations
import re
import time
from pathlib import Path
import httpx
from .context import Recorder
from .environment import _wait_http_ready

_LOG_FINDING_PATTERNS = (
    (re.compile(r"(?m)^.*FATAL:.*$"), "UNEXPECTED_FATAL"),
    (re.compile(r"(?m)^Traceback \(most recent call last\):$"), "TRACEBACK"),
    (re.compile(r"(?mi)^.*Unhandled exception.*$"), "UNHANDLED_EXCEPTION"),
    (re.compile(r"(?m)^.*\[(?:error|crit|alert|emerg)\].*$"), "NGINX_ERROR"),
)


def _poll_import(
    client: httpx.Client,
    import_id: str,
    expected: set[str],
    *,
    timeout: float = 90,
) -> dict:
    deadline = time.monotonic() + timeout
    last: dict = {}
    while time.monotonic() < deadline:
        response = client.get(f"/knowledge/v3/imports/{import_id}")
        response.raise_for_status()
        last = response.json()
        if last["state"] in expected:
            return last
        time.sleep(0.25)
    raise AssertionError(f"KNOWLEDGE_IMPORT_TIMEOUT:{import_id}:{last.get('state')}")


def _knowledge_document(
    knowledge_id: str,
    content: str,
    binding: dict[str, object] | None = None,
) -> dict[str, object]:
    return {
        "knowledgeId": knowledge_id,
        "title": knowledge_id,
        "contentVersion": "1",
        "bindings": [] if binding is None else [binding],
        "sources": [
            {
                "sourceId": "joint-test",
                "kind": "DEMO_ASSUMPTION",
                "title": "Joint verification fixture",
                "publisher": "Data Knowledge joint gate",
                "reviewAfter": "2099-01-01",
            }
        ],
        "units": [
            {
                "unitId": "text",
                "content": content,
                "sourceRefs": ["joint-test"],
            }
        ],
    }


def _submit_import(
    client: httpx.Client,
    project_id: str,
    key: str,
    documents: list[dict[str, object]],
) -> str:
    response = client.post(
        "/knowledge/v3/imports",
        json={
            "schemaVersion": "knowledge-package.v1",
            "projectId": project_id,
            "idempotencyKey": key,
            "documents": documents,
        },
    )
    response.raise_for_status()
    return str(response.json()["importId"])


def _knowledge_ids(bundle: dict) -> set[str]:
    return {
        str(item["knowledgeId"])
        for item in bundle["items"]
        if item.get("knowledgeId") is not None
    }


def _retrieve(
    client: httpx.Client,
    token: str,
    *,
    scope: str | None = None,
    binding: dict[str, object] | None = None,
) -> dict:
    body: dict[str, object] = {"query": token, "mode": "lexical"}
    if scope is not None:
        body["scope"] = scope
    if binding is not None:
        body["binding"] = binding
    response = client.post("/knowledge/v3/retrievals", json=body)
    response.raise_for_status()
    return response.json()


def _joint_http_checks(
    recorder: Recorder,
    *,
    data_url: str,
    knowledge_url: str,
    project_id: str,
    data_compose: list[str],
    data_env: dict[str, str],
    run_id: str,
) -> dict[str, object]:
    data = httpx.Client(base_url=data_url, timeout=30, trust_env=False)
    knowledge = httpx.Client(base_url=knowledge_url, timeout=30, trust_env=False)
    try:
        catalog = data.get("/data/v3/datasets?limit=100")
        catalog.raise_for_status()
        datasets = catalog.json()["items"]
        if not datasets:
            raise AssertionError("DATA_UI_DID_NOT_CREATE_DATASET")
        dataset = datasets[0]
        version_response = data.get(f"/data/v3/versions/{dataset['currentVersion']}")
        version_response.raise_for_status()
        version = version_response.json()
        field_id = version["descriptor"]["fields"][0]["fieldId"]
        first_semantics = data.post(
            f"/data/v3/versions/{version['versionId']}/semantics",
            json={
                "expectedSemanticId": version["semanticId"],
                "semantics": {
                    "description": "Joint verification governed semantics",
                    "fields": {
                        field_id: {
                            "meaning": "Joint verification identifier",
                            "unit": "1",
                        }
                    },
                    "limitations": ["Synthetic joint verification fixture"],
                },
            },
        )
        first_semantics.raise_for_status()
        historical_semantic_id = first_semantics.json()["semanticId"]
        current_semantics = data.post(
            f"/data/v3/versions/{version['versionId']}/semantics",
            json={
                "expectedSemanticId": historical_semantic_id,
                "semantics": {
                    "description": "Joint verification current semantics",
                    "fields": {
                        field_id: {
                            "meaning": "Joint verification identifier",
                            "unit": "1",
                        }
                    },
                    "limitations": ["Synthetic joint verification fixture"],
                },
            },
        )
        current_semantics.raise_for_status()
        current_semantic_id = current_semantics.json()["semanticId"]
        binding: dict[str, object] = {
            "serviceBinding": "data-primary",
            "projectId": dataset["project"],
            "datasetId": dataset["datasetId"],
            "versionId": version["versionId"],
            "semanticId": historical_semantic_id,
            "fieldId": field_id,
        }
        token = f"jointtoken{run_id}"
        general_id = f"joint-general-{run_id}"
        bound_id = f"joint-bound-{run_id}"
        import_id = _submit_import(
            knowledge,
            project_id,
            f"joint-scope-{run_id}",
            [
                _knowledge_document(general_id, f"{token} general"),
                _knowledge_document(bound_id, f"{token} bound", binding),
            ],
        )
        completed = _poll_import(knowledge, import_id, {"SUCCEEDED", "FAILED"})
        assert completed["state"] == "SUCCEEDED", completed

        general = _retrieve(knowledge, token, scope="general")
        default_general = _retrieve(knowledge, token)
        bound = _retrieve(knowledge, token, scope="bound", binding=binding)
        combined = _retrieve(knowledge, token, scope="combined", binding=binding)
        default_combined = _retrieve(knowledge, token, binding=binding)
        assert _knowledge_ids(general) == {general_id}
        assert _knowledge_ids(default_general) == {general_id}
        assert _knowledge_ids(bound) == {bound_id}
        assert _knowledge_ids(combined) == {general_id, bound_id}
        assert _knowledge_ids(default_combined) == {general_id, bound_id}
        mismatch = dict(binding)
        mismatch["semanticId"] = "different-semantic"
        no_match = _retrieve(knowledge, token, scope="bound", binding=mismatch)
        assert no_match["status"] == "NO_MATCH" and not no_match["items"]

        missing = dict(binding)
        missing["versionId"] = "missing-version"
        missing_id = _submit_import(
            knowledge,
            project_id,
            f"joint-missing-{run_id}",
            [
                _knowledge_document(
                    f"joint-missing-{run_id}", f"{token} missing", missing
                )
            ],
        )
        permanent = _poll_import(knowledge, missing_id, {"SUCCEEDED", "FAILED"})
        assert permanent["state"] == "FAILED", permanent
        assert permanent["error"]["code"] == "DATA_BINDING_NOT_FOUND", permanent

        before_ids = {item["datasetId"] for item in datasets}
        recorder.run([*data_compose, "stop", "api"], env=data_env)
        try:
            retry_id = _submit_import(
                knowledge,
                project_id,
                f"joint-retry-{run_id}",
                [
                    _knowledge_document(
                        f"joint-retry-{run_id}", f"{token} retry", binding
                    )
                ],
            )
            waiting = _poll_import(knowledge, retry_id, {"RETRY_WAIT", "FAILED"})
            assert waiting["state"] == "RETRY_WAIT", waiting
        finally:
            recorder.run([*data_compose, "restart", "api"], env=data_env)
        recovered = _poll_import(
            knowledge, retry_id, {"SUCCEEDED", "FAILED"}, timeout=180
        )
        assert recovered["state"] == "SUCCEEDED", recovered
        _wait_http_ready(data)
        after = data.get("/data/v3/datasets?limit=100")
        after.raise_for_status()
        assert {item["datasetId"] for item in after.json()["items"]} == before_ids
        return {
            "status": "PASS",
            "datasetId": dataset["datasetId"],
            "versionId": version["versionId"],
            "bindingSemanticId": historical_semantic_id,
            "currentSemanticId": current_semantic_id,
            "fieldId": field_id,
            "scopeImportId": import_id,
            "permanentFailureImportId": missing_id,
            "retryImportId": retry_id,
            "retryAttempts": recovered["dataAttempts"],
            "knowledgeDoesNotWriteData": True,
        }
    finally:
        data.close()
        knowledge.close()


def _has_nearby_log_line(
    text: str,
    match: re.Match[str],
    needle: str,
    *,
    before: int = 0,
    after: int = 0,
    same_stream: bool = False,
) -> bool:
    """Match an expected lifecycle line near one log finding only.

    A global substring check would let an unrelated later ``CREATE DATABASE``
    or shutdown sequence hide a fatal error.  Restricting the context to the
    adjacent log lines keeps the exception specific to the bootstrap event.
    """
    lines = text.splitlines()
    index = text.count("\n", 0, match.start())
    start = max(0, index - before)
    stop = min(len(lines), index + after + 1)
    positions = [position for position in range(start, stop) if position != index]
    if same_stream and " | " in lines[index]:
        stream = lines[index].split(" | ", 1)[0]
        # Compose can interleave other services between adjacent lines from
        # one container.  Keep both bounds: at most 24 physical lines and at
        # most ``before``/``after`` lines from the matching container.
        physical_start = max(0, index - 24)
        physical_stop = min(len(lines), index + 25)
        preceding = [
            position
            for position in range(physical_start, index)
            if lines[position].split(" | ", 1)[0] == stream
        ]
        following = [
            position
            for position in range(index + 1, physical_stop)
            if lines[position].split(" | ", 1)[0] == stream
        ]
        positions = preceding[-before:] if before else []
        if after:
            positions.extend(following[:after])
    return any(needle in lines[position] for position in positions)


def _scan_log(text: str) -> list[dict[str, object]]:
    """Return bounded, stable findings from service logs.

    The joint gate must not report PASS while a service emitted a fatal
    runtime error.  We intentionally retain only the code and line number;
    full log text remains in the run-scoped artifact for diagnosis.
    """
    findings: list[dict[str, object]] = []
    for pattern, code in _LOG_FINDING_PATTERNS:
        for match in pattern.finditer(text):
            # PostgreSQL probes its configured database once before the
            # entrypoint creates it during first initialisation.  This exact
            # one-line FATAL is expected test-container bootstrap noise; a
            # FATAL after the database exists must still fail the gate.
            if (
                code == "UNEXPECTED_FATAL"
                and 'FATAL:  database "knowledge" does not exist' in match.group(0)
                and _has_nearby_log_line(
                    text, match, "CREATE DATABASE", after=3, same_stream=True
                )
            ):
                continue
            if (
                code == "UNEXPECTED_FATAL"
                and 'FATAL:  database "data" does not exist' in match.group(0)
                and _has_nearby_log_line(
                    text, match, "CREATE DATABASE", after=3, same_stream=True
                )
            ):
                # The Data image has the same first-initialisation race as
                # Knowledge: a probe can hit the default server before the
                # configured application database is created.
                continue
            if (
                code == "UNEXPECTED_FATAL"
                and "FATAL:  the database system is shutting down" in match.group(0)
                and _has_nearby_log_line(
                    text,
                    match,
                    "received fast shutdown request",
                    before=8,
                    same_stream=True,
                )
                and _has_nearby_log_line(
                    text,
                    match,
                    "database system is shut down",
                    after=12,
                    same_stream=True,
                )
            ):
                # The joint gate deliberately restarts a service while a
                # database health probe is in flight. PostgreSQL reports this
                # transient FATAL during the controlled shutdown; any FATAL
                # without the full shutdown sequence remains a failure.
                continue
            findings.append(
                {"code": code, "line": text.count("\n", 0, match.start()) + 1}
            )
            if len(findings) >= 32:
                return findings
    return findings


def _capture_logs(
    recorder: Recorder,
    work: Path,
    name: str,
    command: list[str],
    env: dict[str, str] | None = None,
) -> dict[str, object]:
    result = recorder.run(
        command, env=env, capture=True, check=False
    )
    path = work / f"{name}.log"
    path.write_text(result.stdout + result.stderr, encoding="utf-8")
    findings = _scan_log(result.stdout + result.stderr)
    return {
        "path": str(path),
        "status": "PASS" if result.returncode == 0 else "UNAVAILABLE",
        "returnCode": result.returncode,
        "findings": findings,
    }


def _cleanup(
    recorder: Recorder,
    command: list[str],
    *,
    env: dict[str, str] | None = None,
) -> dict[str, object]:
    result = recorder.run(command, env=env, capture=True, check=False, phase="teardown")
    output = (result.stdout or "") + (result.stderr or "")
    expected_missing = result.returncode != 0 and any(
        marker in output.lower()
        for marker in ("no such container", "no such network", "no such image")
    )
    return {
        "command": command,
        "returnCode": result.returncode,
        "status": "PASS" if result.returncode == 0 or expected_missing else "FAIL",
        "expectedMissingResource": expected_missing,
    }
