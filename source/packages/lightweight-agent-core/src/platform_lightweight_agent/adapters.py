"""Optional execution adapters for trusted V6 external tool definitions.

The lightweight kernel deliberately does not install a container runtime, an
MCP client, or an HTTP SDK.  These adapters provide the narrow integration
boundary instead: application code registers an exact versioned tool before a
run starts, while the model can only propose that frozen reference and JSON
arguments.  No adapter accepts a command, URL, credential, or plugin package
from workflow input.
"""

from __future__ import annotations

import json
import re
from collections import OrderedDict
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import PurePosixPath
from threading import Condition, RLock
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .runtime_contracts import (
    ToolExecutionFailedError,
    ToolExecutionRejectedError,
    ToolExecutionRequest,
    ToolExecutionResult,
    ToolExecutionStoppedError,
    ToolExecutionUncertainError,
)
from .spec import JsonValue, ToolReference, WorkflowValidationError, digest, json_copy

_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_IMAGE_DIGEST = re.compile(r"@sha256:[0-9a-f]{64}$")
_ERROR_CODE = re.compile(r"^[A-Z][A-Z0-9_]{0,127}$")
_MAX_REMOTE_RESPONSE_BYTES = 1_048_576
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})


def _key(tool: ToolReference) -> tuple[str, str, str]:
    return (tool.tool_id, tool.version, tool.package_digest)


def _identifier(value: object, label: str) -> str:
    if not isinstance(value, str) or _IDENTIFIER.fullmatch(value) is None:
        raise WorkflowValidationError(f"{label} must be a portable identifier")
    return value


def _output(action_id: str, value: object) -> ToolExecutionResult:
    if isinstance(value, Mapping):
        output = json_copy(dict(value))
    else:
        output = {"value": json_copy(value)}
    if not isinstance(output, Mapping):  # defensive: json_copy is deliberately dynamic
        raise ToolExecutionRejectedError("external tool output is not JSON")
    return ToolExecutionResult(action_id, True, output=output)


def _failed(action_id: str, error: str) -> ToolExecutionResult:
    if _ERROR_CODE.fullmatch(error) is None:
        raise ToolExecutionRejectedError("external tool returned an invalid error code")
    return ToolExecutionResult(action_id, False, error=error)


def _reject_non_finite_json(value: str) -> object:
    """Make stdlib decoding conform to JSON instead of accepting NaN/Infinity."""

    raise ValueError(f"non-finite JSON number: {value}")


def _strict_json_loads(body: bytes) -> object:
    return json.loads(
        body.decode("utf-8"),
        parse_constant=_reject_non_finite_json,
    )


class _NoRedirect(HTTPRedirectHandler):
    """Never forward a registered credential to a redirect target."""

    def redirect_request(self, *args: object, **kwargs: object) -> None:
        del args, kwargs


