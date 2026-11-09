from dataclasses import dataclass
import os
from pathlib import Path
import re
from urllib.parse import quote, urlsplit, urlunsplit

from platform_protocol_contracts import LocalIdentity

MODEL_PROFILE = "qwen3-embedding-4b-q4:1024:title-summary-content-v1"
INDEX_GENERATION = "qwen3-1024-ngram-v1"


def _database_url() -> str:
    value = os.environ["KNOWLEDGE_DATABASE_URL"]
    password_file = os.environ.get("KNOWLEDGE_DATABASE_PASSWORD_FILE")
    if not password_file:
        return value
    path = Path(password_file)
    if not path.is_absolute() or path.is_symlink() or not path.is_file():
        raise ValueError("KNOWLEDGE_DATABASE_PASSWORD_FILE_INVALID")
    secret = path.read_text(encoding="utf-8").strip()
    if not secret or len(secret) > 4096 or any(character in secret for character in "\r\n"):
        raise ValueError("KNOWLEDGE_DATABASE_PASSWORD_FILE_INVALID")
    parsed = urlsplit(value)
    if (
        parsed.scheme not in {"postgres", "postgresql"}
        or parsed.username is None
        or parsed.password is not None
        or parsed.hostname is None
        or parsed.fragment
    ):
        raise ValueError("KNOWLEDGE_DATABASE_URL_WITH_SECRET_FILE_INVALID")
    raw_user = parsed.netloc.rsplit("@", 1)[0]
    host = f"[{parsed.hostname}]" if ":" in parsed.hostname else parsed.hostname
    port = f":{parsed.port}" if parsed.port is not None else ""
    netloc = f"{raw_user}:{quote(secret, safe='')}@{host}{port}"
    return urlunsplit((parsed.scheme, netloc, parsed.path, parsed.query, ""))


@dataclass(frozen=True)
class Settings:
    database_url: str
    project: str = "local-data"
    embedding_url: str = ""
    data_url: str = ""
    read_only: bool = False
    vector_search: str = "exact"
    embedding_health_path: str = "/health"
    product: str = "platform"
    identity_mode: str = "local"
    local_identity: LocalIdentity = LocalIdentity()

    def __post_init__(self) -> None:
        if re.fullmatch(r"[a-z][a-z0-9_.-]*", self.product) is None:
            raise ValueError("PLATFORM_PRODUCT_ID_INVALID")
        if self.identity_mode not in {"local", "gateway"}:
            raise ValueError("PLATFORM_IDENTITY_MODE_INVALID")

    @classmethod
    def from_env(cls):
        mode = os.getenv("KNOWLEDGE_VECTOR_SEARCH", "exact")
        if mode not in {"exact", "hnsw"}:
            raise ValueError("KNOWLEDGE_VECTOR_SEARCH_INVALID")
        health_path = os.getenv("PLATFORM_EMBEDDING_HEALTH_PATH", "/health")
        if not health_path.startswith("/") or health_path.startswith("//"):
            raise ValueError("PLATFORM_EMBEDDING_HEALTH_PATH_INVALID")
        return cls(
            _database_url(),
            os.getenv("KNOWLEDGE_PROJECT", "local-data"),
            os.getenv("PLATFORM_EMBEDDING_BASE_URL", ""),
            os.getenv("KNOWLEDGE_DATA_URL", ""),
            os.getenv("KNOWLEDGE_READ_ONLY", "0") == "1",
            mode,
            health_path,
            os.getenv("PLATFORM_PRODUCT_ID", "platform"),
            os.getenv("PLATFORM_IDENTITY_MODE", "local"),
            LocalIdentity.from_environment(),
        )
