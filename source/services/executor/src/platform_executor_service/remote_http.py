"""Fail-closed implementation of the deployment-bound remote operation protocol."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Awaitable, Callable
from urllib.parse import quote, urlsplit

import httpx

from .contracts import canonical_json


_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_PORT = re.compile(r"^[a-z][a-z0-9_.-]*$")
_TERMINAL = {"succeeded", "failed", "cancelled", "interrupted"}


class RemoteOperationError(Exception):
    def __init__(self, code: str, state: dict | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.state = state


@dataclass(frozen=True)
class RemoteExecutionResult:
    outputs: list[dict]
    values: dict
    state: dict


PersistState = Callable[[dict], Awaitable[None]]


def _now() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def _file_identity(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            size += len(chunk)
            digest.update(chunk)
    return "sha256:" + digest.hexdigest(), size


class RemoteOperationRunner:
    def __init__(self, config: dict, lease: dict, persist_state: PersistState) -> None:
        self.config = config
        self.lease = lease
        self.persist_state = persist_state
        self.binding = lease.get("remoteBinding")
        if not isinstance(self.binding, dict) or set(self.binding) != {
            "serviceId",
            "operationType",
        }:
            raise RemoteOperationError("REMOTE_BINDING_INVALID")
        try:
            self.service = config["remoteServices"][self.binding["serviceId"]]
        except (KeyError, TypeError) as error:
            raise RemoteOperationError("REMOTE_BINDING_INVALID") from error
        if self.binding["operationType"] not in self.service.get(
            "allowedOperations", ()
        ):
            raise RemoteOperationError("REMOTE_BINDING_INVALID")
        self.base_url, self.allowed_host = self._endpoint()
        self.api_key = self._read_secret(self.service["credentialSecret"])
        self.ca_bundle = self._secret_path(self.service["caBundleSecret"])
        self.state = dict(lease.get("remoteState") or {})
        self.capabilities: dict = {}

    def _endpoint(self) -> tuple[str, str]:
        try:
            base_url = os.environ[self.service["baseUrlEnvironment"]].rstrip("/")
            allowed_host = os.environ[self.service["allowedHostEnvironment"]]
        except (KeyError, TypeError) as error:
            raise RemoteOperationError("REMOTE_BINDING_INVALID") from error
        parsed = urlsplit(base_url)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or parsed.hostname != allowed_host
            or "/../" in parsed.path + "/"
        ):
            raise RemoteOperationError("REMOTE_BINDING_INVALID")
        return base_url, allowed_host

    @staticmethod
    def _secret_path(name: str) -> Path:
        if not isinstance(name, str) or not re.fullmatch(r"[a-z0-9][a-z0-9-]*", name):
            raise RemoteOperationError("REMOTE_BINDING_INVALID")
        path = Path("/run/secrets") / name
        if not path.is_file() or path.is_symlink():
            raise RemoteOperationError("REMOTE_BINDING_INVALID")
        return path

    def _read_secret(self, name: str) -> str:
        try:
            value = self._secret_path(name).read_text(encoding="utf-8").strip()
        except (OSError, UnicodeDecodeError) as error:
            raise RemoteOperationError("REMOTE_BINDING_INVALID") from error
        if len(value) < 32:
            raise RemoteOperationError("REMOTE_BINDING_INVALID")
        return value

    def _client(self) -> httpx.AsyncClient:
        timeout = float(self.service["connectTimeoutSeconds"])
        return httpx.AsyncClient(
            base_url=self.base_url,
            headers={"Authorization": f"Bearer {self.api_key}"},
            timeout=httpx.Timeout(timeout, connect=timeout),
            verify=str(self.ca_bundle),
            follow_redirects=False,
            trust_env=False,
        )

    async def run(self) -> RemoteExecutionResult:
        client = self._client()
        try:
            try:
                async with asyncio.timeout(self.lease["timeoutSeconds"]):
                    return await self._run(client)
            except TimeoutError as error:
                await self._cancel_safely(client)
                raise RemoteOperationError("OPERATOR_TIMEOUT", self.state) from error
            except asyncio.CancelledError:
                await asyncio.shield(self._cancel_safely(client))
                raise
        finally:
            await client.aclose()

    async def _run(self, client: httpx.AsyncClient) -> RemoteExecutionResult:
        version = await self._get_json(client, "/version", anonymous=True)
        self._validate_version(version)
        self.capabilities = await self._get_json(client, "/api/v1/capabilities")
        operations = self.capabilities.get("operations")
        if (
            not isinstance(operations, list)
            or self.binding["operationType"] not in operations
        ):
            raise RemoteOperationError("REMOTE_VERSION_MISMATCH", self.state)

        inputs, stable_inputs = self._local_inputs()
        idempotency_key = hashlib.sha256(
            f"{self.lease['jobId']}:{self.lease['nodeId']}".encode()
        ).hexdigest()
        request_digest = (
            "sha256:"
            + hashlib.sha256(
                canonical_json(
                    {
                        "operationType": self.binding["operationType"],
                        "clientRequestId": idempotency_key,
                        "inputs": stable_inputs,
                        "parameters": self.lease["parameters"],
                    }
                )
            ).hexdigest()
        )
        if self.state and (
            self.state.get("requestSha256") != request_digest
            or self.state.get("idempotencyKey") != idempotency_key
        ):
            raise RemoteOperationError("REMOTE_PROTOCOL_INVALID", self.state)
        self.state = {
            "remoteTaskId": self.state.get("remoteTaskId"),
            "requestSha256": request_digest,
            "idempotencyKey": idempotency_key,
            "remoteStatus": self.state.get("remoteStatus", "coordinating"),
            "lastPolledAt": _now(),
            "remoteTerminationConfirmed": self.state.get("remoteTerminationConfirmed"),
        }
        await self.persist_state(self.state)

        operation = await self._reconcile(client, idempotency_key)
        if operation is None:
            remote_inputs = {
                port: await self._upload(client, port, item)
                for port, item in inputs.items()
            }
            request = {
                "operationType": self.binding["operationType"],
                "clientRequestId": idempotency_key,
                "inputs": remote_inputs,
                "parameters": self.lease["parameters"],
            }
            operation = await self._submit(client, request, idempotency_key)
        operation_id = operation.get("operationId")
        if not isinstance(operation_id, str) or not operation_id:
            raise RemoteOperationError("REMOTE_PROTOCOL_INVALID", self.state)
        if self.state.get("remoteTaskId") not in {None, operation_id}:
            raise RemoteOperationError("REMOTE_PROTOCOL_INVALID", self.state)
        self.state["remoteTaskId"] = operation_id
        await self.persist_state(self.state)

        operation = await self._poll(client, operation_id, operation)
        if operation["status"] != "succeeded":
            self.state["remoteTerminationConfirmed"] = operation["status"] in _TERMINAL
            await self.persist_state(self.state)
            raise RemoteOperationError("REMOTE_OPERATION_FAILED", self.state)
        result = await self._get_json(
            client, f"/api/v1/operations/{quote(operation_id, safe='')}/results"
        )
        return await self._materialize(
            client, operation_id, result, version, stable_inputs
        )

    def _validate_version(self, value: dict) -> None:
        if (
            value.get("apiVersion") != self.service["apiVersion"]
            or value.get("serviceVersion") not in self.service["allowedServiceVersions"]
            or value.get("modelVersion") not in self.service["allowedModelVersions"]
        ):
            raise RemoteOperationError("REMOTE_VERSION_MISMATCH", self.state)

    def _local_inputs(self) -> tuple[dict, dict]:
        local: dict[str, dict] = {}
        stable: dict[str, dict] = {}
        for port, values in self.lease["inputs"].items():
            if (
                not _PORT.fullmatch(port)
                or not isinstance(values, list)
                or len(values) != 1
            ):
                raise RemoteOperationError("REMOTE_PROTOCOL_INVALID", self.state)
            item = values[0]
            path = Path(item["path"])
            if not path.is_file() or path.is_symlink():
                raise RemoteOperationError("REMOTE_INTEGRITY_FAILED", self.state)
            digest, size = _file_identity(path)
            local[port] = {**item, "path": path, "sha256": digest, "sizeBytes": size}
            stable[port] = {
                "mediaType": item["mediaType"],
                "format": item["format"],
                "sizeBytes": size,
                "sha256": digest,
            }
        return local, stable

    async def _upload(self, client: httpx.AsyncClient, port: str, item: dict) -> dict:
        upload_limits = self.capabilities.get("uploads")
        if not isinstance(upload_limits, dict):
            raise RemoteOperationError("REMOTE_PROTOCOL_INVALID", self.state)
        maximum = upload_limits.get("maxArtifactBytes")
        if type(maximum) is not int or item["sizeBytes"] > maximum:
            raise RemoteOperationError("REMOTE_INTEGRITY_FAILED", self.state)
        created = await self._post_json(
            client,
            "/api/v1/uploads",
            {
                "name": f"{port}.{item['format']}",
                "sizeBytes": item["sizeBytes"],
                "sha256": item["sha256"],
                "mediaType": item["mediaType"],
                "format": item["format"],
            },
            expected={200, 201},
        )
        upload_id = created.get("uploadId")
        part_size = created.get("partSizeBytes")
        advertised = upload_limits.get("uploadPartBytes")
        if (
            not isinstance(upload_id, str)
            or type(part_size) is not int
            or part_size <= 0
            or part_size != advertised
        ):
            raise RemoteOperationError("REMOTE_PROTOCOL_INVALID", self.state)
        with item["path"].open("rb") as stream:
            part_number = 0
            while chunk := stream.read(part_size):
                part_digest = "sha256:" + hashlib.sha256(chunk).hexdigest()
                response = await client.put(
                    f"/api/v1/uploads/{quote(upload_id, safe='')}/parts/{part_number}",
                    content=chunk,
                    headers={"X-Content-SHA256": part_digest},
                )
                self._check_response(response, {200})
                part_number += 1
        completed = await self._post_json(
            client,
            f"/api/v1/uploads/{quote(upload_id, safe='')}/complete",
            None,
            expected={200},
        )
        expected = {
            "mediaType": item["mediaType"],
            "format": item["format"],
            "sizeBytes": item["sizeBytes"],
            "sha256": item["sha256"],
        }
        if any(completed.get(name) != value for name, value in expected.items()):
            raise RemoteOperationError("REMOTE_INTEGRITY_FAILED", self.state)
        artifact_id = completed.get("artifactId")
        if not isinstance(artifact_id, str) or not artifact_id:
            raise RemoteOperationError("REMOTE_PROTOCOL_INVALID", self.state)
        return {"artifactId": artifact_id, **expected}

    async def _reconcile(self, client: httpx.AsyncClient, key: str) -> dict | None:
        path = "/api/v1/operations/by-client-request/" + quote(key, safe="")
        for attempt in range(3):
            try:
                response = await client.get(path)
            except httpx.TransportError:
                if attempt == 2:
                    raise RemoteOperationError(
                        "REMOTE_SUBMISSION_UNCERTAIN", self.state
                    )
                await asyncio.sleep(0.25 * (attempt + 1))
                continue
            if response.status_code == 404:
                return None
            self._check_response(response, {200})
            return self._response_json(response)
        raise AssertionError("unreachable")

    async def _submit(self, client: httpx.AsyncClient, request: dict, key: str) -> dict:
        try:
            response = await client.post("/api/v1/operations", json=request)
        except httpx.TransportError:
            response = None
        if response is not None and response.status_code in {200, 201, 202}:
            return self._response_json(response)
        if response is not None and response.status_code < 500:
            self._check_response(response, {200, 201, 202})
        for attempt in range(4):
            await asyncio.sleep(0.25 * (attempt + 1))
            operation = await self._reconcile(client, key)
            if operation is not None:
                return operation
        raise RemoteOperationError("REMOTE_SUBMISSION_UNCERTAIN", self.state)

    async def _poll(
        self, client: httpx.AsyncClient, operation_id: str, current: dict
    ) -> dict:
        while True:
            status = current.get("status")
            if status not in {"pending", "running", *_TERMINAL}:
                raise RemoteOperationError("REMOTE_PROTOCOL_INVALID", self.state)
            self.state.update(remoteStatus=status, lastPolledAt=_now())
            await self.persist_state(self.state)
            if status in _TERMINAL:
                return current
            await asyncio.sleep(self.service["pollIntervalSeconds"])
            current = await self._get_json(
                client, f"/api/v1/operations/{quote(operation_id, safe='')}"
            )

    async def _materialize(
        self,
        client: httpx.AsyncClient,
        operation_id: str,
        result: dict,
        version: dict,
        stable_inputs: dict,
    ) -> RemoteExecutionResult:
        if (
            result.get("operationId") != operation_id
            or result.get("serviceVersion") != version["serviceVersion"]
            or result.get("modelVersion") != version["modelVersion"]
        ):
            raise RemoteOperationError("REMOTE_VERSION_MISMATCH", self.state)
        contracts = {item["name"]: item for item in self.lease["outputContracts"]}
        artifact_contracts = {
            name: item
            for name, item in contracts.items()
            if item["kind"] == "artifact" and name != "run_receipt"
        }
        value_ports = {
            name for name, item in contracts.items() if item["kind"] == "value"
        }
        artifacts = result.get("artifacts")
        values = result.get("values")
        if (
            not isinstance(artifacts, list)
            or any(not isinstance(item, dict) for item in artifacts)
            or len(artifacts) != len(artifact_contracts)
            or {item.get("port") for item in artifacts} != set(artifact_contracts)
            or not isinstance(values, dict)
            or set(values) != value_ports
        ):
            raise RemoteOperationError("REMOTE_PROTOCOL_INVALID", self.state)
        remote_receipt = await self._get_json(
            client, f"/api/v1/operations/{quote(operation_id, safe='')}/receipt"
        )
        if (
            remote_receipt.get("remoteTaskId") != operation_id
            or remote_receipt.get("status") != "succeeded"
        ):
            raise RemoteOperationError("REMOTE_PROTOCOL_INVALID", self.state)
        output_root = Path(self.lease["workspace"]) / "outputs"
        outputs = []
        receipt_artifacts = []
        for item in artifacts:
            port = item["port"]
            contract = artifact_contracts[port]
            if (
                type(item.get("sizeBytes")) is not int
                or item["sizeBytes"] < 0
                or _DIGEST.fullmatch(str(item.get("sha256", ""))) is None
                or {"mediaType": item.get("mediaType"), "format": item.get("format")}
                not in contract["formatPairs"]
            ):
                raise RemoteOperationError("REMOTE_INTEGRITY_FAILED", self.state)
            relative = item.get("downloadUrl")
            parsed = urlsplit(relative) if isinstance(relative, str) else None
            if (
                parsed is None
                or not relative.startswith("/")
                or parsed.scheme
                or parsed.netloc
                or parsed.query
                or parsed.fragment
                or ".." in Path(parsed.path).parts
            ):
                raise RemoteOperationError("REMOTE_PROTOCOL_INVALID", self.state)
            target = output_root / port
            await self._download(
                client, relative, target, item["sizeBytes"], item["sha256"]
            )
            outputs.append(
                {
                    "port": port,
                    "path": str(target.relative_to(Path(self.lease["workspace"]))),
                    "mediaType": item["mediaType"],
                    "format": item["format"],
                    "sizeBytes": item["sizeBytes"],
                    "checksum": item["sha256"],
                }
            )
            receipt_artifacts.append(
                {
                    "port": port,
                    "mediaType": item["mediaType"],
                    "format": item["format"],
                    "sizeBytes": item["sizeBytes"],
                    "sha256": item["sha256"],
                }
            )
        self.state.update(
            remoteStatus="succeeded",
            lastPolledAt=_now(),
            remoteTerminationConfirmed=True,
        )
        receipt = {
            "schemaVersion": "run-receipt.v1",
            "serviceId": self.binding["serviceId"],
            "operationType": self.binding["operationType"],
            "serviceVersion": version["serviceVersion"],
            "apiVersion": version["apiVersion"],
            "modelVersion": version["modelVersion"],
            "remoteTaskId": operation_id,
            "requestSha256": self.state["requestSha256"],
            "idempotencyKey": self.state["idempotencyKey"],
            "inputs": stable_inputs,
            "valuesSha256": "sha256:"
            + hashlib.sha256(canonical_json(values)).hexdigest(),
            "artifacts": receipt_artifacts,
            "finishedAt": _now(),
            "status": "succeeded",
            "remoteTerminationConfirmed": True,
            "remoteReceiptSha256": "sha256:"
            + hashlib.sha256(canonical_json(remote_receipt)).hexdigest(),
        }
        receipt_path = output_root / "run_receipt.json"
        receipt_path.write_bytes(canonical_json(receipt))
        receipt_digest, receipt_size = _file_identity(receipt_path)
        if (
            "run_receipt" not in contracts
            or contracts["run_receipt"]["kind"] != "artifact"
            or {
                "mediaType": "application/json",
                "format": "json",
            }
            not in contracts["run_receipt"]["formatPairs"]
        ):
            raise RemoteOperationError("REMOTE_PROTOCOL_INVALID", self.state)
        outputs.append(
            {
                "port": "run_receipt",
                "path": str(receipt_path.relative_to(Path(self.lease["workspace"]))),
                "mediaType": "application/json",
                "format": "json",
                "sizeBytes": receipt_size,
                "checksum": receipt_digest,
            }
        )
        await self.persist_state(self.state)
        return RemoteExecutionResult(outputs=outputs, values=values, state=self.state)

    async def _download(
        self,
        client: httpx.AsyncClient,
        url: str,
        target: Path,
        expected_size: int,
        expected_digest: str,
    ) -> None:
        temporary = target.with_suffix(".part")
        digest = hashlib.sha256()
        size = 0
        try:
            async with client.stream("GET", url) as response:
                self._check_response(response, {200})
                with temporary.open("xb") as stream:
                    async for chunk in response.aiter_bytes():
                        size += len(chunk)
                        if size > expected_size:
                            raise RemoteOperationError(
                                "REMOTE_INTEGRITY_FAILED", self.state
                            )
                        digest.update(chunk)
                        stream.write(chunk)
                    stream.flush()
                    os.fsync(stream.fileno())
            actual = "sha256:" + digest.hexdigest()
            if size != expected_size or actual != expected_digest:
                raise RemoteOperationError("REMOTE_INTEGRITY_FAILED", self.state)
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)

    async def _cancel_safely(self, client: httpx.AsyncClient) -> None:
        operation_id = self.state.get("remoteTaskId")
        if not operation_id:
            self.state["remoteTerminationConfirmed"] = False
            return
        try:
            result = await self._post_json(
                client,
                f"/api/v1/operations/{quote(operation_id, safe='')}/cancel",
                None,
                expected={200, 202},
            )
            confirmed = bool(result.get("remoteTerminationConfirmed", False))
            self.state.update(
                remoteStatus=result.get("status", "cancellation-requested"),
                lastPolledAt=_now(),
                remoteTerminationConfirmed=confirmed,
            )
            try:
                await self.persist_state(self.state)
            except asyncio.CancelledError:
                pass
        except (httpx.HTTPError, RemoteOperationError):
            self.state["remoteTerminationConfirmed"] = False

    async def _get_json(
        self, client: httpx.AsyncClient, path: str, *, anonymous: bool = False
    ) -> dict:
        try:
            response = await client.get(
                path, headers={} if not anonymous else {"Authorization": ""}
            )
        except httpx.TransportError as error:
            raise RemoteOperationError(
                "REMOTE_SUBMISSION_UNCERTAIN", self.state
            ) from error
        self._check_response(response, {200})
        return self._response_json(response)

    async def _post_json(
        self,
        client: httpx.AsyncClient,
        path: str,
        payload: dict | None,
        *,
        expected: set[int],
    ) -> dict:
        try:
            response = await client.post(path, json=payload)
        except httpx.TransportError as error:
            raise RemoteOperationError(
                "REMOTE_SUBMISSION_UNCERTAIN", self.state
            ) from error
        self._check_response(response, expected)
        return self._response_json(response)

    def _check_response(self, response: httpx.Response, expected: set[int]) -> None:
        if 300 <= response.status_code < 400:
            raise RemoteOperationError("REMOTE_PROTOCOL_INVALID", self.state)
        if response.status_code in {401, 403}:
            raise RemoteOperationError("REMOTE_AUTHENTICATION_FAILED", self.state)
        if response.status_code not in expected:
            raise RemoteOperationError("REMOTE_OPERATION_FAILED", self.state)

    def _response_json(self, response: httpx.Response) -> dict:
        try:
            value = response.json()
        except (json.JSONDecodeError, UnicodeDecodeError) as error:
            raise RemoteOperationError("REMOTE_PROTOCOL_INVALID", self.state) from error
        if not isinstance(value, dict):
            raise RemoteOperationError("REMOTE_PROTOCOL_INVALID", self.state)
        return value


async def execute_remote_operation(
    config: dict, lease: dict, persist_state: PersistState
) -> RemoteExecutionResult:
    return await RemoteOperationRunner(config, lease, persist_state).run()