class _RegisteredExternalToolExecutor:
    """Shared action-id safety mechanics for external adapters.

    An in-process cache removes duplicate calls while one V6 process is alive.
    Across a restart the engine asks ``reconcile`` first; a real external
    backend must use the stable ``action_id`` as its idempotency/lookup key.
    """

    def __init__(self, *, max_durable_results: int = 1_024) -> None:
        if (
            not isinstance(max_durable_results, int)
            or isinstance(max_durable_results, bool)
            or max_durable_results < 0
        ):
            raise ValueError("max_durable_results must be a non-negative integer")
        self._lock = RLock()
        self._finished = Condition(self._lock)
        self._definitions: dict[tuple[str, str, str], object] = {}
        self._requests: dict[str, ToolExecutionRequest] = {}
        self._results: dict[str, ToolExecutionResult] = {}
        self._in_flight: set[str] = set()
        self._uncertain: set[str] = set()
        self._max_durable_results = max_durable_results
        self._durable_results: OrderedDict[str, None] = OrderedDict()

    def _register(self, tool: ToolReference, definition: object) -> None:
        with self._lock:
            key = _key(tool)
            if key in self._definitions:
                raise ToolExecutionRejectedError("tool reference is already registered")
            self._definitions[key] = definition

    def execution_binding(self, tool: ToolReference) -> dict[str, JsonValue]:
        """Return the credential-free target identity frozen into a run."""

        with self._lock:
            definition = self._definitions.get(_key(tool))
        if definition is None:
            raise ToolExecutionRejectedError(
                "tool is not registered in the trusted catalogue"
            )
        binding = json_copy(self._execution_binding(definition))
        if not isinstance(binding, dict):  # defensive: subclass contract
            raise WorkflowValidationError(
                "external adapter execution binding must be an object"
            )
        return binding

    def execute(self, request: ToolExecutionRequest) -> ToolExecutionResult:
        definition: object
        with self._finished:
            while True:
                original = self._requests.get(request.action_id)
                if original is not None:
                    if original.to_mapping() != request.to_mapping():
                        raise ToolExecutionRejectedError(
                            "action id was reused with another request"
                        )
                    known = self._results.get(request.action_id)
                    if known is not None:
                        return known
                    if request.action_id in self._uncertain:
                        raise ToolExecutionUncertainError(
                            "the original tool outcome is still uncertain"
                        )
                    if request.action_id in self._in_flight:
                        self._finished.wait()
                        continue
                definition = self._definitions.get(_key(request.tool))
                if definition is None:
                    raise ToolExecutionRejectedError(
                        "tool is not registered in the trusted catalogue"
                    )
                self._requests[request.action_id] = request
                self._in_flight.add(request.action_id)
                break
        try:
            result = self._invoke(definition, request)
            if result.action_id != request.action_id:
                raise ToolExecutionRejectedError(
                    "external adapter returned a result for another action"
                )
        except ToolExecutionRejectedError:
            with self._finished:
                self._in_flight.discard(request.action_id)
                self._finished.notify_all()
            raise
        except ToolExecutionStoppedError:
            with self._finished:
                self._in_flight.discard(request.action_id)
                self._finished.notify_all()
            raise
        except ToolExecutionFailedError as error:
            result = _failed(request.action_id, error.code)
        except ToolExecutionUncertainError:
            with self._finished:
                self._in_flight.discard(request.action_id)
                self._uncertain.add(request.action_id)
                self._finished.notify_all()
            raise
        except Exception as error:  # noqa: BLE001 - adapter boundary is total
            # Crossing an adapter boundary means an arbitrary client/parser
            # exception cannot prove that the external operation failed.  Keep
            # the diagnostic stable and credential-free, but preserve the
            # business fact as unknown so recovery can query the same action.
            with self._finished:
                self._in_flight.discard(request.action_id)
                self._uncertain.add(request.action_id)
                self._finished.notify_all()
            raise ToolExecutionUncertainError("EXTERNAL_ADAPTER_ERROR") from error
        with self._finished:
            previous = self._results.get(request.action_id)
            self._in_flight.discard(request.action_id)
            if previous is None:
                self._results[request.action_id] = result
            self._finished.notify_all()
            return previous or result

    def reconcile(self, request: ToolExecutionRequest) -> ToolExecutionResult | None:
        with self._finished:
            original = self._requests.get(request.action_id)
            if original is not None and original.to_mapping() != request.to_mapping():
                raise ToolExecutionRejectedError(
                    "action id was reused with another request"
                )
            known = self._results.get(request.action_id)
            if known is not None:
                return known
            definition = self._definitions.get(_key(request.tool))
            if definition is None:
                raise ToolExecutionRejectedError(
                    "tool is not registered in the trusted catalogue"
                )
        try:
            recovered = self._reconcile(definition, request)
        except ToolExecutionRejectedError:
            raise
        except ToolExecutionFailedError as error:
            recovered = _failed(request.action_id, error.code)
        except ToolExecutionStoppedError:
            recovered = _failed(request.action_id, "TOOL_EXECUTION_STOPPED")
        except ToolExecutionUncertainError:
            return None
        except Exception:  # noqa: BLE001 - reconciliation must fail closed to unknown
            # Reconciliation cannot safely create a new action or turn an
            # unavailable status endpoint into a false failure.  Keep the run
            # reconciling until an operator/backend can establish the outcome.
            return None
        if recovered is None:
            return None
        if recovered.action_id != request.action_id:
            raise ToolExecutionRejectedError(
                "external adapter returned a result for another action"
            )
        with self._finished:
            previous = self._results.get(request.action_id)
            if previous is None:
                self._requests[request.action_id] = request
                self._results[request.action_id] = recovered
                self._uncertain.discard(request.action_id)
            return previous or recovered

    @property
    def cached_result_count(self) -> int:
        """Number of completed action results currently retained in memory."""

        with self._lock:
            return len(self._results)

    def acknowledge_durable_result(self, request: ToolExecutionRequest) -> None:
        """Discard only a completion already protected by the V6 journal."""

        with self._finished:
            action_id = request.action_id
            original = self._requests.get(action_id)
            if original is not None and original.to_mapping() != request.to_mapping():
                raise ToolExecutionRejectedError(
                    "action id was reused with another request"
                )
            if (
                action_id not in self._results
                or action_id in self._in_flight
                or action_id in self._uncertain
            ):
                return
            self._durable_results[action_id] = None
            self._durable_results.move_to_end(action_id)
            while len(self._durable_results) > self._max_durable_results:
                expired_action_id, _ = self._durable_results.popitem(last=False)
                self._results.pop(expired_action_id, None)
                self._requests.pop(expired_action_id, None)

    def _invoke(
        self, definition: object, request: ToolExecutionRequest
    ) -> ToolExecutionResult:
        raise NotImplementedError

    def _reconcile(
        self, definition: object, request: ToolExecutionRequest
    ) -> ToolExecutionResult | None:
        return None

    def _execution_binding(self, definition: object) -> dict[str, JsonValue]:
        raise NotImplementedError


