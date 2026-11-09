import argparse
import hashlib
from importlib.resources import files
import json
import logging
from pathlib import Path
import random
import time

import httpx
import psycopg

from .config import Settings
from .package_conversion import convert_domain_package
from .models import KnowledgePackageV1


LOGGER = logging.getLogger(__name__)
DATABASE_STARTUP_ATTEMPTS = 12


def _migrate_when_database_ready(database) -> None:
    """Bridge the temporary-to-final PostgreSQL server startup handoff."""
    for attempt in range(1, DATABASE_STARTUP_ATTEMPTS + 1):
        try:
            database.migrate()
            return
        except psycopg.OperationalError:
            if attempt == DATABASE_STARTUP_ATTEMPTS:
                raise
            delay = min(attempt, 3)
            LOGGER.warning(
                "DATABASE_STARTUP_RETRY attempt=%d max_attempts=%d delay_seconds=%d",
                attempt,
                DATABASE_STARTUP_ATTEMPTS,
                delay,
            )
            time.sleep(delay)


def _canonical_checksum(value: object) -> str:
    body = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(body).hexdigest()


def _verified_bootstrap_manifest(
    domain_root: Path, lock_path: Path, project: str
) -> tuple[dict, dict, str]:
    """Verify the immutable domain package before reading bootstrap documents."""
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    identity = lock["domainPackage"]
    expected = lock["knowledgeBootstrap"]
    files = identity["files"]
    if _canonical_checksum(files) != identity["checksum"]:
        raise SystemExit("DOMAIN_IDENTITY_MISMATCH")
    if project not in expected["projects"]:
        raise SystemExit("KNOWLEDGE_PROJECTS_INVALID")

    root = domain_root.resolve()
    resolved: set[Path] = set()
    content: dict[str, bytes] = {}
    for relative, checksum in files.items():
        path = Path(relative)
        if (
            not isinstance(relative, str)
            or "\\" in relative
            or path.is_absolute()
            or ".." in path.parts
            or path.as_posix() != relative
        ):
            raise SystemExit("DOMAIN_PATH_INVALID")
        source = (root / path).resolve()
        if not source.is_relative_to(root) or source in resolved:
            raise SystemExit("DOMAIN_PATH_INVALID")
        resolved.add(source)
        raw = source.read_bytes()
        if "sha256:" + hashlib.sha256(raw).hexdigest() != checksum:
            raise SystemExit("DOMAIN_FILE_CHECKSUM_MISMATCH")
        content[relative] = raw

    manifest = json.loads(content["domain-manifest.json"])
    if (
        manifest["domainId"] != identity["domainId"]
        or manifest["version"] != identity["version"]
        or sorted(manifest["knowledgeDocuments"]) != sorted(expected["files"])
        or len(set(expected["files"])) != len(expected["files"])
    ):
        raise SystemExit("DOMAIN_BOOTSTRAP_MISMATCH")
    return manifest, expected, identity["checksum"]


def _prepare_bootstrap_import(
    package: KnowledgePackageV1,
    preflight: dict,
) -> KnowledgePackageV1 | None:
    """Turn a desired domain snapshot into an idempotent CAS import.

    The preflight response is the authoritative current catalog snapshot. Exact
    matches are omitted, while changed documents carry the current version as
    their compare-and-swap precondition. The current-version digest makes a
    failed/stale attempt distinct from a retry against a later catalog state.
    """

    differences = preflight.get("documents")
    if not isinstance(differences, list) or len(differences) != len(package.documents):
        raise SystemExit("KNOWLEDGE_BOOTSTRAP_PREFLIGHT_INVALID")
    by_id = {
        difference.get("knowledgeId"): difference
        for difference in differences
        if isinstance(difference, dict)
    }
    if set(by_id) != {document.knowledge_id for document in package.documents}:
        raise SystemExit("KNOWLEDGE_BOOTSTRAP_PREFLIGHT_INVALID")

    changed = []
    catalog_basis = []
    for document in package.documents:
        difference = by_id[document.knowledge_id]
        content_changed = difference.get("contentChanged")
        current_version = difference.get("currentVersion")
        if not isinstance(content_changed, bool) or (
            current_version is not None and not isinstance(current_version, str)
        ):
            raise SystemExit("KNOWLEDGE_BOOTSTRAP_PREFLIGHT_INVALID")
        if not content_changed:
            continue
        updated = document.model_copy(
            deep=True,
            update={"expected_version": current_version},
        )
        changed.append(updated)
        catalog_basis.append((document.knowledge_id, current_version))

    if not changed:
        return None
    state_digest = _canonical_checksum(catalog_basis).removeprefix("sha256:")[:20]
    key = f"{package.idempotency_key}-{state_digest}"
    return KnowledgePackageV1(
        project_id=package.project_id,
        idempotency_key=key,
        documents=changed,
    )


