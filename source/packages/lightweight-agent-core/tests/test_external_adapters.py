from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

import pytest
from platform_lightweight_agent import (
    ContainerResourceLimits,
    ContainerToolDefinition,
    ContainerToolExecutor,
    InMemoryRunJournal,
    McpToolDefinition,
    McpToolExecutor,
    RemoteHttpTool,
    RemoteHttpToolExecutor,
    ToolExecutionFailedError,
    ToolExecutionRejectedError,
    ToolExecutionRequest,
    ToolExecutionStoppedError,
    ToolReference,
    WorkflowEngine,
    WorkflowValidationError,
    tool_step,
    workflow,
)
from platform_lightweight_agent.engine import ToolExecutionUncertainError
from platform_lightweight_agent.journal import RunConflictError
from platform_lightweight_agent.spec import canonical_json

REMOTE_TOOL = ToolReference("remote.echo", "1", "sha256:" + "a" * 64)
MCP_TOOL = ToolReference("mcp.echo", "1", "sha256:" + "b" * 64)
CONTAINER_TOOL = ToolReference("container.echo", "1", "sha256:" + "c" * 64)


class _RemoteState:
    mode = "success"

    def __init__(self) -> None:
        self.posts: list[tuple[dict[str, object], dict[str, str]]] = []
        self.gets: list[str] = []


@contextmanager
def _remote_server() -> Iterator[tuple[_RemoteState, str]]:
    state = _RemoteState()

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            raw = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            state.posts.append((json.loads(raw), dict(self.headers.items())))
            if state.mode == "uncertain":
                self.send_response(503)
                self.end_headers()
                return
            response = (
                b'{"output":{"value":NaN}}'
                if state.mode == "nan"
                else b'{"output":'
                if state.mode == "malformed"
                else json.dumps({"output": {"echo": "remote"}}).encode()
            )
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(response)))
            self.end_headers()
            self.wfile.write(response)

        def do_GET(self) -> None:
            state.gets.append(self.path)
            if state.mode != "recovered":
                self.send_response(202)
                self.end_headers()
                return
            response = json.dumps(
                {"status": "succeeded", "output": {"recovered": True}}
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(response)))
            self.end_headers()
            self.wfile.write(response)

        def log_message(self, *_: object) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield state, f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()


def test_remote_http_adapter_uses_registered_endpoint_and_stable_idempotency_key() -> (
    None
):
    with _remote_server() as (state, origin):
        journal = InMemoryRunJournal()
        executor = RemoteHttpToolExecutor(lambda reference: "credential-value")
        executor.register(
            RemoteHttpTool(
                REMOTE_TOOL,
                f"{origin}/execute",
                credential_ref="service-credential",
                reconciliation_endpoint=f"{origin}/result",
            )
        )
        binding = executor.execution_binding(REMOTE_TOOL)
        assert binding == {
            "adapter": "remote-http-v1",
            "service_id": origin,
            "request_path": "/execute",
            "reconciliation_path": "/result",
            "protocol": "v6-light-remote-tool-v1",
            "timeout_seconds": 30,
        }
        assert "credential" not in canonical_json(binding)
        engine = WorkflowEngine(journal, executor, {})
        result = engine.run(
            workflow(
                "remote-flow",
                "1",
                [
                    tool_step(
                        "call",
                        REMOTE_TOOL,
                        {"message": "hello"},
                        approval_required=False,
                    )
                ],
                schema_version="v6-light-workflow-v1",
            ),
            run_id="remote-success",
        )
        request = ToolExecutionRequest.from_mapping(
            next(iter(result.state["actions"].values()))["request"]
        )
        repeated = executor.execute(request)

    assert result.status == "SUCCEEDED"
    assert repeated.output == {"echo": "remote"}
    assert len(state.posts) == 1
    wire, headers = state.posts[0]
    normalized_headers = {key.lower(): value for key, value in headers.items()}
    assert wire["action_id"] == request.action_id
    assert wire["tool"] == REMOTE_TOOL.to_mapping()
    assert normalized_headers["idempotency-key"] == request.action_id
    assert normalized_headers["x-v6-action-id"] == request.action_id
    assert normalized_headers["authorization"] == "Bearer credential-value"
    persisted = canonical_json(result.to_mapping()) + canonical_json(
        [event.to_mapping() for event in journal.events("remote-success")]
    )
    assert "credential-value" not in persisted