def _safe_url(value: object, label: str) -> str:
    if not isinstance(value, str) or len(value) > 2_048:
        raise WorkflowValidationError(f"{label} must be a registered HTTPS endpoint")
    parsed = urlsplit(value)
    host = parsed.hostname.lower() if parsed.hostname else None
    if (
        parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
        or parsed.query
        or host is None
        or not parsed.path
        or (
            parsed.scheme != "https"
            and not (parsed.scheme == "http" and host in _LOOPBACK_HOSTS)
        )
    ):
        raise WorkflowValidationError(
            f"{label} must be an HTTPS endpoint (or loopback HTTP)"
        )
    return value


@dataclass(frozen=True)
class RemoteHttpTool:
    """One reviewed remote service endpoint, fixed outside workflow input."""

    tool: ToolReference
    endpoint: str
    credential_ref: str | None = None
    timeout_seconds: int = 30
    reconciliation_endpoint: str | None = None
    service_id: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "endpoint", _safe_url(self.endpoint, "remote endpoint")
        )
        if self.credential_ref is not None:
            object.__setattr__(
                self,
                "credential_ref",
                _identifier(self.credential_ref, "credential_ref"),
            )
        if (
            not isinstance(self.timeout_seconds, int)
            or isinstance(self.timeout_seconds, bool)
            or not 1 <= self.timeout_seconds <= 86_400
        ):
            raise WorkflowValidationError(
                "remote timeout_seconds must be between 1 and 86400"
            )
        if self.reconciliation_endpoint is not None:
            reconciliation_endpoint = _safe_url(
                self.reconciliation_endpoint, "remote reconciliation_endpoint"
            )
            primary = urlsplit(self.endpoint)
            recovery = urlsplit(reconciliation_endpoint)
            if (primary.scheme, primary.netloc) != (recovery.scheme, recovery.netloc):
                raise WorkflowValidationError(
                    "remote reconciliation_endpoint must use the same origin"
                )
            object.__setattr__(self, "reconciliation_endpoint", reconciliation_endpoint)
        if self.service_id is not None:
            object.__setattr__(
                self, "service_id", _identifier(self.service_id, "remote service_id")
            )