def _run_bootstrap(client: httpx.Client, base: str, headers: dict, package) -> None:
    response = client.post(
        base + "/knowledge/v3/imports/preflight",
        json=package.model_dump(mode="json", by_alias=True),
        headers=headers,
    )
    response.raise_for_status()
    prepared = _prepare_bootstrap_import(package, response.json())
    if prepared is None:
        print(
            json.dumps(
                {
                    "bootstrapState": "ALREADY_APPLIED",
                    "documentCount": len(package.documents),
                },
                ensure_ascii=False,
            )
        )
        return

    # Recheck the derived compare-and-swap package at the service boundary.
    response = client.post(
        base + "/knowledge/v3/imports/preflight",
        json=prepared.model_dump(mode="json", by_alias=True),
        headers=headers,
    )
    response.raise_for_status()
    if response.json().get("canSubmit") is not True:
        raise SystemExit("KNOWLEDGE_BOOTSTRAP_PREFLIGHT_STALE")
    response = client.post(
        base + "/knowledge/v3/imports",
        json=prepared.model_dump(mode="json", by_alias=True),
        headers=headers,
    )
    response.raise_for_status()
    import_id = response.json()["importId"]
    deadline = time.monotonic() + 1800
    while time.monotonic() < deadline:
        response = client.get(
            base + f"/knowledge/v3/imports/{import_id}",
            headers=headers,
        )
        response.raise_for_status()
        state = response.json()
        if state["state"] == "SUCCEEDED":
            print(
                json.dumps(
                    {
                        "bootstrapState": "APPLIED",
                        "documentCount": len(package.documents),
                        "changedDocumentCount": len(prepared.documents),
                        "import": state,
                    },
                    ensure_ascii=False,
                )
            )
            return
        if state["state"] == "FAILED":
            failure = state.get("error")
            code = failure.get("code") if isinstance(failure, dict) else None
            raise SystemExit(f"KNOWLEDGE_BOOTSTRAP_FAILED:{code or 'UNKNOWN'}")
        time.sleep(0.5)
    raise SystemExit("KNOWLEDGE_BOOTSTRAP_TIMEOUT")


def main():
    logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("migrate")
    sub.add_parser("worker")
    sub.add_parser("serve")
    convert = sub.add_parser("convert")
    convert.add_argument("file", type=Path)
    convert.add_argument("--project", required=True)
    convert.add_argument("--key", required=True)
    convert.add_argument("--prefix", default="")
    sub.add_parser("contracts")
    bootstrap = sub.add_parser("bootstrap")
    bootstrap.add_argument("--domain", type=Path, required=True)
    bootstrap.add_argument("--lock", type=Path, required=True)
    bootstrap.add_argument("--url", required=True)
    bootstrap.add_argument("--project", required=True)
    args = parser.parse_args()
    if args.command == "convert":
        print(
            convert_domain_package(
                json.loads(args.file.read_text()),
                project=args.project,
                idempotency_key=args.key,
                prefix=args.prefix,
            ).model_dump_json(indent=2)
        )
        return
    if args.command == "contracts":
        print(
            files("platform_knowledge_next")
            .joinpath("contracts/observation-tools.v5.json")
            .read_text()
            .rstrip()
        )
        return
    if args.command == "bootstrap":
        manifest, expected, domain_checksum = _verified_bootstrap_manifest(
            args.domain, args.lock, args.project
        )
        documents = []
        for index, relative in enumerate(manifest["knowledgeDocuments"]):
            converted = convert_domain_package(
                json.loads((args.domain / relative).read_text()),
                project=args.project,
                idempotency_key=f"part-{index}",
            )
            documents.extend(converted.documents)
        if (
            len(documents) != expected["documentCount"]
            or sum(len(document.units) for document in documents)
            != expected["chunkCount"]
        ):
            raise SystemExit("KNOWLEDGE_BOOTSTRAP_IDENTITY_MISMATCH")
        package = KnowledgePackageV1(
            project_id=args.project,
            idempotency_key=(
                "domain-"
                + manifest["version"].replace(".", "-")
                + "-"
                + domain_checksum.removeprefix("sha256:")[:24]
            ),
            documents=documents,
        )
        base = args.url.rstrip("/")
        headers = {
            "X-Project-ID": args.project,
            "X-Platform-Subject": "platform-knowledge-bootstrap",
            "X-Platform-Display-Name": "Platform Knowledge Bootstrap",
            "X-Platform-Roles": "admin",
        }
        with httpx.Client(timeout=30, trust_env=False) as client:
            _run_bootstrap(client, base, headers, package)
        return
    settings = Settings.from_env()
    from .database import Database

    if args.command == "migrate":
        db = Database(settings)
        _migrate_when_database_ready(db)
    elif args.command == "serve":
        import uvicorn

        uvicorn.run(
            "platform_knowledge_next.api:create_app",
            factory=True,
            host="0.0.0.0",
            port=8000,
        )
    else:
        from platform_embedding_client import EmbeddingClient

        from .data_adapter import DataAdapter
        from .ingestion import Importer

        db = Database(settings, pool_min=1, pool_max=4, pool_timeout=3)
        db.open()
        db.check()
        embeddings, data = (
            EmbeddingClient(settings.embedding_url),
            DataAdapter(settings),
        )
        importer = Importer(db, embeddings, data)
        try:
            LOGGER.info(
                "Knowledge worker capacity configured slots=%d pool_max=%d",
                1,
                4,
            )
            delay = 0.5
            while True:
                if not settings.read_only and importer.run_once():
                    delay = 0.5
                    continue
                time.sleep(delay + random.uniform(0, delay * 0.1))
                delay = min(delay * 2, 2.0)
        finally:
            embeddings.close()
            data.close()
            db.close()


if __name__ == "__main__":
    main()
