from dataclasses import dataclass
import os
from pathlib import Path
import re
from urllib.parse import quote, urlsplit, urlunsplit

from platform_protocol_contracts import LocalIdentity


def _database_url() -> str:
    value = os.environ["DATA_DATABASE_URL"]
    password_file = os.environ.get("DATA_DATABASE_PASSWORD_FILE")
    if not password_file:
        return value
    path = Path(password_file)
    if not path.is_absolute() or path.is_symlink() or not path.is_file():
        raise ValueError("DATA_DATABASE_PASSWORD_FILE_INVALID")
    secret = path.read_text(encoding="utf-8").strip()
    if not secret or len(secret) > 4096 or any(character in secret for character in "\r\n"):
        raise ValueError("DATA_DATABASE_PASSWORD_FILE_INVALID")
    parsed = urlsplit(value)
    if (
        parsed.scheme not in {"postgres", "postgresql"}
        or parsed.username is None
        or parsed.password is not None
        or parsed.hostname is None
        or parsed.fragment
    ):
        raise ValueError("DATA_DATABASE_URL_WITH_SECRET_FILE_INVALID")
    raw_user = parsed.netloc.rsplit("@", 1)[0]
    host = f"[{parsed.hostname}]" if ":" in parsed.hostname else parsed.hostname
    port = f":{parsed.port}" if parsed.port is not None else ""
    netloc = f"{raw_user}:{quote(secret, safe='')}@{host}{port}"
    return urlunsplit((parsed.scheme, netloc, parsed.path, parsed.query, ""))


@dataclass(frozen=True)
class Settings:
    dsn: str
    root: Path
    project: str = "local-data"
    query_seconds: int = 60
    preview_seconds: int = 60
    prepare_seconds: int = 3600
    query_memory_mb: int = 512
    temp_mb: int = 2048
    result_bytes: int = 4 * 1024 * 1024
    result_rows: int = 10000
    preview_input_bytes: int = 256 * 1024**2
    preview_expanded_bytes: int = 512 * 1024**2
    preview_rows: int = 1000
    preview_columns: int = 100
    preview_coordinates: int = 200000
    upload_bytes: int = 1024**3
    upload_slots: int = 4
    json_validation_bytes: int = 16 * 1024**2
    lease_seconds: int = 30
    result_hours: int = 24
    worker_slots: int = 2
    read_only: bool = False
    product: str = "platform"
    identity_mode: str = "local"
    local_identity: LocalIdentity = LocalIdentity()
    openlineage_url: str | None = None
    quality_webhook_url: str | None = None
    mlflow_adapter_url: str | None = None

    def __post_init__(self):
        for name in (
            "query_seconds",
            "preview_seconds",
            "prepare_seconds",
            "query_memory_mb",
            "temp_mb",
            "result_bytes",
            "result_rows",
            "preview_input_bytes",
            "preview_expanded_bytes",
            "preview_rows",
            "preview_columns",
            "preview_coordinates",
            "upload_bytes",
            "upload_slots",
            "json_validation_bytes",
            "lease_seconds",
            "result_hours",
            "worker_slots",
        ):
            if getattr(self, name) <= 0:
                raise ValueError(f"{name} must be positive")
        if self.worker_slots > 8:
            raise ValueError("worker_slots must not exceed 8")
        if (
            not self.project
            or re.fullmatch(r"[a-z][a-z0-9_.-]*", self.product) is None
            or not self.root.is_absolute()
        ):
            raise ValueError("product, project and absolute data root required")
        if self.identity_mode not in {"local", "gateway"}:
            raise ValueError("identity_mode must be local or gateway")
        for name in (
            "openlineage_url",
            "quality_webhook_url",
            "mlflow_adapter_url",
        ):
            value = getattr(self, name)
            if value is None:
                continue
            parsed = urlsplit(value)
            if (
                parsed.scheme not in {"http", "https"}
                or not parsed.netloc
                or parsed.username is not None
                or parsed.password is not None
                or parsed.fragment
            ):
                raise ValueError(f"{name} must be an HTTP(S) URL without credentials")

    @classmethod
    def env(cls):
        fields = {
            name: int(os.environ[f"DATA_{name.upper()}"])
            for name in (
                "query_seconds",
                "preview_seconds",
                "prepare_seconds",
                "query_memory_mb",
                "temp_mb",
                "result_bytes",
                "result_rows",
                "preview_input_bytes",
                "preview_expanded_bytes",
                "preview_rows",
                "preview_columns",
                "preview_coordinates",
                "upload_bytes",
                "upload_slots",
                "json_validation_bytes",
                "lease_seconds",
                "result_hours",
                "worker_slots",
            )
            if f"DATA_{name.upper()}" in os.environ
        }
        return cls(
            _database_url(),
            Path(os.environ["DATA_ROOT"]),
            project=os.environ.get("DATA_PROJECT", "local-data"),
            read_only=os.environ.get("DATA_READ_ONLY") == "1",
            product=os.environ.get("PLATFORM_PRODUCT_ID", "platform"),
            identity_mode=os.environ.get("PLATFORM_IDENTITY_MODE", "local"),
            local_identity=LocalIdentity.from_environment(),
            openlineage_url=os.environ.get("OPENLINEAGE_URL") or None,
            quality_webhook_url=os.environ.get("DATA_QUALITY_WEBHOOK_URL") or None,
            mlflow_adapter_url=os.environ.get("MLFLOW_ADAPTER_URL") or None,
            **fields,
        )