CredentialResolver = Callable[[str], str]


class RemoteHttpToolExecutor(_RegisteredExternalToolExecutor):
    """Strict stdlib HTTP adapter with idempotency and outcome reconciliation.

    The remote endpoint receives a fixed v1 JSON envelope and must return
    ``{"output": {...}}`` on a completed 2xx request.  It should persist the
    ``Idempotency-Key`` and expose the optional recovery protocol described by
    :class:`RemoteHttpTool`; a timeout/5xx never triggers a blind replay.
    """

    def __init__(
        self,
        credential_resolver: CredentialResolver | None = None,
        *,
        max_durable_results: int = 1_024,
    ) -> None:
        super().__init__(max_durable_results=max_durable_results)
        self._credential_resolver = credential_resolver
        self._opener = build_opener(_NoRedirect())

    def register(self, definition: RemoteHttpTool) -> None:
        self._register(definition.tool, definition)

    def _execution_binding(self, definition: object) -> dict[str, JsonValue]:
        if not isinstance(definition, RemoteHttpTool):
            raise WorkflowValidationError("remote tool registration is invalid")
        primary = urlsplit(definition.endpoint)
        recovery = (
            urlsplit(definition.reconciliation_endpoint)
            if definition.reconciliation_endpoint is not None
            else None
        )
        return {
            "adapter": "remote-http-v1",
            "service_id": definition.service_id
            or f"{primary.scheme}://{primary.netloc}",
            "request_path": primary.path,
            "reconciliation_path": recovery.path if recovery is not None else None,
            "protocol": "v6-light-remote-tool-v1",
            "timeout_seconds": definition.timeout_seconds,
        }

    def _headers(self, definition: RemoteHttpTool, action_id: str) -> dict[str, str]:
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Idempotency-Key": action_id,
            "X-V6-Action-ID": action_id,
        }
        if definition.credential_ref is None:
            return headers
        if self._credential_resolver is None:
            raise ToolExecutionRejectedError("registered credential is unavailable")
        try:
            credential = self._credential_resolver(definition.credential_ref)
        except Exception as error:
            raise ToolExecutionRejectedError(
                "registered credential is unavailable"
            ) from error
        if (
            not isinstance(credential, str)
            or not credential
            or "\r" in credential
            or "\n" in credential
        ):
            raise ToolExecutionRejectedError("registered credential is invalid")
        headers["Authorization"] = f"Bearer {credential}"
        return headers

    def _invoke(
        self, definition: object, request: ToolExecutionRequest
    ) -> ToolExecutionResult:
        if not isinstance(definition, RemoteHttpTool):
            raise ToolExecutionRejectedError("remote tool registration is invalid")
        payload = {
            "schema_version": "v6-light-remote-tool-v1",
            "action_id": request.action_id,
            "tool": request.tool.to_mapping(),
            "arguments": dict(request.arguments),
        }
        wire = json.dumps(
            payload, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")
        try:
            response = self._opener.open(
                Request(
                    definition.endpoint,
                    data=wire,
                    headers=self._headers(definition, request.action_id),
                    method="POST",
                ),
                timeout=definition.timeout_seconds,
            )
            with response:
                status = response.status
                body = response.read(_MAX_REMOTE_RESPONSE_BYTES + 1)
        except HTTPError as error:
            if 400 <= error.code < 500:
                return _failed(request.action_id, f"REMOTE_HTTP_{error.code}")
            raise ToolExecutionUncertainError(
                "remote service outcome is unavailable"
            ) from error
        except (URLError, OSError, TimeoutError) as error:
            raise ToolExecutionUncertainError(
                "remote service outcome is unavailable"
            ) from error
        if status == 202:
            raise ToolExecutionUncertainError(
                "remote service accepted an asynchronous action"
            )
        if not 200 <= status < 300:
            raise ToolExecutionUncertainError(
                "remote service returned an unexpected outcome"
            )
        if len(body) > _MAX_REMOTE_RESPONSE_BYTES:
            raise ToolExecutionUncertainError("remote service response is too large")
        if status == 204 and not body:
            return _output(request.action_id, {})
        try:
            decoded = _strict_json_loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
            raise ToolExecutionUncertainError(
                "remote service response is not recoverable"
            ) from error
        if not isinstance(decoded, Mapping) or set(decoded) != {"output"}:
            raise ToolExecutionUncertainError(
                "remote service response is not a V6 envelope"
            )
        return _output(request.action_id, decoded["output"])

    def _reconcile(
        self, definition: object, request: ToolExecutionRequest
    ) -> ToolExecutionResult | None:
        if not isinstance(definition, RemoteHttpTool):
            raise ToolExecutionRejectedError("remote tool registration is invalid")
        if definition.reconciliation_endpoint is None:
            return None
        parsed = urlsplit(definition.reconciliation_endpoint)
        query = [
            *parse_qsl(parsed.query, keep_blank_values=True),
            ("action_id", request.action_id),
        ]
        endpoint = urlunsplit(
            (
                parsed.scheme,
                parsed.netloc,
                parsed.path,
                urlencode(query),
                parsed.fragment,
            )
        )
        try:
            response = self._opener.open(
                Request(
                    endpoint,
                    headers=self._headers(definition, request.action_id),
                    method="GET",
                ),
                timeout=definition.timeout_seconds,
            )
            with response:
                if response.status == 202:
                    return None
                if response.status != 200:
                    return None
                body = response.read(_MAX_REMOTE_RESPONSE_BYTES + 1)
        except (HTTPError, URLError, OSError, TimeoutError):
            return None
        if len(body) > _MAX_REMOTE_RESPONSE_BYTES:
            return None
        try:
            decoded = _strict_json_loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
            return None
        if not isinstance(decoded, Mapping):
            return None
        status = decoded.get("status")
        if status == "pending":
            return None
        if status == "succeeded" and set(decoded) == {"status", "output"}:
            return _output(request.action_id, decoded["output"])
        if status == "failed" and set(decoded) == {"status", "error"}:
            error = decoded.get("error")
            if isinstance(error, str) and _ERROR_CODE.fullmatch(error) is not None:
                return _failed(request.action_id, error)
        return None


class McpClient(Protocol):
    """Application-owned bridge to an already reviewed MCP client/session."""

    def call_tool(
        self,
        *,
        server_id: str,
        tool_name: str,
        arguments: Mapping[str, JsonValue],
        action_id: str,
    ) -> Mapping[str, JsonValue] | JsonValue: ...

    def reconcile_tool(
        self, *, server_id: str, tool_name: str, action_id: str
    ) -> ToolExecutionResult | Mapping[str, JsonValue] | JsonValue | None: ...


@dataclass(frozen=True)
class McpToolDefinition:
    """Maps one V6 tool reference to one reviewed MCP server/tool pair."""

    tool: ToolReference
    server_id: str
    tool_name: str
    server_version: str | None = None
    schema_digest: str | None = None
    tool_error_semantics: str = "uncertain"

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "server_id", _identifier(self.server_id, "mcp server_id")
        )
        object.__setattr__(
            self, "tool_name", _identifier(self.tool_name, "mcp tool_name")
        )
        if self.server_version is not None:
            object.__setattr__(
                self,
                "server_version",
                _identifier(self.server_version, "mcp server_version"),
            )
        if self.schema_digest is not None and (
            not isinstance(self.schema_digest, str)
            or re.fullmatch(r"sha256:[0-9a-f]{64}", self.schema_digest) is None
        ):
            raise WorkflowValidationError("mcp schema_digest must be sha256")
        if self.tool_error_semantics not in {"uncertain", "known_failure"}:
            raise WorkflowValidationError(
                "mcp tool_error_semantics must be uncertain or known_failure"
            )