def test_remote_http_adapter_reconciles_ambiguous_side_effect_without_replaying() -> (
    None
):
    with _remote_server() as (state, origin):
        state.mode = "uncertain"
        executor = RemoteHttpToolExecutor()
        executor.register(
            RemoteHttpTool(
                REMOTE_TOOL,
                f"{origin}/execute",
                reconciliation_endpoint=f"{origin}/result",
            )
        )
        engine = WorkflowEngine(InMemoryRunJournal(), executor, {})
        pending = engine.run(
            workflow(
                "remote-reconcile",
                "1",
                [tool_step("call", REMOTE_TOOL, {}, approval_required=False)],
                schema_version="v6-light-workflow-v1",
            ),
            run_id="remote-reconcile",
        )
        state.mode = "recovered"
        complete = engine.resume("remote-reconcile")

    assert pending.status == "RECONCILING"
    assert complete.status == "SUCCEEDED"
    assert complete.state["values"]["call"] == {"recovered": True}
    assert len(state.posts) == 1
    assert len(state.gets) == 1
    assert "action_id=" in state.gets[0]


@pytest.mark.parametrize("mode", ("nan", "malformed"))
def test_remote_http_invalid_receipt_remains_unknown(mode: str) -> None:
    """Client decoding failure is not a fabricated business failure."""

    with _remote_server() as (state, origin):
        state.mode = mode
        executor = RemoteHttpToolExecutor()
        executor.register(RemoteHttpTool(REMOTE_TOOL, f"{origin}/execute"))
        snapshot = WorkflowEngine(InMemoryRunJournal(), executor, {}).run(
            workflow(
                "invalid-remote-receipt",
                "1",
                [tool_step("call", REMOTE_TOOL, {}, approval_required=False)],
                schema_version="v6-light-workflow-v1",
            ),
            run_id=f"invalid-receipt-{mode}",
        )

    assert snapshot.status == "RECONCILING"
    action = next(iter(snapshot.state["actions"].values()))
    assert action["status"] == "RECONCILING"
    assert len(state.posts) == 1


class _McpClient:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def call_tool(self, **kwargs: object) -> dict[str, object]:
        self.calls.append(kwargs)
        return {"via": "mcp"}

    def reconcile_tool(self, **_: object) -> None:
        return None


class _ContainerRunner:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def run(self, **kwargs: object) -> dict[str, object]:
        self.calls.append(kwargs)
        return {"via": "container"}

    def reconcile(self, **_: object) -> None:
        return None


class _ResourceAwareContainerRunner(_ContainerRunner):
    def __init__(self) -> None:
        super().__init__()
        self.probes: list[ContainerResourceLimits] = []
        self.available = True

    def ensure_resource_limits(self, *, limits: ContainerResourceLimits) -> None:
        self.probes.append(limits)
        if not self.available:
            raise RuntimeError("cgroup controllers unavailable")


def test_mcp_and_container_adapters_only_delegate_exact_registered_capabilities() -> (
    None
):
    mcp_client = _McpClient()
    mcp = McpToolExecutor(mcp_client)
    mcp.register(
        McpToolDefinition(
            MCP_TOOL,
            "trusted-server",
            "read_status",
            server_version="2026.10",
            schema_digest="sha256:" + "9" * 64,
        )
    )
    assert mcp.execution_binding(MCP_TOOL) == {
        "adapter": "mcp-tools-v1",
        "server_id": "trusted-server",
        "server_version": "2026.10",
        "tool_name": "read_status",
        "schema_digest": "sha256:" + "9" * 64,
        "tool_error_semantics": "uncertain",
    }
    mcp_request = ToolExecutionRequest("mcp-action", MCP_TOOL, {"id": "42"})
    assert mcp.execute(mcp_request).output == {"via": "mcp"}
    assert mcp.execute(mcp_request).output == {"via": "mcp"}
    assert len(mcp_client.calls) == 1
    assert mcp_client.calls[0] == {
        "server_id": "trusted-server",
        "tool_name": "read_status",
        "arguments": {"id": "42"},
        "action_id": "mcp-action",
    }

    runner = _ContainerRunner()
    container = ContainerToolExecutor(runner)
    image = "registry.example/compute@sha256:" + "d" * 64
    container.register(ContainerToolDefinition(CONTAINER_TOOL, image, ("run",)))
    binding = container.execution_binding(CONTAINER_TOOL)
    assert binding["image"] == image
    assert binding["execution_config_version"] == "1"
    assert binding["command_digest"].startswith("sha256:")
    assert container.execute(
        ToolExecutionRequest("container-action", CONTAINER_TOOL, {})
    ).output == {"via": "container"}
    assert runner.calls[0]["image"] == image
    assert runner.calls[0]["command"] == ("run",)

    with pytest.raises(ToolExecutionRejectedError, match="trusted catalogue"):
        mcp.execute(ToolExecutionRequest("unknown-action", REMOTE_TOOL, {}))


