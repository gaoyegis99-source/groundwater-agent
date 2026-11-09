#!/usr/bin/env python3
"""Build the deterministic public v12 OpenAPI contract from module contracts."""

from __future__ import annotations

import argparse
from copy import deepcopy
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]


def _load_product(root: Path, product_id: str | None) -> tuple[str, dict[str, Any]]:
    import sys

    assembler_path = str(root / "platform" / "assembler")
    if assembler_path not in sys.path:
        sys.path.insert(0, assembler_path)
    from assembler_manifests import _load_manifest, available_products

    products = available_products(root)
    if product_id is None:
        if len(products) != 1:
            choices = ",".join(products) or "none"
            raise ValueError(f"PRODUCT_SELECTION_REQUIRED:{choices}")
        product_id = products[0]
    elif product_id not in products:
        raise ValueError(f"PRODUCT_NOT_FOUND:{product_id}")
    _, manifest = _load_manifest(root, f"{product_id}/product-manifest.json")
    return product_id, manifest


def _output_path(root: Path, product_id: str, manifest: dict[str, Any]) -> Path:
    return (
        root
        / "products"
        / product_id
        / "contracts"
        / f"public-api-{manifest['publicApiVersion']}.json"
    )


def _rewrite_refs(value: Any, prefix: str) -> Any:
    if isinstance(value, dict):
        return {key: _rewrite_refs(item, prefix) for key, item in value.items()}
    if isinstance(value, list):
        return [_rewrite_refs(item, prefix) for item in value]
    if isinstance(value, str) and value.startswith("#/components/schemas/"):
        return value.replace(
            "#/components/schemas/",
            f"#/components/schemas/{prefix}__",
            1,
        )
    return value


def merge_documents(
    documents: list[tuple[str, str, str, dict[str, Any]]],
    *,
    product_id: str = "platform",
) -> dict[str, Any]:
    """Merge service OpenAPI documents and apply their public Gateway prefixes."""
    paths: dict[str, Any] = {}
    schemas: dict[str, Any] = {}
    tags: list[dict[str, str]] = []
    for service, source_prefix, public_prefix, original in documents:
        document = _rewrite_refs(deepcopy(original), service)
        for source_path, path_item in document.get("paths", {}).items():
            if source_path != source_prefix and not source_path.startswith(
                source_prefix + "/"
            ):
                continue
            public_path = public_prefix + source_path[len(source_prefix) :]
            if public_path in paths:
                raise ValueError(f"PUBLIC_API_PATH_CONFLICT: {public_path}")
            for operation in path_item.values():
                if isinstance(operation, dict) and "operationId" in operation:
                    operation["x-source-service"] = service
            paths[public_path] = path_item
        for name, schema in document.get("components", {}).get("schemas", {}).items():
            schemas[f"{service}__{name}"] = schema
        tags.extend(
            {"name": f"{service}:{tag['name']}"}
            for tag in document.get("tags", [])
            if isinstance(tag, dict) and isinstance(tag.get("name"), str)
        )
    return {
        "openapi": "3.1.0",
        "info": {
            "title": f"{product_id.replace('-', ' ').title()} Public API",
            "version": "11",
            "description": (
                "Gateway-visible contract compiled from Product v7 routes and "
                "Module v9 frozen owner documents."
            ),
        },
        "paths": {key: paths[key] for key in sorted(paths)},
        "components": {
            "schemas": {key: schemas[key] for key in sorted(schemas)}
        },
        **({"tags": tags} if tags else {}),
    }


def build_contract(
    root: Path = ROOT, product_id: str | None = None
) -> dict[str, Any]:
    import sys

    assembler_path = str(root / "platform" / "assembler")
    if assembler_path not in sys.path:
        sys.path.insert(0, assembler_path)
    from assembler_manifests import _load_modules

    product_id, manifest = _load_product(root, product_id)
    modules = _load_modules(root)
    kernel = modules["kernel"].http_contracts["kernel-http-v12"]
    documents = [
        ("kernel", kernel.base_path, "/api/v12", kernel.document),
    ]
    for route in manifest["publicRoutes"]:
        contract = modules[route["service"]].http_contracts[route["contract"]]
        namespace = route["service"].replace("-", "_")
        documents.append(
            (namespace, contract.base_path, route["prefix"], contract.document)
        )
    document = merge_documents(documents, product_id=product_id)
    _validate_public_contract(document)
    return document


def _validate_public_contract(document: dict[str, Any]) -> None:
    operation_ids: set[str] = set()
    for path, path_item in document["paths"].items():
        if not path.startswith("/api/v12"):
            raise ValueError(f"PUBLIC_API_PATH_INVALID: {path}")
        for method, operation in path_item.items():
            if method not in {"get", "post", "put", "patch", "delete"}:
                continue
            operation_id = operation.get("operationId")
            if not isinstance(operation_id, str) or operation_id in operation_ids:
                raise ValueError("PUBLIC_API_OPERATION_ID_INVALID")
            operation_ids.add(operation_id)
            for status, response in operation.get("responses", {}).items():
                if not str(status).startswith("2") or str(status) == "204":
                    continue
                contents = response.get("content", {})
                if not contents:
                    raise ValueError("PUBLIC_API_SUCCESS_CONTENT_MISSING")
                for media_type, content in contents.items():
                    schema = content.get("schema")
                    if not isinstance(schema, dict):
                        raise ValueError("PUBLIC_API_SUCCESS_SCHEMA_MISSING")
                    if media_type.endswith("json") and (
                        schema == {"type": "object"}
                        or schema == {"type": "array"}
                        or schema.get("additionalProperties") is True
                    ):
                        raise ValueError("PUBLIC_API_SUCCESS_SCHEMA_GENERIC")
            for parameter in operation.get("parameters", []):
                name = parameter.get("name")
                location = parameter.get("in")
                if location in {"path", "query"} and isinstance(name, str):
                    if "_" in name:
                        raise ValueError(f"PUBLIC_API_PARAMETER_CASE_INVALID: {name}")
                    if location == "path" and name == "id":
                        raise ValueError("PUBLIC_API_PATH_PARAMETER_INVALID: id")
    for schema in document.get("components", {}).get("schemas", {}).values():
        _validate_schema_properties(schema)


def _validate_schema_properties(value: object) -> None:
    if isinstance(value, dict):
        properties = value.get("properties")
        if isinstance(properties, dict):
            invalid = [name for name in properties if "_" in name]
            if invalid:
                raise ValueError(
                    f"PUBLIC_API_JSON_CASE_INVALID: {','.join(sorted(invalid))}"
                )
        for child in value.values():
            _validate_schema_properties(child)
    elif isinstance(value, list):
        for child in value:
            _validate_schema_properties(child)


def encoded_contract(
    root: Path = ROOT, product_id: str | None = None
) -> bytes:
    return (
        json.dumps(
            build_contract(root, product_id),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--product")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    product_id, manifest = _load_product(ROOT, args.product)
    output = args.output or _output_path(ROOT, product_id, manifest)
    content = encoded_contract(ROOT, product_id)
    if args.check:
        if not output.is_file() or output.read_bytes() != content:
            raise SystemExit(
                f"PUBLIC_API_CONTRACT_STALE: run {Path(__file__).name}"
            )
        print(f"Public API contract PASS: {output}")
        return
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(content)
    print(f"Wrote {output}")


if __name__ == "__main__":
    main()