def translate_mcp_tool_result(
    action_id: str,
    value: Mapping[str, JsonValue] | JsonValue,
    *,
    tool_error_semantics: str = "uncertain",
) -> ToolExecutionResult:
    """Translate one MCP tools/call result without inventing execution facts.

    Raw application-owned bridges may still return their legacy JSON payload.
    A mapping carrying MCP result fields is interpreted according to the MCP
    protocol: ``structuredContent`` is preferred, unstructured ``content`` is
    retained as data, and ``isError`` is never treated as successful output.
    Because an MCP tool error does not prove absence of side effects, the safe
    default is an unknown outcome; a reviewed integration may explicitly opt
    into a known business failure contract.
    """

    if tool_error_semantics not in {"uncertain", "known_failure"}:
        raise WorkflowValidationError("invalid MCP tool error semantics")
    if not isinstance(value, Mapping) or not (
        {"content", "structuredContent", "isError"} & set(value)
    ):
        return _output(action_id, value)
    is_error = value.get("isError", False)
    if not isinstance(is_error, bool):
        raise ToolExecutionUncertainError("MCP result has invalid isError")
    if is_error:
        if tool_error_semantics == "known_failure":
            return _failed(action_id, "MCP_TOOL_ERROR")
        raise ToolExecutionUncertainError("MCP tool reported an uncertain failure")
    if "structuredContent" in value:
        structured = value["structuredContent"]
        if not isinstance(structured, Mapping):
            raise ToolExecutionUncertainError(
                "MCP structuredContent is not a JSON object"
            )
        return _output(action_id, structured)
    content = value.get("content", [])
    if not isinstance(content, list):
        raise ToolExecutionUncertainError("MCP content is not a JSON array")
    return _output(action_id, {"content": content})