def test_container_resource_limits_require_and_recheck_runner_capability() -> None:
    image = "registry.example/compute@sha256:" + "4" * 64
    limits = ContainerResourceLimits(
        memory_bytes=64 * 1024 * 1024,
        cpu_millis=250,
        pids=32,
    )

    missing_capability = ContainerToolExecutor(_ContainerRunner())
    with pytest.raises(
        ToolExecutionRejectedError,
        match="RESOURCE_LIMIT_ENFORCEMENT_UNAVAILABLE",
    ):
        missing_capability.register(
            ContainerToolDefinition(
                CONTAINER_TOOL,
                image,
                ("run",),
                resource_limits=limits,
            )
        )

    runner = _ResourceAwareContainerRunner()
    executor = ContainerToolExecutor(runner)
    executor.register(
        ContainerToolDefinition(
            CONTAINER_TOOL,
            image,
            ("run",),
            resource_limits=limits,
        )
    )
    binding = executor.execution_binding(CONTAINER_TOOL)
    assert binding["resource_limits"] == {
        "memory_bytes": 64 * 1024 * 1024,
        "cpu_millis": 250,
        "pids": 32,
    }
    result = executor.execute(
        ToolExecutionRequest("resource-limited", CONTAINER_TOOL, {})
    )
    assert result.output == {"via": "container"}
    assert runner.probes == [limits, limits]
    assert runner.calls[0]["resource_limits"] is limits

    runner.available = False
    second = ToolExecutionRequest("resource-limit-lost", CONTAINER_TOOL, {})
    with pytest.raises(
        ToolExecutionRejectedError,
        match="RESOURCE_LIMIT_ENFORCEMENT_UNAVAILABLE",
    ):
        executor.execute(second)
    assert len(runner.calls) == 1


def test_container_resource_gate_code_survives_kernel_diagnostics() -> None:
    image = "registry.example/compute@sha256:" + "5" * 64
    limits = ContainerResourceLimits(
        memory_bytes=64 * 1024 * 1024,
        cpu_millis=250,
        pids=32,
    )
    runner = _ResourceAwareContainerRunner()
    executor = ContainerToolExecutor(runner)
    executor.register(
        ContainerToolDefinition(
            CONTAINER_TOOL,
            image,
            ("run",),
            resource_limits=limits,
        )
    )
    runner.available = False
    journal = InMemoryRunJournal()

    blocked = WorkflowEngine(journal, executor, {}).run(
        workflow(
            "resource-gate-lost",
            "1",
            [tool_step("call", CONTAINER_TOOL, {}, approval_required=False)],
            schema_version="v6-light-workflow-v1",
        ),
        run_id="resource-gate-lost",
    )

    assert blocked.status == "BLOCKED"
    assert blocked.state["failure"]["code"] == (
        "RESOURCE_LIMIT_ENFORCEMENT_UNAVAILABLE"
    )
    assert runner.calls == []
    rejected = [
        event
        for event in journal.events(blocked.run_id)
        if event.type == "TOOL_REJECTED"
    ]
    assert rejected[-1].payload["error_code"] == (
        "RESOURCE_LIMIT_ENFORCEMENT_UNAVAILABLE"
    )


def test_rejection_diagnostics_never_promote_arbitrary_exception_text() -> None:
    assert ToolExecutionRejectedError("lower-case private detail").code == (
        "TOOL_REJECTED"
    )
    assert ToolExecutionRejectedError("STABLE_REJECTION").code == "STABLE_REJECTION"
    with pytest.raises(ValueError, match="stable"):
        ToolExecutionRejectedError("message", code="not-stable")


