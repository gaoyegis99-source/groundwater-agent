#!/usr/bin/env python3
"""Verify that each running service's OpenAPI matches its Module v9 snapshot."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[2]
SOURCE_ROOTS = (
    "packages/protocol-contracts/src",
    "packages/embedding-client/src",
    "packages/workflow-compiler/src",
    "packages/operator-pack/src",
    "services/data/client/src",
    "services/executor/client/src",
    "kernel/src",
    "services/data/src",
    "services/knowledge-next/src",
    "services/executor/src",
)


def _encoded(value: dict) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


def runtime_documents(root: Path = ROOT) -> dict[Path, dict]:
    for relative in reversed(SOURCE_ROOTS):
        source = str(root / relative)
        if source not in sys.path:
            sys.path.insert(0, source)

    from platform_data_service.api.app import create_app as create_data_app
    from platform_data_service.settings import Settings as DataSettings
    from platform_executor_service.app import create_app as create_executor_app
    from platform_kernel.api import create_app as create_kernel_app
    from platform_knowledge_next.api import create_app as create_knowledge_app
    from platform_knowledge_next.config import Settings as KnowledgeSettings

    with TemporaryDirectory(prefix="runtime-openapi-v12-") as directory:
        data_settings = DataSettings(
            "postgresql://contract.invalid/contract",
            Path(directory).resolve(),
            project="contract",
        )
        knowledge_settings = KnowledgeSettings(
            "postgresql://contract.invalid/contract",
            project="contract",
            embedding_url="http://embedding.invalid",
            data_url="http://data.invalid",
        )
        return {
            root / "kernel/contracts/openapi-v12.json": create_kernel_app(
                application=object()
            ).openapi(),
            root / "services/data/contracts/openapi-v3.json": create_data_app(
                data_settings
            ).openapi(),
            root
            / "services/knowledge-next/contracts/openapi-v3.json": create_knowledge_app(
                knowledge_settings, embeddings=object()
            ).openapi(),
            root / "services/executor/contracts/openapi-v6.json": create_executor_app(
                SimpleNamespace()
            ).openapi(),
        }


def _contract_violations(path: Path, document: dict) -> list[str]:
    violations: list[str] = []
    operation_ids: list[str] = []
    for route, path_item in document.get("paths", {}).items():
        for method, operation in path_item.items():
            if not isinstance(operation, dict):
                continue
            operation_id = operation.get("operationId")
            if isinstance(operation_id, str):
                operation_ids.append(operation_id)
            else:
                violations.append(f"{path}:{route}:{method}:missing operationId")
            for parameter in operation.get("parameters", []):
                name = str(parameter.get("name", ""))
                if parameter.get("in") in {"path", "query"} and "_" in name:
                    violations.append(
                        f"{path}:{route}:{method}:snake_case {parameter.get('in')} parameter"
                    )
                if parameter.get("in") == "path" and name == "id":
                    violations.append(
                        f"{path}:{route}:{method}:non-semantic path parameter"
                    )
            for status, response in operation.get("responses", {}).items():
                if not str(status).startswith("2") or str(status) == "204":
                    continue
                contents = response.get("content", {})
                if not contents:
                    violations.append(
                        f"{path}:{route}:{method}:{status}:missing success content"
                    )
                    continue
                for media_type, content in contents.items():
                    schema = content.get("schema")
                    if not schema:
                        violations.append(
                            f"{path}:{route}:{method}:{status}:{media_type}:empty schema"
                        )
                    elif media_type.endswith("/json") and (
                        schema == {"type": "object"}
                        or schema == {"type": "array"}
                        or schema.get("additionalProperties") is True
                    ):
                        violations.append(
                            f"{path}:{route}:{method}:{status}:generic JSON schema"
                        )
    for schema_name, schema in document.get("components", {}).get("schemas", {}).items():
        for property_name in schema.get("properties", {}):
            if "_" in property_name:
                violations.append(
                    f"{path}:{schema_name}:snake_case schema property:{property_name}"
                )
    duplicates = sorted({item for item in operation_ids if operation_ids.count(item) > 1})
    violations.extend(f"{path}:duplicate operationId:{item}" for item in duplicates)
    return violations


def check(root: Path = ROOT, *, write: bool = False) -> list[str]:
    stale: list[str] = []
    for path, document in runtime_documents(root).items():
        stale.extend(_contract_violations(path, document))
        content = _encoded(document)
        if write:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        elif not path.is_file() or path.read_bytes() != content:
            stale.append(str(path.relative_to(root)))
    return stale


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args()
    stale = check(args.root.resolve(), write=args.write)
    if stale:
        raise SystemExit("RUNTIME_OPENAPI_STALE: " + ", ".join(stale))
    print("Runtime OpenAPI snapshots PASS")


if __name__ == "__main__":
    main()