class McpToolExecutor(_RegisteredExternalToolExecutor):
    """Adapter over an injected MCP session; it never discovers/installs tools."""

    def __init__(self, client: McpClient, *, max_durable_results: int = 1_024) -> None:
        super().__init__(max_durable_results=max_durable_results)
        self._client = client

    def register(self, definition: McpToolDefinition) -> None:
        self._register(definition.tool, definition)

    def _execution_binding(self, definition: object) -> dict[str, JsonValue]:
        if not isinstance(definition, McpToolDefinition):
            raise WorkflowValidationError("MCP tool registration is invalid")
        return {
            "adapter": "mcp-tools-v1",
            "server_id": definition.server_id,
            "server_version": definition.server_version,
            "tool_name": definition.tool_name,
            "schema_digest": definition.schema_digest,
            "tool_error_semantics": definition.tool_error_semantics,
        }

    def _invoke(
        self, definition: object, request: ToolExecutionRequest
    ) -> ToolExecutionResult:
        if not isinstance(definition, McpToolDefinition):
            raise ToolExecutionRejectedError("MCP tool registration is invalid")
        try:
            return translate_mcp_tool_result(
                request.action_id,
                self._client.call_tool(
                    server_id=definition.server_id,
                    tool_name=definition.tool_name,
                    arguments=request.arguments,
                    action_id=request.action_id,
                ),
                tool_error_semantics=definition.tool_error_semantics,
            )
        except ToolExecutionRejectedError:
            raise
        except ToolExecutionFailedError:
            raise
        except ToolExecutionStoppedError:
            raise
        except ToolExecutionUncertainError:
            raise
        except Exception as error:
            raise ToolExecutionUncertainError("MCP outcome is unavailable") from error

    def _reconcile(
        self, definition: object, request: ToolExecutionRequest
    ) -> ToolExecutionResult | None:
        if not isinstance(definition, McpToolDefinition):
            raise ToolExecutionRejectedError("MCP tool registration is invalid")
        recovered = self._client.reconcile_tool(
            server_id=definition.server_id,
            tool_name=definition.tool_name,
            action_id=request.action_id,
        )
        if recovered is None:
            return None
        if isinstance(recovered, ToolExecutionResult):
            return recovered
        return translate_mcp_tool_result(
            request.action_id,
            recovered,
            tool_error_semantics=definition.tool_error_semantics,
        )


