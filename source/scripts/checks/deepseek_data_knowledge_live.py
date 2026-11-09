"""Opt-in DeepSeek smoke against isolated Data and Knowledge service APIs.

The service records and prompts are synthetic. The SDK owns the model/tool loop;
only read-only tool results leave the local test services. No credentials or
request bodies are written to the report.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
from contextlib import ExitStack
from decimal import Decimal
from importlib.metadata import version
from pathlib import Path
import subprocess
import tempfile
import time
from uuid import uuid4

from agents import (
    Agent,
    ModelSettings,
    RunConfig,
    Runner,
    function_tool,
    set_trace_provider,
)
from agents.models.openai_chatcompletions import OpenAIChatCompletionsModel
from agents.tracing.provider import DefaultTraceProvider
from fastapi.testclient import TestClient
import httpx
from openai import AsyncOpenAI
import psycopg
from psycopg.conninfo import make_conninfo

from platform_data_service.adapters.database import Database as DataDatabase
from platform_data_service.api.app import create_app as create_data_app
from platform_data_service.operations.worker import Worker as DataWorker
from platform_data_service.settings import Settings as DataSettings
from platform_knowledge_next.api import create_app as create_knowledge_app
from platform_knowledge_next.config import Settings as KnowledgeSettings
from platform_knowledge_next.database import Database as KnowledgeDatabase
from platform_knowledge_next.ingestion import Importer, submit
from platform_knowledge_next.models import KnowledgePackageV1


MODEL = "deepseek-flash"
IMAGE = (
    "pgvector/pgvector:0.8.7-pg16-bookworm@"
    "sha256:7b822b0aac60967beb1ea5e576b8602c94c300a157d187f385ae3e0da199b90a"
)
ROOT = Path(__file__).resolve().parents[2]
DOCKER = ROOT.parent / "bin/docker"
INPUT_PRICE = Decimal("0.30")
OUTPUT_PRICE = Decimal("1.20")
MAX_REQUESTS = 7
RESERVE = (32768 * INPUT_PRICE + 256 * OUTPUT_PRICE) / 1_000_000
MAX_COST = Decimal("0.10")


def _disable_sdk_trace_export() -> DefaultTraceProvider:
    """Install a trace provider with no ambient exporter or network client."""

    provider = DefaultTraceProvider()
    provider.set_disabled(True)
    set_trace_provider(provider)
    return provider


class SyntheticEmbeddings:
    """Deterministic fixture for ingestion; no external Embedding requests."""

    def embed(self, texts):
        vectors = []
        for text in texts:
            values = [0.0] * 1024
            for character in text:
                index = (
                    int(hashlib.sha256(character.encode()).hexdigest()[:8], 16) % 1024
                )
                values[index] += 1
            norm = sum(value * value for value in values) ** 0.5
            vectors.append(tuple(value / norm for value in values))
        return tuple(vectors)

    def ready(self):
        return None

    def close(self):
        return None


class NoDataBinding:
    def begin_attempt(self):
        return None

    def validate(self, _binding):
        return None


class BudgetTransport(httpx.AsyncBaseTransport):
    def __init__(self, receipt_root: Path):
        self.delegate = httpx.AsyncHTTPTransport(retries=0)
        self.receipt_root = receipt_root
        self.attempts = 0
        self.usage: list[dict[str, int | str]] = []
        self.provider_errors: list[dict[str, object]] = []
        self.responses: list[dict[str, object]] = []

    async def handle_async_request(self, request):
        payload = json.loads(request.content)
        if (
            request.method != "POST"
            or request.url.scheme != "https"
            or request.url.host != "api.deepseek.com"
            or request.url.path not in {"/chat/completions", "/v1/chat/completions"}
            or payload.get("model") != MODEL
            or payload.get("max_tokens") != 256
            or payload.get("thinking") != {"type": "disabled"}
            or len(request.content) > 16384
        ):
            raise RuntimeError("LIVE_REQUEST_OUTSIDE_SCOPE")
        if self.attempts >= MAX_REQUESTS or (self.attempts + 1) * RESERVE > MAX_COST:
            raise RuntimeError("LIVE_REQUEST_BUDGET_EXHAUSTED")
        # Count before dispatch: an ambiguous timeout still consumes reservation.
        self.attempts += 1
        (self.receipt_root / f"attempt-{self.attempts}.json").write_text(
            json.dumps({"attempt": self.attempts, "reserved_usd": str(RESERVE)}) + "\n",
            encoding="utf-8",
        )
        started = time.perf_counter()
        try:
            response = await self.delegate.handle_async_request(request)
            await response.aread()
        except Exception as error:
            self.responses.append(
                {
                    "attempt": self.attempts,
                    "http_status": None,
                    "latency_ms": round((time.perf_counter() - started) * 1000, 1),
                    "return_kind": "transport_error",
                    "error_type": type(error).__name__,
                }
            )
            raise
        try:
            response_body = json.loads(response.content)
        except (TypeError, ValueError):
            response_body = {}
        choices = response_body.get("choices", [])
        first_choice = choices[0] if choices and isinstance(choices[0], dict) else {}
        message = first_choice.get("message", {})
        if not isinstance(message, dict):
            message = {}
        tool_calls = message.get("tool_calls")
        tool_call_count = len(tool_calls) if isinstance(tool_calls, list) else 0
        self.responses.append(
            {
                "attempt": self.attempts,
                "http_status": response.status_code,
                "latency_ms": round((time.perf_counter() - started) * 1000, 1),
                "return_kind": (
                    "tool_calls"
                    if tool_call_count
                    else "final_text"
                    if isinstance(message.get("content"), str)
                    else "none"
                ),
                "tool_call_count": tool_call_count,
                "finish_reason": first_choice.get("finish_reason")
                if first_choice.get("finish_reason")
                in {"stop", "tool_calls", "length", "content_filter"}
                else None,
            }
        )
        if not response.is_success:
            try:
                detail = response_body.get("error", {})
                message = str(detail.get("message", "")).lower()
                self.provider_errors.append(
                    {
                        "status": response.status_code,
                        "type": detail.get("type"),
                        "code": detail.get("code"),
                        "hints": [
                            term
                            for term in (
                                "strict",
                                "json_schema",
                                "response_format",
                                "tool_choice",
                            )
                            if term in message
                        ],
                    }
                )
            except (ValueError, AttributeError):
                self.provider_errors.append({"status": response.status_code})
        if response.is_success:
            usage = response_body.get("usage", {})
            prompt = usage.get("prompt_tokens")
            completion = usage.get("completion_tokens")
            if (
                not isinstance(prompt, int)
                or not isinstance(completion, int)
                or not 0 <= prompt <= 32768
                or not 0 <= completion <= 256
            ):
                raise RuntimeError("LIVE_USAGE_CANNOT_BE_BOUNDED")
            self.usage.append(
                {
                    "input_tokens": prompt,
                    "output_tokens": completion,
                    "upper_cost_usd": str(
                        (prompt * INPUT_PRICE + completion * OUTPUT_PRICE) / 1_000_000
                    ),
                }
            )
        return response

    async def aclose(self):
        await self.delegate.aclose()


def _docker(*args: str, timeout: int = 30) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(DOCKER), *args],
        capture_output=True,
        text=True,
        check=True,
        timeout=timeout,
    )


def _start_database() -> tuple[str, str]:
    name = "codex-dk-live-" + uuid4().hex[:12]
    _docker(
        "run",
        "--rm",
        "-d",
        "--name",
        name,
        "-e",
        "POSTGRES_PASSWORD=synthetic-test-only",
        "-p",
        "127.0.0.1::5432",
        IMAGE,
    )
    try:
        for _ in range(100):
            ready = subprocess.run(
                [str(DOCKER), "exec", name, "pg_isready", "-U", "postgres"],
                capture_output=True,
                timeout=5,
                check=False,
            )
            if ready.returncode == 0:
                break
            time.sleep(0.2)
        else:
            raise RuntimeError("ISOLATED_DATABASE_NOT_READY")
        binding = _docker("port", name, "5432/tcp").stdout.strip()
        port = binding.rsplit(":", 1)[-1]
        if not port.isdecimal():
            raise RuntimeError("ISOLATED_DATABASE_PORT_INVALID")
        dsn = f"postgresql://postgres:synthetic-test-only@127.0.0.1:{port}/postgres"
        # pg_isready inside the container can precede host port forwarding.
        for _ in range(50):
            try:
                with psycopg.connect(dsn, connect_timeout=1):
                    return name, dsn
            except psycopg.OperationalError:
                time.sleep(0.2)
        raise RuntimeError("ISOLATED_DATABASE_CONNECTION_NOT_READY")
    except BaseException:
        _docker("stop", name)
        raise


def _seed_data(dsn: str, root: Path) -> TestClient:
    settings = DataSettings(dsn, root)
    DataDatabase(dsn).migrate()
    client = TestClient(create_data_app(settings))
    client.__enter__()
    try:
        response = client.post(
            "/data/v3/publications",
            json={
                "idempotencyKey": "deepseek-data-smoke",
                "outputs": [
                    {
                        "name": "table",
                        "title": "Synthetic wells",
                        "format": "csv",
                        "policy": "FILE_QUERYABLE",
                    }
                ],
            },
        )
        assert response.status_code == 201
        publication = response.json()["publicationId"]
        content = b"well,value\nW01,2\n"
        response = client.put(
            f"/data/v3/publications/{publication}/items/table",
            content=content,
            headers={
                "X-Content-SHA256": "sha256:" + hashlib.sha256(content).hexdigest()
            },
        )
        assert response.status_code == 200
        assert (
            client.post(f"/data/v3/publications/{publication}/prepare").status_code
            == 202
        )
        DataWorker(settings).once()
        assert (
            client.post(f"/data/v3/publications/{publication}/commit").status_code
            == 200
        )
        inventory = client.get("/data/v3/inventory")
        assert inventory.status_code == 200 and inventory.json()["datasetCount"] == 1
        return client
    except BaseException:
        client.__exit__(None, None, None)
        raise


def _seed_knowledge(dsn: str, embeddings: SyntheticEmbeddings) -> TestClient:
    settings = KnowledgeSettings(dsn)
    db = KnowledgeDatabase(settings)
    db.migrate()
    package = KnowledgePackageV1.model_validate(
        {
            "project_id": "local-data",
            "idempotency_key": "deepseek-knowledge-smoke",
            "documents": [
                {
                    "knowledge_id": "aquifer-head",
                    "title": "Aquifer head",
                    "content_version": "1.0.0",
                    "aliases": ["head"],
                    "sources": [
                        {
                            "source_id": "synthetic-manual",
                            "kind": "DOMAIN_DEFINITION",
                            "title": "Synthetic test manual",
                            "publisher": "Synthetic test",
                            "review_after": "2099-01-01",
                        }
                    ],
                    "units": [
                        {
                            "unit_id": "definition",
                            "content": "Aquifer head is measured in metres.",
                            "source_refs": ["synthetic-manual"],
                        }
                    ],
                }
            ],
        }
    )
    submit(db, package)
    assert Importer(db, embeddings, NoDataBinding()).run_once()
    client = TestClient(create_knowledge_app(settings, embeddings))
    client.__enter__()
    try:
        response = client.post("/knowledge/v3/lookup", json={"key": "head"})
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "OK"
        assert body["items"][0]["content"] == "Aquifer head is measured in metres."
        return client
    except BaseException:
        client.__exit__(None, None, None)
        raise


async def _evaluate(
    data: TestClient, knowledge: TestClient, receipt_root: Path
) -> dict[str, object]:
    _disable_sdk_trace_export()
    transport = BudgetTransport(receipt_root)
    results: list[dict[str, object]] = []
    async with httpx.AsyncClient(
        transport=transport, timeout=30, trust_env=False, follow_redirects=False
    ) as http:
        client = AsyncOpenAI(
            api_key=os.environ["DEEPSEEK_API_KEY"],
            base_url="https://api.deepseek.com",
            max_retries=0,
            http_client=http,
        )
        model = OpenAIChatCompletionsModel(MODEL, openai_client=client)
        settings = ModelSettings(
            tool_choice="required",
            parallel_tool_calls=False,
            max_tokens=256,
            timeout=30,
            extra_body={"thinking": {"type": "disabled"}},
        )
        calls: list[str] = []

        @function_tool(strict_mode=False)
        def data_inventory() -> str:
            """Read the number of synthetic datasets from the isolated Data service."""
            calls.append("data_inventory")
            response = data.get("/data/v3/inventory")
            response.raise_for_status()
            return str(response.json()["datasetCount"])

        @function_tool(strict_mode=False)
        def knowledge_lookup(key: str) -> str:
            """Look up the exact synthetic knowledge term by key."""
            calls.append("knowledge_lookup:" + key)
            response = knowledge.post("/knowledge/v3/lookup", json={"key": key})
            response.raise_for_status()
            body = response.json()
            return body["items"][0]["content"] if body["status"] == "OK" else "NO_MATCH"

        for case, tools, prompt, expected, expected_calls in (
            (
                "data",
                [data_inventory],
                "Call data_inventory once. Return only its decimal dataset count.",
                "1",
                ["data_inventory"],
            ),
            (
                "knowledge",
                [knowledge_lookup],
                "Call knowledge_lookup once with key head. Return only the exact content.",
                "Aquifer head is measured in metres.",
                ["knowledge_lookup:head"],
            ),
            (
                "data_knowledge_combined",
                [data_inventory, knowledge_lookup],
                "Call data_inventory once and knowledge_lookup once with key head. "
                "Return only the dataset count, then a vertical bar, then the exact "
                "knowledge content, in this format: "
                "1 | Aquifer head is measured in metres.",
                "1 | Aquifer head is measured in metres.",
                ["data_inventory", "knowledge_lookup:head"],
            ),
        ):
            calls.clear()
            started = time.perf_counter()
            response_start = len(transport.responses)
            failure = None
            answer = None
            try:
                agent = Agent(
                    name="Synthetic " + case + " reader",
                    instructions="Use the available read-only tool. Return only its requested result.",
                    model=model,
                    model_settings=settings,
                    tools=tools,
                )
                run = await Runner.run(
                    agent,
                    prompt,
                    max_turns=3,
                    run_config=RunConfig(tracing_disabled=True),
                )
                answer = (
                    run.final_output.strip()
                    if isinstance(run.final_output, str)
                    else None
                )
            except Exception as error:
                failure = type(error).__name__
            results.append(
                {
                    "case": case,
                    "result": "PASS"
                    if answer == expected and sorted(calls) == sorted(expected_calls)
                    else "FAIL",
                    "tool_calls": list(calls),
                    "answer_exact": answer == expected,
                    "error_type": failure,
                    "latency_ms": round((time.perf_counter() - started) * 1000, 1),
                    "responses": transport.responses[response_start:],
                }
            )
    return {
        "schema_version": "deepseek-data-knowledge-live-v1",
        "result": "PASS"
        if all(item["result"] == "PASS" for item in results)
        else "FAIL",
        "provider": "DeepSeek",
        "model": MODEL,
        "pricing_source": "https://api-docs.deepseek.com/quick_start/pricing/",
        "sdk_versions": {
            name: version(name) for name in ("openai-agents", "openai", "httpx")
        },
        "max_requests": MAX_REQUESTS,
        "max_cost_usd": str(MAX_COST),
        "transport_attempts": transport.attempts,
        "reserved_cost_usd": str(transport.attempts * RESERVE),
        "usage": transport.usage,
        "responses": transport.responses,
        "provider_errors": transport.provider_errors,
        "results": results,
        "limitations": [
            "synthetic data and deterministic embedding fixture",
            "in-process service APIs, not the unified Gateway or full Compose",
            "read-only model tools; no service write authorization tested by model",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--allow-model-requests", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not args.allow_model_requests or not os.environ.get("DEEPSEEK_API_KEY"):
        raise SystemExit("Explicit authorization and DEEPSEEK_API_KEY are required")
    if args.output.exists():
        raise SystemExit("Output path must not exist")
    receipt_root = args.output.with_suffix(args.output.suffix + ".attempts")
    receipt_root.mkdir(exist_ok=False)
    container = None
    stage = "start_database"
    report: dict[str, object]
    try:
        container, admin_dsn = _start_database()
        with tempfile.TemporaryDirectory(
            prefix="deepseek-data-knowledge-"
        ) as temporary:
            stage = "create_databases"
            with psycopg.connect(admin_dsn, autocommit=True) as connection:
                connection.execute("CREATE DATABASE data_live_test")
                connection.execute("CREATE DATABASE knowledge_live_test")
            data_dsn = make_conninfo(admin_dsn, dbname="data_live_test")
            knowledge_dsn = make_conninfo(admin_dsn, dbname="knowledge_live_test")
            embeddings = SyntheticEmbeddings()
            with ExitStack() as stack:
                stage = "seed_data"
                data = _seed_data(data_dsn, Path(temporary) / "data")
                stack.callback(data.__exit__, None, None, None)
                stage = "seed_knowledge"
                knowledge = _seed_knowledge(knowledge_dsn, embeddings)
                stack.callback(knowledge.__exit__, None, None, None)
                stage = "model_evaluation"
                report = asyncio.run(_evaluate(data, knowledge, receipt_root))
    except Exception as error:
        report = {
            "schema_version": "deepseek-data-knowledge-live-v1",
            "result": "FAIL",
            "stage": stage,
            "error_type": type(error).__name__,
        }
    finally:
        cleanup_ok = True
        if container is not None:
            try:
                _docker("stop", container)
            except (OSError, subprocess.SubprocessError):
                cleanup_ok = False
    report["isolated_database_stopped"] = cleanup_ok
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report))
    return 0 if report["result"] == "PASS" and cleanup_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
