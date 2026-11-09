"""One-way migration from a source dotenv file into the XDG config domain."""

from __future__ import annotations

import hashlib
import os
import re
import secrets
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

from .contracts import canonical_bytes, sha256_digest
from .xdg_layout import LifecyclePaths, ensure_private_roots, owned_path


KEY = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")
SECRET_FILES = {
    "PLATFORM_AGENT_API_KEY": "agent-api-key",
    "PLATFORM_INTENT_ROUTER_API_KEY": "intent-router-api-key",
    "PLATFORM_EMBEDDING_API_KEY": "embedding-api-key",
}


class ConfigRefusalError(RuntimeError):
    """Configuration cannot be migrated without exposing or losing a value."""


ConfigRefusal = ConfigRefusalError


def _sha256(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _parse(path: Path) -> tuple[list[tuple[str, str]], list[str]]:
    if path.is_symlink() or not path.is_file():
        raise ConfigRefusal("SOURCE_ENV_INVALID")
    entries: list[tuple[str, str]] = []
    comments: list[str] = []
    seen: set[str] = set()
    for number, raw in enumerate(path.read_text().splitlines(), start=1):
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            comments.append(raw)
            continue
        candidate = stripped.removeprefix("export ").lstrip()
        name, separator, value = candidate.partition("=")
        name = name.strip()
        if not separator or KEY.fullmatch(name) is None or name in seen:
            raise ConfigRefusal(f"SOURCE_ENV_LINE_INVALID:{number}")
        seen.add(name)
        entries.append((name, value))
    return entries, comments


def _looks_sensitive_url(value: str) -> bool:
    unquoted = value.strip().strip("\"'")
    try:
        parsed = urlsplit(unquoted)
    except ValueError:
        return True
    return parsed.password is not None or parsed.username is not None


def migration_plan(
    source_root: Path, paths: LifecyclePaths
) -> dict:
    source = source_root / ".env"
    entries, _ = _parse(source)
    secret_keys = []
    for name, value in entries:
        upper = name.upper()
        sensitive_name = upper in SECRET_FILES or upper.endswith(
            ("_TOKEN", "_SECRET", "_PASSWORD", "_PRIVATE_KEY", "_API_KEY")
        )
        if sensitive_name:
            if name not in SECRET_FILES:
                raise ConfigRefusal(f"UNMAPPED_SECRET_VARIABLE:{name}")
            secret_keys.append(name)
        elif ("URL" in upper or upper.endswith(("_DSN", "_DATABASE_URL"))) and _looks_sensitive_url(value):
            raise ConfigRefusal(f"CREDENTIAL_BEARING_URL_REJECTED:{name}")
    destination = owned_path("profiles/research.env", paths.config)
    if destination.exists() or destination.is_symlink():
        raise ConfigRefusal("DEVELOPMENT_PROFILE_EXISTS")
    body = {
        "schemaVersion": "config-migration-plan.v1",
        "action": "source-env-to-xdg",
        "source": str(source),
        "sourceSha256": _sha256(source),
        "destination": str(destination),
        "keys": sorted(name for name, _ in entries),
        "secretKeys": sorted(secret_keys),
        "sourceWillBeDeleted": True,
    }
    return body | {"confirmationDigest": sha256_digest(canonical_bytes(body))}


def _write_new(path: Path, content: bytes, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
    except Exception:
        path.unlink(missing_ok=True)
        raise


def apply_migration(
    source_root: Path,
    paths: LifecyclePaths,
    plan: dict,
    confirmation: str,
) -> dict:
    expected = plan.get("confirmationDigest")
    body = {key: value for key, value in plan.items() if key != "confirmationDigest"}
    if expected != sha256_digest(canonical_bytes(body)) or confirmation != expected:
        raise ConfigRefusal("CONFIG_MIGRATION_CONFIRMATION_MISMATCH")
    current = migration_plan(source_root, paths)
    if current != plan:
        raise ConfigRefusal("CONFIG_MIGRATION_PLAN_CHANGED")
    ensure_private_roots(paths, ("config", "state"))
    source = source_root / ".env"
    entries, comments = _parse(source)
    secrets_root = owned_path("secrets/research", paths.config)
    secrets_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    profile_lines = [
        "# Migrated from source/.env for the local research profile.",
        *comments,
    ]
    written_secrets: list[Path] = []
    for name, value in entries:
        if name in SECRET_FILES:
            target = owned_path(SECRET_FILES[name], secrets_root)
            # Compose implements local file secrets as bind mounts and cannot
            # remap an owner-only host file to the non-root service UID. The
            # enclosing directory is 0700; make the leaf immutable/read-only so
            # the container can read it without exposing the directory tree.
            _write_new(target, (value.strip().strip("\"'") + "\n").encode(), 0o444)
            written_secrets.append(target)
            continue
        if name == "PLATFORM_OPERATOR_PORT":
            continue
        if name == "PLATFORM_SECRETS_DIR":
            value = str(secrets_root)
        elif name == "PLATFORM_GATEWAY_PORT":
            value = "28186"
        profile_lines.append(f"{name}={value}")
    destination = Path(plan["destination"])
    content = ("\n".join(profile_lines).rstrip() + "\n").encode()
    _write_new(destination, content, 0o600)
    if destination.read_bytes() != content or destination.stat().st_mode & 0o077:
        raise ConfigRefusal("CONFIG_MIGRATION_WRITE_FAILED")
    source.unlink()
    receipt = {
        "schemaVersion": "config-migration-receipt.v1",
        "action": "source-env-to-xdg",
        "status": "PASS",
        "sourceSha256": plan["sourceSha256"],
        "destination": str(destination),
        "keys": plan["keys"],
        "secretFiles": [path.name for path in written_secrets],
        "sourceDeleted": not source.exists(),
        "confirmationDigest": expected,
        "completedAt": datetime.now(UTC).replace(microsecond=0).isoformat(),
    }
    audit = owned_path("config", paths.audit)
    audit.mkdir(parents=True, exist_ok=True, mode=0o700)
    receipt_path = audit / f"env-migration-{secrets.token_hex(8)}.json"
    _write_new(receipt_path, canonical_bytes(receipt), 0o600)
    return receipt | {"receiptPath": str(receipt_path)}


def status(
    source_root: Path, paths: LifecyclePaths, profile_name: str = "research"
) -> dict:
    if profile_name != "research":
        raise ConfigRefusal("CONFIG_PROFILE_INVALID")
    profile = paths.config / f"profiles/{profile_name}.env"
    source = source_root / ".env"
    secure = profile.is_file() and not profile.is_symlink() and not (
        profile.stat().st_mode & 0o077
    )
    secrets_root = paths.config / f"secrets/{profile_name}"
    secrets_root_secure = (
        secrets_root.is_dir()
        and not secrets_root.is_symlink()
        and secrets_root.stat().st_uid == os.getuid()
        and not secrets_root.stat().st_mode & 0o077
    )
    required_secrets = (
        "agent-api-key",
        "intent-router-api-key",
        "data-db-password",
        "knowledge-db-password",
    )
    secret_status = {}
    for name in required_secrets:
        path = secrets_root / name
        secret_status[name] = (
            "PASS"
            if path.is_file()
            and not path.is_symlink()
            and path.stat().st_uid == os.getuid()
            and secrets_root_secure
            and path.stat().st_mode & 0o777 == 0o444
            and bool(path.read_bytes().strip())
            else "MISSING_OR_INSECURE"
        )
    ready = secure and secrets_root_secure and not source.exists() and all(
        value == "PASS" for value in secret_status.values()
    )
    return {
        "status": "PASS" if ready else "NOT_READY",
        "sourceEnv": "ABSENT" if not source.exists() else "PRESENT",
        "profile": profile_name,
        "profilePath": str(profile),
        "profileMode": oct(profile.stat().st_mode & 0o777) if profile.exists() else None,
        "secretFiles": secret_status,
    }
