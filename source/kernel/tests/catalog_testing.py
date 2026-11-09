"""Frozen source catalog for isolated Kernel tests; no generated deployment data."""
from functools import lru_cache
import hashlib
import json
from pathlib import Path

from platform_workflow_compiler import validate_catalog


@lru_cache(maxsize=1)
def authoring_fixture():
    directory = Path(__file__).resolve().parents[2] / "packages/operator-pack/catalog"
    lock = json.loads((directory / "implementation-lock.json").read_text())["operators"]
    operators = []
    for path in sorted((directory / "operators").glob("*/*/manifest.json")):
        item = json.loads(path.read_text())
        item["implementationRevision"] = lock[f"{item['id']}@{item['version']}"]["implementationRevision"]
        operators.append(item)
    digest = "sha256:" + hashlib.sha256(json.dumps(operators, sort_keys=True).encode()).hexdigest()
    return {
        "operatorCatalogDigest": digest,
        "operators": operators,
        "enabledOperators": [
            f"{item['id']}@{item['version']}" for item in operators
        ],
    }, validate_catalog(operators, digest)