class ContainerRunner(Protocol):
    """Reviewed container runtime adapter owned by the embedding application."""

    def ensure_resource_limits(self, *, limits: ContainerResourceLimits) -> None:
        """Fail unless the runtime can enforce every requested cgroup limit."""

        ...

    def run(
        self,
        *,
        image: str,
        command: tuple[str, ...],
        arguments: Mapping[str, JsonValue],
        action_id: str,
        timeout_seconds: int,
        resource_limits: ContainerResourceLimits | None = None,
    ) -> Mapping[str, JsonValue] | JsonValue: ...

    def reconcile(
        self, *, image: str, action_id: str
    ) -> ToolExecutionResult | Mapping[str, JsonValue] | JsonValue | None: ...


@dataclass(frozen=True)
class ContainerResourceLimits:
    """Strong limits a reviewed runner must prove it can enforce.

    ``cpu_millis`` uses 1000 units per CPU (250 means one quarter of one CPU).
    Merely accepting equivalent Docker flags is not sufficient: the runner's
    capability probe must reject a daemon/cgroup setup that would ignore them.
    """

    memory_bytes: int
    cpu_millis: int
    pids: int

    def __post_init__(self) -> None:
        limits = {
            "memory_bytes": (self.memory_bytes, 1, 1 << 50),
            "cpu_millis": (self.cpu_millis, 1, 1_000_000),
            "pids": (self.pids, 1, 1_000_000),
        }
        for name, (value, minimum, maximum) in limits.items():
            if (
                not isinstance(value, int)
                or isinstance(value, bool)
                or not minimum <= value <= maximum
            ):
                raise WorkflowValidationError(
                    f"container {name} must be between {minimum} and {maximum}"
                )

    def to_mapping(self) -> dict[str, JsonValue]:
        return {
            "memory_bytes": self.memory_bytes,
            "cpu_millis": self.cpu_millis,
            "pids": self.pids,
        }


@dataclass(frozen=True)
class ContainerToolDefinition:
    """One immutable image digest and command, never a model-provided shell."""

    tool: ToolReference
    image: str
    command: tuple[str, ...] = ()
    timeout_seconds: int = 300
    execution_config_version: str = "1"
    resource_limits: ContainerResourceLimits | None = None

    def __post_init__(self) -> None:
        if (
            not isinstance(self.image, str)
            or len(self.image) > 1_024
            or _IMAGE_DIGEST.search(self.image) is None
            or PurePosixPath(self.image).name in {"", "."}
        ):
            raise WorkflowValidationError(
                "container image must be pinned by sha256 digest"
            )
        command = tuple(self.command)
        if len(command) > 64 or any(
            not isinstance(item, str) or not item or "\x00" in item or len(item) > 4_096
            for item in command
        ):
            raise WorkflowValidationError("container command is invalid")
        object.__setattr__(self, "command", command)
        if (
            not isinstance(self.timeout_seconds, int)
            or isinstance(self.timeout_seconds, bool)
            or not 1 <= self.timeout_seconds <= 86_400
        ):
            raise WorkflowValidationError(
                "container timeout_seconds must be between 1 and 86400"
            )
        object.__setattr__(
            self,
            "execution_config_version",
            _identifier(
                self.execution_config_version, "container execution_config_version"
            ),
        )
        if self.resource_limits is not None and not isinstance(
            self.resource_limits, ContainerResourceLimits
        ):
            raise WorkflowValidationError("container resource_limits are invalid")