def test_mcp_protocol_result_translation_is_fail_closed_and_structured() -> None:
    class Client:
        result: dict[str, object]

        def __init__(self, result: dict[str, object]) -> None:
            self.result = result

        def call_tool(self, **_: object) -> dict[str, object]:
            return self.result

        def reconcile_tool(self, **_: object) -> None:
            return None

    failed_client = Client(
        {"content": [{"type": "text", "text": "operation failed"}], "isError": True}
    )
    uncertain = McpToolExecutor(failed_client)
    uncertain.register(McpToolDefinition(MCP_TOOL, "trusted-server", "write"))
    with pytest.raises(ToolExecutionUncertainError):
        uncertain.execute(ToolExecutionRequest("mcp-error-unknown", MCP_TOOL, {}))

    malformed = McpToolExecutor(Client({"content": [], "isError": "yes"}))
    malformed.register(McpToolDefinition(MCP_TOOL, "trusted-server", "write"))
    with pytest.raises(ToolExecutionUncertainError, match="invalid isError"):
        malformed.execute(ToolExecutionRequest("mcp-protocol-error", MCP_TOOL, {}))

    known = McpToolExecutor(failed_client)
    known.register(
        McpToolDefinition(
            MCP_TOOL,
            "trusted-server",
            "validate",
            tool_error_semantics="known_failure",
        )
    )
    failed = known.execute(ToolExecutionRequest("mcp-error-known", MCP_TOOL, {}))
    assert failed.succeeded is False
    assert failed.error == "MCP_TOOL_ERROR"

    structured = McpToolExecutor(
        Client(
            {
                "content": [{"type": "text", "text": '{"value":7}'}],
                "structuredContent": {"value": 7},
                "isError": False,
            }
        )
    )
    structured.register(McpToolDefinition(MCP_TOOL, "trusted-server", "read"))
    assert structured.execute(
        ToolExecutionRequest("mcp-structured", MCP_TOOL, {})
    ).output == {"value": 7}


def test_mcp_reconciliation_translates_existing_result_without_redispatch() -> None:
    class RecoverableClient:
        calls = 0
        queries = 0

        def call_tool(self, **_: object) -> dict[str, object]:
            self.calls += 1
            raise AssertionError("recovered action must not be created again")

        def reconcile_tool(self, **_: object) -> dict[str, object]:
            self.queries += 1
            return {
                "structuredContent": {"value": 9},
                "content": [{"type": "text", "text": '{"value":9}'}],
                "isError": False,
            }

    client = RecoverableClient()
    executor = McpToolExecutor(client)
    executor.register(McpToolDefinition(MCP_TOOL, "trusted-server", "read"))
    request = ToolExecutionRequest("mcp-recovered", MCP_TOOL, {})
    recovered = executor.reconcile(request)
    assert recovered is not None and recovered.output == {"value": 9}
    assert executor.execute(request).output == {"value": 9}
    assert client.queries == 1
    assert client.calls == 0


@pytest.mark.parametrize(
    ("query_error", "error_code"),
    (
        (ToolExecutionFailedError("QUERY_CONFIRMED_FAILED"), "QUERY_CONFIRMED_FAILED"),
        (
            ToolExecutionStoppedError("query confirmed stopped"),
            "TOOL_EXECUTION_STOPPED",
        ),
    ),
)
def test_reconciliation_preserves_confirmed_failure_or_stop(
    query_error: Exception, error_code: str
) -> None:
    class Client:
        def call_tool(self, **_: object) -> dict[str, object]:
            raise AssertionError("reconciliation must not dispatch")

        def reconcile_tool(self, **_: object) -> None:
            raise query_error

    executor = McpToolExecutor(Client())
    executor.register(McpToolDefinition(MCP_TOOL, "trusted-server", "write"))
    request = ToolExecutionRequest(f"mcp-query-{error_code}", MCP_TOOL, {})
    result = executor.reconcile(request)
    assert result is not None
    assert result.succeeded is False
    assert result.error == error_code


@pytest.mark.parametrize("kind", ("mcp", "container"))
@pytest.mark.parametrize(
    ("error", "expected"),
    (
        (ToolExecutionFailedError("UPSTREAM_DECLINED"), "failed"),
        (ToolExecutionStoppedError("stopped"), "stopped"),
    ),
)
def test_external_bridges_preserve_explicit_execution_facts(
    kind: str, error: Exception, expected: str
) -> None:
    class Bridge:
        def call_tool(self, **_: object) -> dict[str, object]:
            raise error

        def reconcile_tool(self, **_: object) -> None:
            return None

        def run(self, **_: object) -> dict[str, object]:
            raise error

        def reconcile(self, **_: object) -> None:
            return None

    bridge = Bridge()
    if kind == "mcp":
        executor = McpToolExecutor(bridge)
        executor.register(McpToolDefinition(MCP_TOOL, "trusted-server", "write"))
        request = ToolExecutionRequest(f"{kind}-{expected}", MCP_TOOL, {})
    else:
        executor = ContainerToolExecutor(bridge)
        executor.register(
            ContainerToolDefinition(
                CONTAINER_TOOL,
                "registry.example/compute@sha256:" + "e" * 64,
            )
        )
        request = ToolExecutionRequest(f"{kind}-{expected}", CONTAINER_TOOL, {})
    if expected == "failed":
        result = executor.execute(request)
        assert result.succeeded is False
        assert result.error == "UPSTREAM_DECLINED"
    else:
        with pytest.raises(ToolExecutionStoppedError):
            executor.execute(request)


