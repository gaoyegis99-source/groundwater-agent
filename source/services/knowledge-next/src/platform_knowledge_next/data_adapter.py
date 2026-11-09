from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import httpx
from platform_data_client import DataClient
from platform_data_client.generated import operations
from platform_protocol_contracts import (
    ApiProblem,
    ProtocolError,
    TransportError,
    validate_origin,
)

from .errors import KnowledgeProblemError


class DataTransientError(KnowledgeProblemError):
    def __init__(self, retry_after=None):
        super().__init__("DATA_UNAVAILABLE", 503)
        self.retry_after = retry_after


def retry_after(value, now):
    if value is None:
        return None
    if value.isascii() and value.isdigit():
        # Cap before converting untrusted arbitrarily large integers.
        return min(int(value[:10]), 601) if len(value) <= 10 else 601
    try:
        stamp = parsedate_to_datetime(value)
        if stamp.tzinfo is None:
            return None
        return max(0, (stamp - now).total_seconds())
    except (ValueError, TypeError, OverflowError):
        return None


class DataAdapter:
    def __init__(self, settings, client=None, clock=None):
        self.settings = settings
        if settings.data_url:
            try:
                validate_origin(settings.data_url)
            except ValueError as error:
                raise KnowledgeProblemError("DATA_ADDRESS_INVALID", 503) from error
        self._owns_client = client is None
        self.client = client or httpx.Client(timeout=15, trust_env=False)
        self.data = (
            DataClient(
                settings.data_url,
                settings.project,
                client_type="service",
                client=self.client,
                identity_headers={
                    "X-Platform-Subject": "platform-knowledge",
                    "X-Platform-Display-Name": "Platform Knowledge",
                    "X-Platform-Roles": "researcher",
                },
            )
            if settings.data_url
            else None
        )
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.cache = {}

    def begin_attempt(self):
        self.cache.clear()

    def _get(self, key, operation, *, path=None):
        if key in self.cache:
            return self.cache[key]
        if not self.settings.data_url:
            raise KnowledgeProblemError("DATA_NOT_CONFIGURED", 503)
        try:
            if self.data is None:
                raise KnowledgeProblemError("DATA_NOT_CONFIGURED", 503)
            payload = self.data.call(operation, path=path).model_dump(
                mode="json", by_alias=True
            )
            self.cache[key] = payload
            return payload
        except (httpx.InvalidURL, httpx.UnsupportedProtocol):
            raise KnowledgeProblemError("DATA_ADDRESS_INVALID", 503) from None
        except TransportError as error:
            if isinstance(error.__cause__, httpx.ProtocolError):
                raise KnowledgeProblemError("DATA_PROTOCOL_ERROR", 502) from error
            raise DataTransientError() from None
        except ProtocolError as error:
            code = (
                "DATA_RESPONSE_INVALID"
                if str(error) == "RESPONSE_CONTRACT_INVALID"
                else "DATA_PROTOCOL_ERROR"
            )
            raise KnowledgeProblemError(code, 502) from error
        except ApiProblem as error:
            status = error.problem.status
            if status in (408, 429, 500, 502, 503, 504):
                raise DataTransientError(
                    retry_after(error.headers.get("retry-after"), self.clock())
                ) from None
            if status in (401, 403):
                raise KnowledgeProblemError("DATA_ACCESS_DENIED", 503) from None
            if status == 404:
                raise KnowledgeProblemError("DATA_BINDING_NOT_FOUND", 422) from None
            raise KnowledgeProblemError("DATA_BINDING_REJECTED", 422) from None
        except ValueError:
            raise KnowledgeProblemError("DATA_RESPONSE_INVALID", 502) from None

    def validate(self, binding):
        if binding.project_id != self.settings.project:
            raise KnowledgeProblemError("BINDING_PROJECT_MISMATCH", 422)
        caps = self._get("capabilities", operations.DATA_CAPABILITIES)
        if not isinstance(caps.get("project"), str):
            raise KnowledgeProblemError("DATA_RESPONSE_INVALID", 502)
        if caps["project"] != self.settings.project:
            raise KnowledgeProblemError("DATA_PROJECT_MISMATCH", 422)
        version = self._get(
            f"version:{binding.version_id}",
            operations.DATA_GET_VERSION,
            path={"versionId": binding.version_id},
        )
        semantic = self._get(
            f"semantic:{binding.version_id}:{binding.semantic_id}",
            operations.DATA_GET_SEMANTIC_REVISION,
            path={
                "versionId": binding.version_id,
                "semanticId": binding.semantic_id,
            },
        )
        if any(
            not isinstance(row.get(key), str)
            for row, keys in (
                (version, ("versionId", "datasetId")),
                (semantic, ("semanticId", "versionId")),
            )
            for key in keys
        ):
            raise KnowledgeProblemError("DATA_RESPONSE_INVALID", 502)
        if (
            version["versionId"] != binding.version_id
            or version["datasetId"] != binding.dataset_id
            or semantic["semanticId"] != binding.semantic_id
            or semantic["versionId"] != binding.version_id
        ):
            raise KnowledgeProblemError("DATA_BINDING_MISMATCH", 422)
        body = semantic.get("body")
        if not isinstance(body, dict) or not isinstance(body.get("fields"), dict):
            raise KnowledgeProblemError("DATA_RESPONSE_INVALID", 502)
        if binding.field_id and binding.field_id not in body["fields"]:
            raise KnowledgeProblemError("DATA_FIELD_NOT_FOUND", 422)

    def close(self):
        if self.data is not None:
            self.data.close()
        if self._owns_client:
            self.client.close()