class ContainerToolExecutor(_RegisteredExternalToolExecutor):
    """Adapter over a caller-owned isolated container runner.

    The runner is responsible for network/filesystem/resource isolation and
    must use ``action_id`` as a backend idempotency key.  This package never
    shells out to Docker/Podman and cannot widen host permissions itself.
    """

    def __init__(
        self, runner: ContainerRunner, *, max_durable_results: int = 1_024
    ) -> None:
        super().__init__(max_durable_results=max_durable_results)
        self._runner = runner

    def register(self, definition: ContainerToolDefinition) -> None:
        if not isinstance(definition, ContainerToolDefinition):
            raise ToolExecutionRejectedError("container tool registration is invalid")
        if definition.resource_limits is not None:
            self._ensure_resource_limits(definition.resource_limits)
        self._register(definition.tool, definition)

    def _ensure_resource_limits(self, limits: ContainerResourceLimits) -> None:
        ensure = getattr(self._runner, "ensure_resource_limits", None)
        if not callable(ensure):
            raise ToolExecutionRejectedError(
                "RESOURCE_LIMIT_ENFORCEMENT_UNAVAILABLE"
            )
        try:
            ensure(limits=limits)
        except ToolExecutionRejectedError:
            raise
        except Exception as error:
            raise ToolExecutionRejectedError(
                "RESOURCE_LIMIT_ENFORCEMENT_UNAVAILABLE"
            ) from error

    def _execution_binding(self, definition: object) -> dict[str, JsonValue]:
        if not isinstance(definition, ContainerToolDefinition):
            raise WorkflowValidationError("container tool registration is invalid")
        binding: dict[str, JsonValue] = {
            "adapter": "container-runner-v1",
            "image": definition.image,
            # Freeze the command without persisting accidental command-line
            # credentials in a portable journal.
            "command_digest": digest(list(definition.command)),
            "execution_config_version": definition.execution_config_version,
            "timeout_seconds": definition.timeout_seconds,
        }
        if definition.resource_limits is not None:
            binding["resource_limits"] = definition.resource_limits.to_mapping()
        return binding

    def _invoke(
        self, definition: object, request: ToolExecutionRequest
    ) -> ToolExecutionResult:
        if not isinstance(definition, ContainerToolDefinition):
            raise ToolExecutionRejectedError("container tool registration is invalid")
        try:
            if definition.resource_limits is not None:
                self._ensure_resource_limits(definition.resource_limits)
                value = self._runner.run(
                    image=definition.image,
                    command=definition.command,
                    arguments=request.arguments,
                    action_id=request.action_id,
                    timeout_seconds=definition.timeout_seconds,
                    resource_limits=definition.resource_limits,
                )
            else:
                value = self._runner.run(
                    image=definition.image,
                    command=definition.command,
                    arguments=request.arguments,
                    action_id=request.action_id,
                    timeout_seconds=definition.timeout_seconds,
                )
            return _output(
                request.action_id,
                value,
            )
        except ToolExecutionRejectedError:
            raise
        except ToolExecutionFailedError:
            raise
        except ToolExecutionStoppedError:
            raise
        except ToolExecutionUncertainError:
            raise
        except Exception as error:
            raise ToolExecutionUncertainError(
                "container outcome is unavailable"
            ) from error

    def _reconcile(
        self, definition: object, request: ToolExecutionRequest
    ) -> ToolExecutionResult | None:
        if not isinstance(definition, ContainerToolDefinition):
            raise ToolExecutionRejectedError("container tool registration is invalid")
        recovered = self._runner.reconcile(
            image=definition.image, action_id=request.action_id
        )
        if recovered is None:
            return None
        if isinstance(recovered, ToolExecutionResult):
            return recovered
        return _output(request.action_id, recovered)