def test_execution_manifest_freezes_only_used_external_target_across_approval() -> None:
    image_a = "registry.example/compute@sha256:" + "1" * 64
    image_b = "registry.example/compute@sha256:" + "2" * 64
    runner_a = _ContainerRunner()
    first_executor = ContainerToolExecutor(runner_a)
    first_executor.register(ContainerToolDefinition(CONTAINER_TOOL, image_a, ("run",)))
    # Registered but unused capabilities are deliberately outside this run.
    first_executor.register(
        ContainerToolDefinition(
            REMOTE_TOOL,
            "registry.example/unused@sha256:" + "3" * 64,
        )
    )
    journal = InMemoryRunJournal()
    spec = workflow(
        "frozen-container-target",
        "1",
        [tool_step("call", CONTAINER_TOOL, {}, approval_required=True)],
        schema_version="v6-light-workflow-v1",
    )
    waiting = WorkflowEngine(journal, first_executor, {}).run(
        spec, run_id="frozen-container-target"
    )
    frozen = waiting.execution_manifest["tool_bindings"]
    assert len(frozen) == 1
    assert frozen[0]["binding"]["image"] == image_a
    assert frozen[0]["binding"]["command_digest"].startswith("sha256:")

    runner_b = _ContainerRunner()
    second_executor = ContainerToolExecutor(runner_b)
    second_executor.register(ContainerToolDefinition(CONTAINER_TOOL, image_b, ("run",)))
    with pytest.raises(RunConflictError, match="execution target"):
        WorkflowEngine(journal, second_executor, {}).resume(
            waiting.run_id, approved=True
        )
    assert runner_b.calls == []

    # Porting the definition into a new run may intentionally bind image B.
    new_engine = WorkflowEngine(InMemoryRunJournal(), second_executor, {})
    new_waiting = new_engine.run(spec, run_id="new-target-run")
    assert (
        new_waiting.execution_manifest["tool_bindings"][0]["binding"]["image"]
        == image_b
    )
    completed = new_engine.resume(new_waiting.run_id, approved=True)
    assert completed.status == "SUCCEEDED"
    assert len(runner_b.calls) == 1


def test_external_executor_evicts_only_after_the_engine_journals_a_result() -> None:
    client = _McpClient()
    executor = McpToolExecutor(client, max_durable_results=1)
    executor.register(McpToolDefinition(MCP_TOOL, "trusted-server", "read_status"))
    result = WorkflowEngine(InMemoryRunJournal(), executor, {}).run(
        workflow(
            "bounded-mcp-cache",
            "1",
            [
                tool_step("first", MCP_TOOL, {"id": "one"}, approval_required=False),
                tool_step("second", MCP_TOOL, {"id": "two"}, approval_required=False),
            ],
            schema_version="v6-light-workflow-v1",
        ),
        run_id="bounded-mcp-cache",
    )

    assert result.status == "SUCCEEDED"
    assert len(client.calls) == 2
    assert executor.cached_result_count == 1
    first_request = ToolExecutionRequest.from_mapping(
        next(
            action["request"]
            for action in result.state["actions"].values()
            if action["request"]["arguments"] == {"id": "one"}
        )
    )
    assert executor.reconcile(first_request) is None


def test_external_adapter_definitions_reject_unpinned_or_public_insecure_targets() -> (
    None
):
    with pytest.raises(WorkflowValidationError, match="HTTPS"):
        RemoteHttpTool(REMOTE_TOOL, "http://example.test/execute")
    with pytest.raises(WorkflowValidationError, match="same origin"):
        RemoteHttpTool(
            REMOTE_TOOL,
            "https://example.test/execute",
            reconciliation_endpoint="https://other.test/result",
        )
    with pytest.raises(WorkflowValidationError, match="pinned"):
        ContainerToolDefinition(CONTAINER_TOOL, "registry.example/compute:latest")


def test_mcp_transport_failure_is_ambiguous_not_a_second_call() -> None:
    class BrokenMcp:
        calls = 0

        def call_tool(self, **_: object) -> dict[str, object]:
            self.calls += 1
            raise ConnectionError("receipt missing")

        def reconcile_tool(self, **_: object) -> None:
            return None

    client = BrokenMcp()
    executor = McpToolExecutor(client)
    executor.register(McpToolDefinition(MCP_TOOL, "trusted-server", "read_status"))
    request = ToolExecutionRequest("ambiguous-mcp", MCP_TOOL, {})
    with pytest.raises(ToolExecutionUncertainError):
        executor.execute(request)
    with pytest.raises(ToolExecutionUncertainError):
        executor.execute(request)
    assert client.calls == 1
